"""Real Paxini PxCap Pro glove, **read-only**: 17 encoder angles in degrees (T-020).

:class:`PxCap` satisfies the same :class:`drivers.interfaces.GloveDriver` protocol as
:class:`drivers.mock.pxcap.MockGlove`. The glove is an input device -- the protocol has no write
call -- and this module goes further: it never names a single one of the SDK's *writing* verbs
(encoder zeroing, sensor zeroing, SN write, static-magnet check, firmware upgrade), all of which
change the device's persistent state. ``tests/test_pxcap.py`` greps this file to prove it. Reading is
not a motion command and no hardware session is needed (CLAUDE.md 4.6, R1); no actuator exists on
this path at all (R2).

**Transport.** ``pxcappro.PxCapPro`` over the glove's USB CDC serial node (docs/sdks.md 6.2):
``connect_device(port)``, then ``start_collection(frequency_hz, callback)``, which delivers complete
frames -- 17 raw encoder counts, 17 joint angles in degrees, tactile, and two host-side nanosecond
timestamps -- on the SDK's own thread. That is the stream; there is no polling read on the 30 Hz
path. The callback stamps with :func:`runtime.clock.now_ns` the moment it is handed the frame, and
keeps the SDK's ``timestamp_monotonic_ns`` beside it: the manual is explicit that the device's
timestamp is host frame-availability time and not device sample time
(``third_party/pxcap_pro_sdk.md:62``), so ``runtime/clock.py`` aligns on ours and the difference
between the two is glove path latency, a Phase 1 measurement.

Frames are copied out of the callback immediately, as the SDK requires (``pxcap_pro_sdk.md:104``),
and the callback stays free of locks it could block on. While a collection is running the SDK
refuses its other calls with ``4000``, so identity (SDK version, SN, firmware) is read once at
connect time, before the stream starts.

**Two routes to the binding** (Q-005), both read-only, tried in this order by :func:`load_binding`:

1. ``from pxhandsdk import pxcappro`` -- the Debian package of ``third_party/pxcap_pro_sdk.md:72``.
   **Not installed on this laptop**; untested here, and it is the route to prefer once Alois
   supplies the deb, because it needs nothing from a vendored binary tree.
2. The PyInstaller bundle's own cp310 binding, loaded **in this process**: the extension is
   ``pxcappro.cpython-310-x86_64-linux-gnu.so``, the same ABI as our 3.10 venv, and its ``RPATH``
   points at a directory the bundle does not have, so :data:`BUNDLE_LIBRARY` is preloaded with
   ``ctypes`` (``RTLD_GLOBAL``) first and the loader then resolves it. **Verified today** in this
   venv with no glove attached: the module imports, ``PxCapPro()`` constructs, ``get_sdk_version()``
   returns ``(0, '1.0.8 20260806 17:08')`` and a read without a device returns ``(106, ...)``.
   docs/drivers.md records the command. Nothing in the bundle is modified and no subprocess is
   spawned; the ``pxcap_pro_local --diagnose`` CLI is a third route this module does not take
   (docs/drivers.md says why).

**The pinch scalar is not measurable yet.** It is the thumb-to-index *tip distance* through
:func:`teleop.retarget.pinch_from_glove`, and a true distance needs the glove's own hand model. Until
``config/hand.yaml`` ``glove.pinch_distance`` is calibrated, :meth:`PxCap.tip_distance_m` and
``GloveSample.pinch`` are ``nan`` -- not 0.0, which would read as "the hand is open" -- exactly as
:attr:`drivers.dexh15.DexH15.pinch_measurable` does for the hand. The raw 17 channels are always
recorded (CLAUDE.md 5.4, 5.6).

Anything that means "there is no glove to read" -- unconfigured port, glove absent, SDK refusing,
stream gone silent, driver closed -- is a :class:`GloveUnavailable`, so a read-only check can skip
rather than fail, exactly as :class:`drivers.dexh15.HandUnavailable` does for the hand.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from drivers.dexh15 import SERIAL_GLOBS, find_port
from drivers.interfaces import GloveSample
from runtime import clock, config
from runtime.clock import Stamped

__all__ = [
    "BACKLOG",
    "BUNDLE_BINDING",
    "BUNDLE_LIBRARY",
    "GloveFrame",
    "GloveProbe",
    "GloveUnavailable",
    "PxCap",
    "default_session",
    "load_binding",
    "resolve_port",
]

#: Frames :meth:`PxCap.poll` keeps when nobody polls, and arrivals :meth:`PxCap.probe` measures over.
#: Same depth as :class:`drivers.g1_arm.BACKLOG`; 4096 is 80 s at the glove's nominal 50 Hz.
BACKLOG = 4096

_BUNDLE = (
    Path(__file__).resolve().parent.parent
    / "third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/sdk_bridge/_internal"
)
#: The bundle's cp310 ``pxcappro`` extension directory (docs/sdks.md 6.1, route (b)).
BUNDLE_BINDING = _BUNDLE / "python3/pxhandsdk/dist-packages"
#: The native library that extension is linked against, preloaded because its RPATH is wrong.
BUNDLE_LIBRARY = _BUNDLE / "libpxcappro_sdk.so.1"

#: Routes :func:`load_binding` accepts.
ROUTES: tuple[str, ...] = ("auto", "pxhandsdk", "bundle")


class GloveUnavailable(RuntimeError):
    """There is no glove to read: no binding, not configured, not present, silent, or closed."""


@dataclass(frozen=True, slots=True)
class GloveFrame:
    """One continuous-capture frame, for the dataset (CLAUDE.md 5.6).

    ``angles_deg`` is the 17 joint angles in degrees and ``encoder_counts`` the 17 raw magnetic
    encoder readings, both in ``config/hand.yaml`` ``glove.encoder_channels`` order.
    ``host_monotonic_ns`` / ``host_unix_ns`` are the SDK's own host-side stamps, kept beside our
    :func:`runtime.clock.now_ns` stamp rather than instead of it (module docstring).
    """

    angles_deg: np.ndarray
    encoder_counts: np.ndarray
    host_monotonic_ns: int
    host_unix_ns: int


@dataclass(frozen=True, slots=True)
class GloveProbe:
    """What the glove stream is actually doing, as opposed to what ``config/hand.yaml`` claims."""

    port: str
    route: str
    channels: int
    sdk_version: str
    serial_number: str
    controller_version: str
    pinch_measurable: bool
    input_hz: float
    expected_hz: float
    samples: int
    window_s: float


# ------------------------------------------------------------------------------------------------
# the binding and the serial node
# ------------------------------------------------------------------------------------------------


def load_binding(route: str = "auto") -> Any:
    """Import the ``pxcappro`` module through :data:`ROUTES`; see the module docstring."""
    if route not in ROUTES:
        raise ValueError(f"unknown route {route!r}; known routes: {', '.join(ROUTES)}")
    if route in ("auto", "pxhandsdk"):
        try:
            from pxhandsdk import pxcappro  # the deb of third_party/pxcap_pro_sdk.md:72

            return pxcappro
        except ImportError as exc:
            if route == "pxhandsdk":
                raise GloveUnavailable(f"the pxhandsdk deb is not installed here (Q-005): {exc}") from exc
    import ctypes
    import importlib
    import sys

    if not BUNDLE_LIBRARY.exists() or not BUNDLE_BINDING.is_dir():
        raise GloveUnavailable(
            f"no pxcappro binding: the pxhandsdk deb is not installed (Q-005) and the vendored bundle "
            f"is not on disk either ({BUNDLE_LIBRARY} missing). third_party payloads are git-ignored; "
            f"see docs/setup.md"
        )
    try:
        ctypes.CDLL(str(BUNDLE_LIBRARY), mode=ctypes.RTLD_GLOBAL)
        if str(BUNDLE_BINDING) not in sys.path:
            sys.path.insert(0, str(BUNDLE_BINDING))
        return importlib.import_module("pxcappro")
    except Exception as exc:  # a wrong ABI, a missing dependency of the vendored .so
        raise GloveUnavailable(f"cannot load the vendored pxcappro binding from {BUNDLE_BINDING}: {exc}") from exc


def default_session(route: str = "auto") -> Any:
    """A real ``pxcappro.PxCapPro``. The binding is loaded here, never at module import."""
    return load_binding(route).PxCapPro()


def resolve_port(override: str | None = None, root: Path | str | None = None) -> str:
    """Decide which serial node the glove is on: explicit, then config, then USB-id discovery.

    The same three-step order and the same node search as :func:`drivers.dexh15.resolve_port`, on
    ``config/hand.yaml`` ``glove.port`` / ``glove.usb_id``. Raises :class:`GloveUnavailable` naming
    the key, so a read-only check can print the reason and skip.
    """
    glove = config.load("hand", root)["glove"]
    if override is not None:
        if not Path(override).exists():
            raise GloveUnavailable(f"{override} does not exist (passed explicitly)")
        return override

    configured = glove.get("port")
    if isinstance(configured, str) and configured != config.UNMEASURED:
        if not Path(configured).exists():
            raise GloveUnavailable(
                f"config/hand.yaml glove.port is {configured} but that path does not exist; is the "
                f"PxCap Pro plugged in? (agents/HARDWARE_NEEDED.md H-004)"
            )
        return configured

    usb_id = glove.get("usb_id")
    patterns = ", ".join(f"/dev/{p}" for p in SERIAL_GLOBS)
    if isinstance(usb_id, str) and usb_id != config.UNMEASURED:
        found = find_port(usb_id)
        if found is not None:
            return found
        raise GloveUnavailable(
            f"config/hand.yaml glove.port is {config.UNMEASURED} and no {patterns} node has usb id "
            f"{usb_id} (config/hand.yaml glove.usb_id); plug the PxCap Pro in "
            f"(agents/HARDWARE_NEEDED.md H-004) and run the read-only inventory, list_devices.py"
        )
    raise GloveUnavailable(
        f"config/hand.yaml glove.port is {config.UNMEASURED} and glove.usb_id gives nothing to "
        f"discover {patterns} with; put the /dev/serial/by-id/... path there (H-004)"
    )


# ------------------------------------------------------------------------------------------------
# the driver
# ------------------------------------------------------------------------------------------------


class PxCap:
    """The PxCap Pro's encoder stream. Read-only; there is no actuator on this path at all (T-020).

    ``session_factory()`` builds the object this driver talks to; it must offer the read half of
    ``pxcappro.PxCapPro``. The default builds the real one through :func:`default_session`; a test
    injects a fake.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] | None = None,
        port: str | None = None,
        route: str = "auto",
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        hand = config.load("hand", config_root)
        glove = hand["glove"]
        self.now_ns: Callable[[], int] = clock.now_ns if now_ns is None else now_ns
        self.channels: tuple[str, ...] = tuple(str(c) for c in glove["encoder_channels"])
        self.expected_hz = float(glove["input_hz"])
        self.timeout_s = float(glove["frame_timeout_s"] if timeout_s is None else timeout_s)
        self.route = route
        self._range = tuple(float(v) for v in hand["pinch"]["scalar_range"])
        self._model = _pinch_model(glove, self.channels)
        self._config_root = config_root

        if self.timeout_s <= 0:
            raise config.ConfigError(f"config/hand.yaml: glove.frame_timeout_s must be positive, got {self.timeout_s}")
        self._lock = threading.Lock()
        self._arrived = threading.Event()
        self._latest: tuple[Stamped[GloveSample], Stamped[GloveFrame]] | None = None
        self._backlog: deque[Stamped[GloveSample]] = deque(maxlen=BACKLOG)
        self._arrivals: deque[int] = deque(maxlen=BACKLOG)
        self._fault: str | None = None

        self.port = resolve_port(port, config_root)
        self._session: Any | None = (session_factory or (lambda: default_session(route)))()
        if self._rc("connect_device", self.port) != 0:
            session, self._session = self._session, None
            raise GloveUnavailable(
                f"cannot open the glove on {self.port}: {_last_error(session)}; is the PxCap Pro "
                f"plugged in and readable (group dialout)? (agents/HARDWARE_NEEDED.md H-004)"
            )
        # Identity first: while a collection runs the SDK refuses these with 4000 (pxcap_pro_sdk.md:80).
        self.sdk_version = str(self._value("get_sdk_version", ""))
        self.serial_number = str(self._value("get_sn", ""))
        self.controller_version = str(getattr(self._value("get_firmware_versions", None), "controller_version", ""))
        if self._rc("start_collection", int(round(self.expected_hz)), self._on_frame) != 0:
            error = _last_error(self._session)
            self.close()
            raise GloveUnavailable(f"{self.port}: start_collection at {self.expected_hz:g} Hz failed: {error}")

    def __repr__(self) -> str:
        return f"PxCap(port={self.port!r}, channels={len(self.channels)}, hz={self.expected_hz:g})"

    @property
    def pinch_measurable(self) -> bool:
        """Whether ``config/hand.yaml`` ``glove.pinch_distance`` is calibrated yet (module docstring)."""
        return self._model is not None

    # -- lifecycle ---------------------------------------------------------------------------------

    def close(self) -> None:
        """Stop the collection and drop the link. Idempotent; it changes nothing on the device."""
        session, self._session = self._session, None
        if session is not None:
            try:
                session.stop_collection()
            finally:
                session.disconnect_device()

    def __enter__(self) -> PxCap:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown ordering
        try:
            self.close()
        except Exception:
            pass

    # -- the SDK's collection callback -------------------------------------------------------------

    def _on_frame(self, data: Any) -> None:
        """Stamp and copy one frame. Runs on the SDK's collection thread; it must not raise."""
        ts_ns = int(self.now_ns())
        try:
            angles = np.asarray(data.joint_angles.values, dtype=np.float64).reshape(-1)
            counts = np.asarray(data.encoder_raw.values, dtype=np.int64).reshape(-1)
            host_ns = int(data.timestamp_monotonic_ns)
            unix_ns = int(data.timestamp_unix_ns)
        except Exception as exc:  # a frame shaped unlike the manual's; surfaced by the next read
            with self._lock:
                self._fault = f"unreadable collection frame: {exc}"
            self._arrived.set()
            return
        if angles.size != len(self.channels):
            with self._lock:
                self._fault = (
                    f"a collection frame carried {angles.size} angles, but config/hand.yaml "
                    f"glove.encoder_channels names {len(self.channels)} channels"
                )
            self._arrived.set()
            return
        for array in (angles, counts):
            array.flags.writeable = False
        frame = Stamped(ts_ns, GloveFrame(angles, counts, host_ns, unix_ns))
        sample = Stamped(ts_ns, GloveSample(angles_deg=angles, pinch=self.estimate_pinch(angles)))
        with self._lock:
            self._latest = (sample, frame)
            self._backlog.append(sample)
            self._arrivals.append(ts_ns)
        self._arrived.set()

    # -- the GloveDriver contract ------------------------------------------------------------------

    def read(self) -> Stamped[GloveSample]:
        """The newest glove frame, stamped when the SDK delivered it. Read-only (R1)."""
        return self._fresh()[0]

    def full_state(self) -> Stamped[GloveFrame]:
        """The newest frame with the raw encoder counts and the SDK's own host stamps (5.6)."""
        return self._fresh()[1]

    def poll(self) -> list[Stamped[GloveSample]]:
        """Every frame that arrived since the previous poll, oldest first.

        This is the stream a recorder or a rate check consumes; :meth:`read` is the single latest
        frame. Bounded at :data:`BACKLOG`; unlike :meth:`read` it never raises, and an empty list
        means nothing arrived.
        """
        with self._lock:
            out = list(self._backlog)
            self._backlog.clear()
        return out

    # -- derived quantities ------------------------------------------------------------------------

    def tip_distance_m(self, angles_deg: np.ndarray) -> float:
        """Thumb-to-index tip distance in metres, or ``nan`` while the model is UNMEASURED."""
        if self._model is None:
            return float("nan")
        thumb, index, offset_mm, thumb_mm_deg, index_mm_deg = self._model
        angles = np.asarray(angles_deg, dtype=np.float64).reshape(-1)
        return (offset_mm + thumb_mm_deg * float(angles[thumb]) + index_mm_deg * float(angles[index])) / 1000.0

    def estimate_pinch(self, angles_deg: np.ndarray) -> float:
        """The pinch scalar of CLAUDE.md 5.4 from one frame; ``nan`` until the glove is calibrated."""
        distance = self.tip_distance_m(angles_deg)
        if not np.isfinite(distance):
            return float("nan")
        from teleop.retarget import pinch_from_glove  # lazy: drivers/ must not import teleop/ to load

        return pinch_from_glove(distance, root=self._config_root)

    def probe(self, window_s: float = 1.0) -> GloveProbe:
        """The measured frame rate over the last ``window_s``, plus the glove's identity.

        Waits for the first frame like :meth:`read` does, then reports the rate the arrival
        timestamps imply -- not what ``config/hand.yaml`` ``glove.input_hz`` claims.
        """
        if window_s <= 0:
            raise ValueError(f"window_s must be positive, got {window_s!r}")
        self._fresh()
        horizon = int(self.now_ns()) - int(window_s * 1e9)
        with self._lock:
            recent = [ts for ts in self._arrivals if ts >= horizon]
        span_s = (recent[-1] - recent[0]) / 1e9 if len(recent) > 1 else 0.0
        return GloveProbe(
            port=self.port,
            route=self.route,
            channels=len(self.channels),
            sdk_version=self.sdk_version,
            serial_number=self.serial_number,
            controller_version=self.controller_version,
            pinch_measurable=self.pinch_measurable,
            input_hz=round((len(recent) - 1) / span_s, 3) if span_s > 0 else 0.0,
            expected_hz=self.expected_hz,
            samples=len(recent),
            window_s=float(window_s),
        )

    # -- the stream ---------------------------------------------------------------------------------

    def _fresh(self) -> tuple[Stamped[GloveSample], Stamped[GloveFrame]]:
        """The latest pair, once one exists and while it is not older than ``timeout_s``."""
        if self._session is None:
            raise GloveUnavailable(f"{getattr(self, 'port', '?')}: driver is closed")
        if not self._arrived.is_set() and not self._arrived.wait(self.timeout_s):
            raise GloveUnavailable(
                f"no collection frame in {self.timeout_s:g} s from the glove on {self.port}: is it "
                f"plugged in and awake? (agents/HARDWARE_NEEDED.md H-004)"
            )
        with self._lock:
            fault, latest = self._fault, self._latest
        if fault is not None:
            raise GloveUnavailable(f"{self.port}: {fault}")
        assert latest is not None  # a frame set _arrived without a fault, and nothing clears it
        age_s = (int(self.now_ns()) - latest[0].ts_ns) / 1e9
        if age_s > self.timeout_s:
            raise GloveUnavailable(
                f"the glove on {self.port} went silent: last frame {age_s:.2f} s ago, older than "
                f"config/hand.yaml glove.frame_timeout_s ({self.timeout_s:g} s)"
            )
        return latest

    def _call(self, what: str, *args: Any) -> Any:
        """Run one SDK call by name, turning anything it throws into a :class:`GloveUnavailable`.

        By name rather than by bound method so that a closed driver -- whose session object is gone
        -- is a :class:`GloveUnavailable` and not an ``AttributeError`` on ``None``.
        """
        session = self._session
        if session is None:
            raise GloveUnavailable(f"{getattr(self, 'port', '?')}: driver is closed")
        try:
            return getattr(session, what)(*args)
        except Exception as exc:
            raise GloveUnavailable(f"{self.port}: {what} failed: {exc}") from exc

    def _rc(self, what: str, *args: Any) -> int:
        """One SDK call whose whole return is a status code (0 is success)."""
        return int(self._call(what, *args))

    def _value(self, what: str, default: Any) -> Any:
        """One SDK read returning ``(rc, payload)``; ``default`` when the glove declines to answer."""
        rc, payload = self._call(what)
        return default if int(rc) != 0 else payload


def _last_error(session: Any) -> str:
    """The SDK's most recent failure message, or a note that it could not be read."""
    try:
        _rc, message = session.get_last_error()
        return str(message)
    except Exception as exc:  # pragma: no cover - only a broken session reaches here
        return f"<get_last_error failed: {exc}>"


def _pinch_model(glove: dict, channels: tuple[str, ...]) -> tuple[int, int, float, float, float] | None:
    """``(thumb_i, index_i, offset_mm, thumb_mm_per_deg, index_mm_per_deg)``, or None while UNMEASURED."""
    spec = glove["pinch_distance"]
    coefficients = []
    for key in ("offset_mm", "thumb_mm_per_deg", "index_mm_per_deg"):
        value = spec[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        coefficients.append(float(value))
    indices = []
    for key in ("thumb_channel", "index_channel"):
        name = str(spec[key])
        if name not in channels:
            raise config.ConfigError(
                f"config/hand.yaml glove.pinch_distance.{key} is {name!r}, which is not one of "
                f"glove.encoder_channels"
            )
        indices.append(channels.index(name))
    return indices[0], indices[1], coefficients[0], coefficients[1], coefficients[2]
