"""Real G1 left arm + waist yaw, **read-only**: the ``rt/lowstate`` DDS stream (T-018).

:class:`G1Arm` satisfies the same :class:`drivers.interfaces.ArmDriver` protocol as
:class:`drivers.mock.g1_arm.MockArm`, so a recorder or a stream check cannot tell them apart, and it
implements exactly half of it: :meth:`G1Arm.read_state` reads, :meth:`G1Arm.send_targets` raises
``NotImplementedError``. **This module creates no DDS writer of any kind.** There is no publisher,
no weight ramp and no guard here; the write path is T-021 (D-007), and until it exists this driver
physically cannot move the robot (R1, R2).

Reading state is not a motion command, so no hardware session is needed (CLAUDE.md 4.6, R1).

**Transport.** ``unitree_sdk2py`` over cyclonedds on the wired LAN (docs/sdks.md 2.2, 2.5):
``ChannelFactoryInitialize(domain_id, interface)`` once per process, then a
``ChannelSubscriber(topics.state, LowState_)`` whose handler runs on the cyclonedds reader thread.
Every number comes from ``config/robot.yaml`` -- ``network.dds_interface``, ``network.dds_domain_id``,
``topics.state``, ``control.state_hz``, ``control.state_timeout_s``, ``control.motor_count`` and the
joint ``index`` of each commanded joint -- and none of it is a constant here (section 7).

**The factory is initialised once, lazily.** ``ChannelFactoryInitialize`` binds the whole process to
one domain and one interface, so it happens on the first subscriber built, never at import time, and
a second G1Arm asking for a different interface is an error rather than a silent no-op. Importing
this module pulls in no SDK at all; a test asserts that.

**Timestamps.** The handler stamps with :func:`runtime.clock.now_ns` the moment the message is
handed to it, which is what makes the state stream alignable with the cameras (docs/clock.md). The
residual (robot-side sampling, DDS transport) is the arm path latency of Phase 1.

**What a sample carries.** :meth:`read_state` gives the 8 commanded joints as a
:class:`runtime.types.RobotState` in ``action_order``; :meth:`full_state` gives every motor slot's
``q``/``dq``/``tau_est`` for the dataset (5.6). ``RobotState.pinch`` is **0.0 and means nothing
here**: the hand is a separate device on a separate SDK, and every consumer in this repo takes the
pinch from ``HandDriver.read_state`` (``runtime/controller.py``, ``teleop/recorder.py``).

Anything that means "there is no state to read" -- interface unconfigured, no message, stream gone
silent, driver closed -- is an :class:`ArmUnavailable`, so a read-only check can skip rather than
fail, exactly as :class:`drivers.cameras.CameraUnavailable` does for a camera.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from runtime import clock, config
from runtime.clock import Stamped
from runtime.types import MotionCommand, RobotState

__all__ = [
    "BACKLOG",
    "ArmProbe",
    "ArmUnavailable",
    "FullState",
    "G1Arm",
    "dds_binding",
    "default_subscriber",
]

#: Samples :meth:`G1Arm.poll` keeps when nobody polls, and arrivals :meth:`G1Arm.probe` measures
#: over. Same depth as :class:`runtime.clock.StreamBuffer`; 4096 is 8 s at the G1's nominal 500 Hz.
BACKLOG = 4096


class ArmUnavailable(RuntimeError):
    """There is no arm state to read: not configured, not arriving, gone silent, or closed."""


@dataclass(frozen=True, slots=True)
class FullState:
    """Every motor slot of one ``LowState_``, for the dataset (CLAUDE.md 5.3, 5.6).

    ``q``/``dq``/``tau_est`` hold ``control.motor_count`` values each, indexed by the 29-joint
    Unitree order that ``config/robot.yaml`` ``arm.joints[].index`` indexes into. ``mode_machine``
    is the robot's operating mode, which is what tells a human whether the arm is under the robot's
    own controller at all.
    """

    q: np.ndarray
    dq: np.ndarray
    tau_est: np.ndarray
    mode_machine: int
    mode_pr: int
    tick: int


@dataclass(frozen=True, slots=True)
class ArmProbe:
    """What the state stream is actually doing, as opposed to what ``config/robot.yaml`` claims."""

    interface: str
    domain_id: int
    topic: str
    state_hz: float
    expected_hz: float
    samples: int
    window_s: float
    mode_machine: int
    mode_pr: int
    tick: int


# ------------------------------------------------------------------------------------------------
# the DDS factory: one domain and one interface per process, bound lazily
# ------------------------------------------------------------------------------------------------

_DDS_LOCK = threading.Lock()
_DDS_BINDING: tuple[int, str] | None = None


def dds_binding() -> tuple[int, str] | None:
    """``(domain_id, interface)`` this process bound the DDS factory to, or None if never."""
    return _DDS_BINDING


def default_subscriber(topic: str, domain_id: int, interface: str) -> Any:
    """Build the real ``LowState_`` subscriber, initialising the DDS factory on first use.

    The SDK imports happen here, not at module import, so that ``import drivers.g1_arm`` costs
    nothing and no DDS participant is created by importing anything.
    """
    global _DDS_BINDING
    with _DDS_LOCK:
        if _DDS_BINDING is None:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize

            ChannelFactoryInitialize(domain_id, interface)
            _DDS_BINDING = (domain_id, interface)
        elif _DDS_BINDING != (domain_id, interface):
            bound_domain, bound_interface = _DDS_BINDING
            raise ArmUnavailable(
                f"this process already bound the DDS factory to domain {bound_domain} interface "
                f"{bound_interface!r}; it cannot also serve domain {domain_id} interface {interface!r}. "
                f"Use one interface per process (config/robot.yaml network.dds_interface)."
            )
    from unitree_sdk2py.core.channel import ChannelSubscriber
    from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_

    return ChannelSubscriber(topic, LowState_)


class G1Arm:
    """The G1's state stream. Read-only; there is no path from here to an actuator (T-018).

    ``subscriber_factory(topic, domain_id, interface)`` builds the object this driver subscribes
    with; it must offer ``Init(handler, queueLen)`` and ``Close()``, like
    ``unitree_sdk2py.core.channel.ChannelSubscriber``. The default builds the real one; a test
    injects a fake that delivers synthetic ``LowState_`` messages on a fake clock.
    """

    def __init__(
        self,
        *,
        subscriber_factory: Callable[[str, int, str], Any] | None = None,
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
        timeout_s: float | None = None,
        queue_len: int = 10,
    ) -> None:
        robot = config.load("robot", root=config_root)
        control = robot["control"]
        self.now_ns: Callable[[], int] = clock.now_ns if now_ns is None else now_ns
        self.interface = str(robot["network"]["dds_interface"])
        self.domain_id = int(robot["network"]["dds_domain_id"])
        self.topic = str(robot["topics"]["state"])
        self.expected_hz = float(control["state_hz"])
        self.timeout_s = float(control["state_timeout_s"] if timeout_s is None else timeout_s)
        self.motor_count = int(control["motor_count"])
        self._arm_index = [int(j["index"]) for j in robot["arm"]["joints"]]
        self._waist_index = int(robot["waist"]["joints"][0]["index"])

        if self.interface == config.UNMEASURED:
            raise ArmUnavailable(
                f"config/robot.yaml network.dds_interface is {config.UNMEASURED}: nothing says which "
                f"interface holds the G1 LAN ({robot['network']['laptop_ip']}/24). Bring the link up and "
                f"record the interface; agents/HARDWARE_NEEDED.md H-002 has the steps and the read-only "
                f"device inventory (list_devices.py) prints the interface with '<-- G1 LAN'."
            )
        if self.timeout_s <= 0:
            raise config.ConfigError(
                f"config/robot.yaml: control.state_timeout_s must be positive, got {self.timeout_s}"
            )

        self._lock = threading.Lock()
        self._arrived = threading.Event()
        self._latest: tuple[Stamped[RobotState], Stamped[FullState]] | None = None
        self._backlog: deque[Stamped[RobotState]] = deque(maxlen=BACKLOG)
        self._arrivals: deque[int] = deque(maxlen=BACKLOG)

        factory = default_subscriber if subscriber_factory is None else subscriber_factory
        self._sub: Any = factory(self.topic, self.domain_id, self.interface)
        self._sub.Init(self._on_lowstate, queue_len)

    def __repr__(self) -> str:
        return f"G1Arm(interface={self.interface!r}, domain={self.domain_id}, topic={self.topic!r})"

    # -- lifecycle ---------------------------------------------------------------------------------

    def close(self) -> None:
        """Close the subscriber. Idempotent."""
        sub, self._sub = self._sub, None
        if sub is not None:
            sub.Close()

    def __enter__(self) -> G1Arm:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown ordering
        try:
            self.close()
        except Exception:
            pass

    # -- the subscriber handler --------------------------------------------------------------------

    def _on_lowstate(self, msg: Any) -> None:
        """Stamp and unpack one ``LowState_``. Runs on the cyclonedds reader thread."""
        ts_ns = int(self.now_ns())
        motors = msg.motor_state[: self.motor_count]
        telemetry = np.array([(m.q, m.dq, m.tau_est) for m in motors], dtype=np.float64)
        # Contiguous and read-only, like every other array a driver hands out (drivers/interfaces.py):
        # these belong to the sample, and a consumer must not be able to edit the driver's state.
        q, dq, tau = (np.ascontiguousarray(telemetry[:, i]) for i in range(3))
        for arr in (q, dq, tau):
            arr.flags.writeable = False
        full = Stamped(
            ts_ns,
            FullState(
                q=q,
                dq=dq,
                tau_est=tau,
                mode_machine=int(msg.mode_machine),
                mode_pr=int(msg.mode_pr),
                tick=int(msg.tick),
            ),
        )
        # pinch is the hand's, not the arm's: LowState_ carries no DexH15 state (module docstring).
        state = Stamped(
            ts_ns,
            RobotState(arm=q[self._arm_index], waist_yaw=float(q[self._waist_index]), pinch=0.0, ts_ns=ts_ns),
        )
        with self._lock:
            self._latest = (state, full)
            self._backlog.append(state)
            self._arrivals.append(ts_ns)
        self._arrived.set()

    # -- the ArmDriver contract --------------------------------------------------------------------

    def read_state(self) -> Stamped[RobotState]:
        """The newest measured state, stamped when its message arrived. Read-only (R1)."""
        return self._fresh()[0]

    def send_targets(self, cmd: MotionCommand) -> MotionCommand:
        """Refused: this driver has no writer at all. The write path is T-021 (D-007)."""
        raise NotImplementedError(
            "drivers/g1_arm.py is read-only (T-018): it creates no DDS writer, so there is no way to "
            f"send {cmd!r} from here. The write path -- the command topic of config/robot.yaml "
            f"topics.command, the enable-weight ramp and runtime.safety.Guard -- is T-021 (D-007, R1)."
        )

    # -- reading -----------------------------------------------------------------------------------

    def full_state(self) -> Stamped[FullState]:
        """The newest full motor telemetry: ``q``, ``dq``, ``tau_est`` and the robot's mode."""
        return self._fresh()[1]

    def poll(self) -> list[Stamped[RobotState]]:
        """Every state sample that arrived since the previous poll, oldest first.

        This is the stream a recorder or a rate check consumes; :meth:`read_state` is the single
        latest sample. Bounded at :data:`BACKLOG` samples, so a consumer that never polls drops the
        oldest. Unlike :meth:`read_state` it never raises: an empty list means nothing arrived.
        """
        with self._lock:
            out = list(self._backlog)
            self._backlog.clear()
        return out

    def probe(self, window_s: float = 1.0) -> ArmProbe:
        """The measured state rate over the last ``window_s``, plus the robot's ``mode_machine``.

        Waits for the first message like :meth:`read_state` does, then reports the rate the arrival
        timestamps imply -- not what ``config/robot.yaml`` ``control.state_hz`` claims.
        """
        if window_s <= 0:
            raise ValueError(f"window_s must be positive, got {window_s!r}")
        _, full = self._fresh()
        horizon = int(self.now_ns()) - int(window_s * 1e9)
        with self._lock:
            recent = [ts for ts in self._arrivals if ts >= horizon]
        span_s = (recent[-1] - recent[0]) / 1e9 if len(recent) > 1 else 0.0
        return ArmProbe(
            interface=self.interface,
            domain_id=self.domain_id,
            topic=self.topic,
            state_hz=round((len(recent) - 1) / span_s, 3) if span_s > 0 else 0.0,
            expected_hz=self.expected_hz,
            samples=len(recent),
            window_s=float(window_s),
            mode_machine=full.payload.mode_machine,
            mode_pr=full.payload.mode_pr,
            tick=full.payload.tick,
        )

    def _fresh(self) -> tuple[Stamped[RobotState], Stamped[FullState]]:
        """The latest pair, once one exists and while it is not older than ``timeout_s``."""
        if self._sub is None:
            raise ArmUnavailable(f"{self.topic}: driver is closed")
        if not self._arrived.is_set() and not self._arrived.wait(self.timeout_s):
            raise ArmUnavailable(
                f"no {self.topic} message in {self.timeout_s:g} s on interface {self.interface!r} "
                f"(domain {self.domain_id}): is the robot powered and the LAN up? "
                f"(agents/HARDWARE_NEEDED.md H-002)"
            )
        with self._lock:
            latest = self._latest
        assert latest is not None  # set before _arrived, never cleared
        age_s = (int(self.now_ns()) - latest[0].ts_ns) / 1e9
        if age_s > self.timeout_s:
            raise ArmUnavailable(
                f"{self.topic} went silent on interface {self.interface!r}: last message {age_s:.2f} s ago, "
                f"older than control.state_timeout_s ({self.timeout_s:g} s)"
            )
        return latest
