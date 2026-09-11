"""Real Pico 4 controller pose, **read-only**: the left controller's 6-DoF pose (T-020).

:class:`Pico` satisfies the same :class:`drivers.interfaces.PoseDriver` protocol as
:class:`drivers.mock.pico.MockPose`. The controller is an input device: the protocol has no write
call, this module creates no writer of any kind, and nothing here can move anything (R1, R2).
Reading it needs no hardware session (CLAUDE.md 4.6).

**Transport.** ``pico_bridge.PicoBridge``, the receiving half of the headset's PicoBridge app: it
binds a TCP server on this laptop (``teleop.pico.bind_host``/``port``), the headset dials in, and
frames land in an in-process store. :meth:`Pico.read` takes
``latest_frame().controllers.left.pose`` -- position in **metres**, rotation as a unit quaternion in
**xyzw** order, in the headset's own ``pico_native`` frame -- and hands it over unchanged, exactly as
:class:`drivers.mock.pico.MockPose` does (docs/sdks.md 7.1). D-006 is why only this one field of the
frame is read: the body skeleton, the ankle trackers, GMR and the RL policy of the vendored Teleopit
stack all left the critical path, and the controller pose is the whole input.

**The frame transform is not applied here.** ``WristPose`` is documented as pico_bridge's own
conventions carried through unchanged, and :mod:`teleop.retarget` owns the map into the pelvis frame
-- ``teleop/loop.py`` already calls :func:`teleop.retarget.pico_to_g1_base` on what this driver
returns, so applying it here as well would apply it twice. :meth:`Pico.read_in_pelvis_frame` is that
same map, for a diagnostic that wants pelvis-frame numbers without a teleop loop; agents/BUILD_LOG.md
(T-020) records the disagreement with the task wording.

**Timestamps and rate.** A frame is stamped with :func:`runtime.clock.now_ns` the first time this
driver sees it, and re-reading the same frame returns the same :class:`runtime.clock.Stamped`: the
store is latest-wins, so seq is what distinguishes a new frame from a re-read, and a rate check
(``stream_stats.py --stream pose``) must not count a poll as a sample. The residual (headset
sampling, Wi-Fi, NAT, TCP) is the controller path latency of Phase 1; ``PicoFrame.timestamp_ns`` is
the headset's own clock and is not comparable with ours, so it is reported by :meth:`Pico.probe` and
never used as a timestamp.

**The headset must be able to reach this laptop.** pico_bridge's discovery broadcast does not cross
the robot's NAT, which is why the lab runs a relay on the Orin
(``third_party/g1_pico_teleop/README.md`` 3.3, 3.4). Both network paths and the steps are in
agents/HARDWARE_NEEDED.md H-004.

Anything that means "there is no pose to read" -- no frame, no controller in the frame, stream gone
silent, bridge that will not start, driver closed -- is a :class:`PoseUnavailable`, so a read-only
check can skip rather than fail, exactly as :class:`drivers.dexh15.HandUnavailable` does for the hand.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from drivers.interfaces import WristPose
from runtime import clock, config
from runtime.clock import Stamped

__all__ = ["BACKLOG", "Pico", "PoseProbe", "PoseUnavailable", "default_bridge"]

#: Arrival timestamps :meth:`Pico.probe` measures a rate over. 4096 is 34 s at the nominal 120 Hz.
BACKLOG = 4096


class PoseUnavailable(RuntimeError):
    """There is no controller pose to read: no frame, no controller, silent, or closed."""


@dataclass(frozen=True, slots=True)
class PoseProbe:
    """What the pose stream is actually doing, as opposed to what ``config/robot.yaml`` claims."""

    bind_host: str
    port: int
    connected: bool
    device_sn: str
    frames: int
    latest_seq: int
    bridge_fps: float
    headset_ts_ns: int
    pose_hz: float
    expected_hz: float
    samples: int
    window_s: float


def default_bridge(bind_host: str, port: int, discovery: bool, advertise_ip: str | None) -> Any:
    """A real, started ``pico_bridge.PicoBridge``. The SDK is imported here, never at module import."""
    from pico_bridge import PicoBridge

    return PicoBridge(host=bind_host, port=port, discovery=discovery, advertise_ip=advertise_ip)


class Pico:
    """The Pico controller's pose stream. Read-only; there is no writer here at all (T-020).

    ``bridge_factory(bind_host, port, discovery, advertise_ip)`` builds the receiver this driver
    reads; it must offer ``start()``, ``latest_frame()``, ``wait_frame(timeout)``, ``stats()`` and
    ``close()``, like ``pico_bridge.PicoBridge``. The default builds the real one; a test injects a
    fake that delivers ``pico_bridge.PicoFrame`` objects on a fake clock.
    """

    def __init__(
        self,
        *,
        bridge_factory: Callable[[str, int, bool, str | None], Any] | None = None,
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
        timeout_s: float | None = None,
        side: str = "left",
    ) -> None:
        pico = config.load("robot", root=config_root)["teleop"]["pico"]
        self.now_ns: Callable[[], int] = clock.now_ns if now_ns is None else now_ns
        self.bind_host = str(pico["bind_host"])
        self.port = int(pico["port"])
        self.discovery = bool(pico["discovery"])
        advertise = pico["advertise_ip"]
        self.advertise_ip = None if advertise in (None, config.UNMEASURED) else str(advertise)
        self.expected_hz = float(pico["input_hz"])
        self.timeout_s = float(pico["frame_timeout_s"] if timeout_s is None else timeout_s)
        self.side = side
        self._config_root = config_root

        if self.timeout_s <= 0:
            raise config.ConfigError(
                f"config/robot.yaml: teleop.pico.frame_timeout_s must be positive, got {self.timeout_s}"
            )
        if side not in ("left", "right"):
            raise ValueError(f"side must be 'left' or 'right', got {side!r}")

        self._lock = threading.Lock()
        self._stamped: tuple[int, Stamped[WristPose]] | None = None
        self._arrivals: deque[int] = deque(maxlen=BACKLOG)
        self._headset_ts_ns = 0

        factory = default_bridge if bridge_factory is None else bridge_factory
        self._bridge: Any = factory(self.bind_host, self.port, self.discovery, self.advertise_ip)
        try:
            self._bridge.start()
        except Exception as exc:
            self._bridge = None
            raise PoseUnavailable(
                f"the PicoBridge receiver could not start on {self.bind_host}:{self.port}: {exc}. Is "
                f"something else holding the port (the XRoboToolkit user unit squats 63901)? "
                f"(agents/HARDWARE_NEEDED.md H-004)"
            ) from exc

    def __repr__(self) -> str:
        return f"Pico(bind={self.bind_host}:{self.port}, side={self.side!r}, hz={self.expected_hz:g})"

    # -- lifecycle ---------------------------------------------------------------------------------

    def close(self) -> None:
        """Stop the receiver. Idempotent; it changes nothing on the headset."""
        bridge, self._bridge = self._bridge, None
        if bridge is not None:
            bridge.close()

    def __enter__(self) -> Pico:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown ordering
        try:
            self.close()
        except Exception:
            pass

    # -- the PoseDriver contract -------------------------------------------------------------------

    def read(self) -> Stamped[WristPose]:
        """The newest controller pose, in pico_bridge's own frame and units. Read-only (R1)."""
        return self._fresh()

    def read_in_pelvis_frame(self) -> Stamped[WristPose]:
        """:meth:`read` expressed in the G1 pelvis frame (the module docstring says why not by default).

        Applies ``config/robot.yaml`` ``teleop.pico_to_pelvis``, which is an UNMEASURED identity
        until Phase 1 calibrates it, so today this is a units-and-order check and not a calibration.
        """
        from teleop.retarget import pico_to_g1_base  # lazy: drivers/ must not import teleop/ to load

        stamped = self._fresh()
        position, quat = pico_to_g1_base(
            stamped.payload.position_m, stamped.payload.quat_xyzw, root=self._config_root
        )
        return Stamped(stamped.ts_ns, WristPose(position_m=position, quat_xyzw=quat))

    def probe(self, window_s: float = 1.0) -> PoseProbe:
        """The measured frame rate over the last ``window_s``, plus what the receiver reports.

        Waits for the first frame like :meth:`read` does, then reports the rate the arrival
        timestamps imply -- not what ``config/robot.yaml`` ``teleop.pico.input_hz`` claims.
        """
        if window_s <= 0:
            raise ValueError(f"window_s must be positive, got {window_s!r}")
        self._fresh()
        stats = self._bridge.stats()
        horizon = int(self.now_ns()) - int(window_s * 1e9)
        with self._lock:
            recent = [ts for ts in self._arrivals if ts >= horizon]
            headset_ts_ns = self._headset_ts_ns
        span_s = (recent[-1] - recent[0]) / 1e9 if len(recent) > 1 else 0.0
        return PoseProbe(
            bind_host=self.bind_host,
            port=self.port,
            connected=bool(stats.connected),
            device_sn=str(stats.device_sn),
            frames=int(stats.frame_count),
            latest_seq=int(stats.latest_seq),
            bridge_fps=float(stats.fps),
            headset_ts_ns=headset_ts_ns,
            pose_hz=round((len(recent) - 1) / span_s, 3) if span_s > 0 else 0.0,
            expected_hz=self.expected_hz,
            samples=len(recent),
            window_s=float(window_s),
        )

    # -- the stream ---------------------------------------------------------------------------------

    def _fresh(self) -> Stamped[WristPose]:
        """The newest frame's pose, stamped once per ``seq`` and while it is not older than the timeout."""
        if self._bridge is None:
            raise PoseUnavailable(f"{self.bind_host}:{self.port}: driver is closed")
        frame = self._bridge.latest_frame()
        if frame is None:
            try:
                frame = self._bridge.wait_frame(timeout=self.timeout_s)
            except TimeoutError as exc:
                raise PoseUnavailable(
                    f"no PICO tracking frame in {self.timeout_s:g} s on {self.bind_host}:{self.port}: is "
                    f"the headset running the PicoBridge app and on a network that reaches this laptop? "
                    f"(agents/HARDWARE_NEEDED.md H-004)"
                ) from exc
        stamped = self._stamp(frame)
        age_s = (int(self.now_ns()) - stamped.ts_ns) / 1e9
        if age_s > self.timeout_s:
            raise PoseUnavailable(
                f"the PICO stream on {self.bind_host}:{self.port} went silent: newest frame {age_s:.2f} s "
                f"old, older than config/robot.yaml teleop.pico.frame_timeout_s ({self.timeout_s:g} s)"
            )
        return stamped

    def _stamp(self, frame: Any) -> Stamped[WristPose]:
        """Stamp one frame, once: a re-read of the same ``seq`` returns the same sample."""
        seq = int(frame.seq)
        with self._lock:
            held = self._stamped
        if held is not None and held[0] == seq:
            return held[1]
        ts_ns = int(self.now_ns())
        pose = getattr(frame.controllers, self.side).pose
        if pose is None:
            raise PoseUnavailable(
                f"frame {seq} from {self.bind_host}:{self.port} carries no {self.side} controller pose: "
                f"is the controller powered on, in the glove jig and visible to the headset?"
            )
        stamped = Stamped(
            ts_ns,
            WristPose(
                position_m=np.asarray(pose.position, dtype=np.float64).reshape(-1),
                quat_xyzw=np.asarray(pose.rotation, dtype=np.float64).reshape(-1),
            ),
        )
        with self._lock:
            self._stamped = (seq, stamped)
            self._arrivals.append(ts_ns)
            self._headset_ts_ns = int(getattr(frame, "timestamp_ns", 0))
        return stamped
