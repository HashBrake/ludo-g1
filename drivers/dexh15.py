"""Real Paxini DexH15 (left), **read-only**: joint angles, motor counts, tactile, palm camera (T-019).

:class:`DexH15` satisfies the same :class:`drivers.interfaces.HandDriver` protocol as
:class:`drivers.mock.dexh15.MockHand` and implements the read half of it: :meth:`DexH15.read_state`
and :meth:`DexH15.palm_frame` read, :meth:`DexH15.send_pinch` raises ``NotImplementedError``.
**This module never writes to the hand.** It opens the Modbus port, binds the slave and issues query
calls only; no motor is ever powered, no control mode is chosen and no target of any kind leaves this
process. The write path -- the synergy of CLAUDE.md 5.4 behind :meth:`runtime.safety.Guard.admit` --
is T-022 (R1, R2), and a test greps this module to prove the SDK's writing verbs appear nowhere
outside the refusing stub. Reading is not a motion command: no session is needed (4.6, R1).

**Transport.** ``pxdex.dh15.DexH15Control`` over Modbus RTU on a USB serial adapter (docs/sdks.md
4.2): ``openModbusDevice(port, baud)`` then ``initModbusDevice(slave_address)``, both of which only
bind the link. Port, baud, slave address, USB id and the 15 joint names come from
``config/hand.yaml``; nothing here is a constant (section 7). The SDK is imported lazily, inside
:func:`default_control`, so importing this module costs nothing and opens no device.

**One read is one round trip.** :meth:`read_state` performs a single synchronous
``getJointPositionsAngle``, which is why the read-only stream check (``stream_stats.py --stream hand``)
measures the achieved rate of back-to-back reads and not a queue's arrival rate: unlike the G1's DDS
stream there is no stream, only a bus the driver interrogates. The extra telemetry of CLAUDE.md 5.6
costs two further round trips and lives in :meth:`full_state`. Angles arrive *normalised* and
``HandState.joints_rad`` is real radians, so the driver converts with ``calculateRealAngle`` on the
connected slave, which is a conversion that cannot be done off-device (docs/sdks.md 4.6).

**The pinch scalar is not measurable yet.** It is the projection of the measured joints onto the
synergy line of CLAUDE.md 5.4, and ``config/hand.yaml`` ``pinch.open_pose`` / ``pinch.closed_pose``
are the literal ``UNMEASURED`` until the Phase 1 bench test (T-020). While they are,
``HandState.pinch`` is ``nan`` -- not 0.0, which would read as "the hand is open" -- and
:attr:`DexH15.pinch_measurable` is False.

Anything that means "there is no hand to read" -- unconfigured port, adapter absent, slave not
answering, driver closed -- is a :class:`HandUnavailable`, so a read-only check can skip rather than
fail, exactly as :class:`drivers.cameras.CameraUnavailable` does for a camera. The palm camera is an
ordinary V4L2 node behind ``pxdex.dh15.DexH15Camera`` (docs/sdks.md 5.2): it raises
``CameraUnavailable``, needs no Modbus link, and is opened on the first :meth:`DexH15.palm_frame`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from drivers.interfaces import HandState
from runtime import clock, config
from runtime.clock import Stamped

__all__ = [
    "DexH15",
    "HandProbe",
    "HandTelemetry",
    "HandUnavailable",
    "PalmCamera",
    "default_camera",
    "default_control",
    "find_port",
    "resolve_port",
]

#: Serial node patterns the DexH15 Modbus adapter can appear as (docs/sdks.md 4.2, H-003).
SERIAL_GLOBS: tuple[str, ...] = ("ttyUSB*", "ttyACM*")


class HandUnavailable(RuntimeError):
    """There is no hand to read: not configured, not present, not answering, or closed."""


@dataclass(frozen=True, slots=True)
class HandTelemetry:
    """One full interrogation of the hand, for the dataset (CLAUDE.md 5.6).

    ``joints_norm`` is the SDK's normalised angle and ``joints_rad`` the same pose in real radians;
    ``motor_counts`` is the raw integer position of each motor of ``config/hand.yaml``
    ``motor_order``; ``force_xyz`` is one resultant force vector per tactile finger in the SDK's
    0.1 N units, ``(fingers, 3)`` (docs/sdks.md 4.6).
    """

    joints_norm: np.ndarray
    joints_rad: np.ndarray
    motor_counts: np.ndarray
    force_xyz: np.ndarray


@dataclass(frozen=True, slots=True)
class HandProbe:
    """What the hand actually is, as opposed to what ``config/hand.yaml`` claims."""

    port: str
    baud: int
    slave_address: int
    connected: bool
    joints: int
    sdk_version: str
    serial_number: str
    hardware_version: str
    firmware_version: str
    pinch_measurable: bool


# ------------------------------------------------------------------------------------------------
# device discovery: the same three-step order as drivers/cameras.py, on serial nodes
# ------------------------------------------------------------------------------------------------


def _usb_id_for(node: Path) -> str | None:
    """``vendor:product`` of the USB device a ``/dev/tty*`` node hangs off, from sysfs. Read-only."""
    try:
        sysfs: Path | None = (Path("/sys/class/tty") / node.name / "device").resolve()
    except OSError:
        return None
    for _ in range(10):
        if sysfs is None or str(sysfs) == sysfs.anchor:
            return None
        try:
            vendor = (sysfs / "idVendor").read_text().strip()
            return f"{vendor.lower()}:{(sysfs / 'idProduct').read_text().strip().lower()}"
        except OSError:
            sysfs = sysfs.parent
    return None


def find_port(usb_id: str) -> str | None:
    """The lowest-numbered serial node with this USB ``vendor:product`` id, by-id link preferred."""
    wanted = usb_id.strip().lower()
    nodes: list[Path] = []
    for pattern in SERIAL_GLOBS:
        nodes.extend(Path("/dev").glob(pattern))
    by_id: dict[str, str] = {}
    links = Path("/dev/serial/by-id")
    if links.is_dir():
        for link in sorted(links.iterdir()):
            try:
                by_id.setdefault(str(link.resolve()), str(link))
            except OSError:
                continue
    for node in sorted(nodes, key=lambda p: (len(p.name), p.name)):
        if _usb_id_for(node) == wanted:
            return by_id.get(str(node), str(node))
    return None


def resolve_port(override: str | None = None, root: Path | str | None = None) -> str:
    """Decide which serial node the hand is on: explicit, then config, then USB-id discovery.

    Raises :class:`HandUnavailable` naming the config key and the node patterns searched, so that a
    read-only check can print the reason and skip.
    """
    device = config.load("hand", root)["device"]
    if override is not None:
        if not Path(override).exists():
            raise HandUnavailable(f"{override} does not exist (passed explicitly)")
        return override

    configured = device.get("port")
    if isinstance(configured, str) and configured != config.UNMEASURED:
        if not Path(configured).exists():
            raise HandUnavailable(
                f"config/hand.yaml device.port is {configured} but that path does not exist; is the "
                f"DexH15 Modbus adapter plugged in? (read-only inventory: list_devices.py)"
            )
        return configured

    usb_id = device.get("usb_id")
    patterns = ", ".join(f"/dev/{p}" for p in SERIAL_GLOBS)
    if isinstance(usb_id, str) and usb_id != config.UNMEASURED:
        found = find_port(usb_id)
        if found is not None:
            return found
        raise HandUnavailable(
            f"config/hand.yaml device.port is {config.UNMEASURED} and no {patterns} node has usb id "
            f"{usb_id} (config/hand.yaml device.usb_id); plug the DexH15 Modbus adapter in "
            f"(agents/HARDWARE_NEEDED.md H-003) and run the read-only inventory, list_devices.py"
        )
    raise HandUnavailable(
        f"config/hand.yaml device.port is {config.UNMEASURED} and device.usb_id gives nothing to "
        f"discover {patterns} with; put the /dev/serial/by-id/... path there (H-003)"
    )


def default_control() -> Any:
    """A real ``pxdex.dh15.DexH15Control``. The SDK is imported here, never at module import."""
    from pxdex.dh15 import DexH15Control

    return DexH15Control()


def default_camera() -> Any:
    """A real ``pxdex.dh15.DexH15Camera``. The SDK is imported here, never at module import."""
    from pxdex.dh15 import DexH15Camera

    return DexH15Camera()


def _frozen(array: np.ndarray) -> np.ndarray:
    """A contiguous, read-only view: a sample belongs to the sample, not to the caller."""
    out = np.ascontiguousarray(array)
    out.flags.writeable = False
    return out


class PalmCamera:
    """The DexH15's palm camera through ``DexH15Camera``, as a :class:`drivers.interfaces.CameraDriver`.

    Frames come out ``(h, w, 3)`` uint8 at ``config/cameras.yaml`` ``palm.policy_resolution``,
    exactly as :class:`drivers.mock.MockCamera` and :class:`drivers.cameras.V4L2Camera` emit them,
    stamped with :func:`runtime.clock.now_ns` at the instant ``getFrame`` returns. The node is found
    by the same discovery as every other camera (``drivers.cameras.resolve_device``), so an absent or
    unconfigured palm camera raises ``CameraUnavailable`` naming ``palm.device``.
    """

    def __init__(
        self,
        *,
        device: str | int | None = None,
        camera_factory: Callable[[], Any] | None = None,
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        from drivers.cameras import CameraUnavailable, resolve_device  # lazy: it pulls in cv2

        spec = config.load("cameras", config_root)["palm"]
        self.name = "palm"
        self.now_ns: Callable[[], int] = clock.now_ns if now_ns is None else now_ns
        self.requested = tuple(int(v) for v in spec["resolution"])
        self.fps = float(spec["fps"])
        self.policy_resolution = tuple(int(v) for v in spec["policy_resolution"])
        self.selection = resolve_device("palm", device, config_root)
        node = self.selection.device
        self.device = node if isinstance(node, str) else f"/dev/video{int(node)}"

        self._camera = (camera_factory or default_camera)()
        try:
            self._camera.connectCameraDevice(self.device)
            ok = self._camera.setCameraConfig(self.requested[0], self.requested[1], self.fps)
        except Exception as exc:  # the SDK raises RuntimeError for a node it cannot use
            raise CameraUnavailable(f"palm: {self.device}: {exc}") from exc
        if not ok:
            raise CameraUnavailable(
                f"palm: {self.device} refused {self.requested[0]}x{self.requested[1]} @ {self.fps:g} fps "
                f"(config/cameras.yaml palm.resolution, palm.fps)"
            )

    def __repr__(self) -> str:
        w, h = self.policy_resolution
        return f"PalmCamera(device={self.device!r}, {w}x{h}, fps={self.fps:g})"

    def close(self) -> None:
        """Release the camera. Idempotent."""
        camera, self._camera = getattr(self, "_camera", None), None
        if camera is not None:
            camera.releaseCameraDevice()

    def __enter__(self) -> PalmCamera:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def grab(self) -> Stamped[np.ndarray]:
        """The next palm frame at ``policy_resolution``, stamped when ``getFrame`` returned."""
        import cv2

        from drivers.cameras import CameraUnavailable

        if self._camera is None:
            raise CameraUnavailable("palm: camera is closed")
        try:
            frame = np.asarray(self._camera.getFrame())
        except Exception as exc:
            raise CameraUnavailable(f"palm: {self.device}: {exc}") from exc
        ts_ns = int(self.now_ns())
        if frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise CameraUnavailable(f"palm: {self.device} gave {frame.shape} {frame.dtype}, want (h, w, 3) uint8")
        pw, ph = self.policy_resolution
        if (frame.shape[1], frame.shape[0]) != (pw, ph):
            frame = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
        return Stamped(ts_ns, np.ascontiguousarray(frame))


class DexH15:
    """The DexH15's state. Read-only; there is no path from here to an actuator (T-019).

    ``control_factory()`` builds the object this driver queries; it must offer the read half of
    ``pxdex.dh15.DexH15Control``. The default builds the real one; a test injects a fake.
    """

    def __init__(
        self,
        *,
        control_factory: Callable[[], Any] | None = None,
        camera_factory: Callable[[], Any] | None = None,
        port: str | None = None,
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        hand = config.load("hand", config_root)
        self.now_ns: Callable[[], int] = clock.now_ns if now_ns is None else now_ns
        self.joint_names: tuple[str, ...] = tuple(str(n) for n in hand["joint_order"])
        self.motor_names: tuple[str, ...] = tuple(str(n) for n in hand["motor_order"])
        self.baud = int(hand["device"]["baud"])
        self.slave_address = int(hand["device"]["slave_address"])
        self.expected_hz = float(hand["device"]["command_hz"])
        self._range = tuple(float(v) for v in hand["pinch"]["scalar_range"])
        self._synergy = _synergy_poses(hand, len(self.joint_names))
        self._config_root = config_root
        self._camera_factory = camera_factory
        self._palm: PalmCamera | None = None

        self.port = resolve_port(port, config_root)
        self._control: Any | None = (control_factory or default_control)()
        if not self._bus("openModbusDevice", self.port, self.baud):
            self._control = None
            raise HandUnavailable(
                f"cannot open the Modbus port {self.port} at {self.baud} baud; is the adapter plugged in "
                f"and readable (group dialout)? (agents/HARDWARE_NEEDED.md H-003)"
            )
        if self._bus("initModbusDevice", self.slave_address) != 1:
            self.close()
            raise HandUnavailable(
                f"{self.port}: slave 0x{self.slave_address:02x} did not answer (config/hand.yaml "
                f"device.slave_address); is the hand powered and wired to this adapter?"
            )

    def __repr__(self) -> str:
        return f"DexH15(port={self.port!r}, slave=0x{self.slave_address:02x}, joints={len(self.joint_names)})"

    @property
    def pinch_measurable(self) -> bool:
        """Whether the synergy poses of ``config/hand.yaml`` exist yet (T-020); see the docstring."""
        return self._synergy is not None

    # -- lifecycle ---------------------------------------------------------------------------------

    def close(self) -> None:
        """Drop the Modbus link and the palm camera. Idempotent; it powers nothing down, because
        this driver never powered anything up."""
        palm, self._palm = self._palm, None
        if palm is not None:
            palm.close()
        control, self._control = self._control, None
        if control is not None:
            control.disconnectModbus()

    def __enter__(self) -> DexH15:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown ordering
        try:
            self.close()
        except Exception:
            pass

    # -- the HandDriver contract -------------------------------------------------------------------

    def read_state(self) -> Stamped[HandState]:
        """One synchronous Modbus round trip: the 15 joint angles. Read-only (R1)."""
        norm = self._joint_angles()
        ts_ns = int(self.now_ns())
        joints_rad = self._real_angles(norm)
        return Stamped(ts_ns, HandState(joints_rad=joints_rad, pinch=self.estimate_pinch(joints_rad)))

    def send_pinch(self, scalar: float) -> np.ndarray:
        """Refused: this driver writes nothing at all. The write path is T-022 (R1, R2)."""
        raise NotImplementedError(
            f"drivers/dexh15.py is read-only (T-019): it never powers a motor, never chooses a control "
            f"mode and never calls setJointPositionsAngle, so there is no way to send pinch {scalar!r} "
            f"from here. The write path -- the CLAUDE.md 5.4 synergy behind runtime.safety.Guard, and the "
            f"bring-up order of docs/sdks.md 4.2 -- is T-022."
        )

    def palm_frame(self) -> Stamped[np.ndarray]:
        """The latest palm-camera frame. The camera is opened on the first call, not before."""
        if self._palm is None:
            self._palm = PalmCamera(
                camera_factory=self._camera_factory, now_ns=self.now_ns, config_root=self._config_root
            )
        return self._palm.grab()

    # -- reading -----------------------------------------------------------------------------------

    def estimate_pinch(self, joints_rad: np.ndarray) -> float:
        """Project a measured pose onto the synergy line of CLAUDE.md 5.4; ``nan`` until T-020."""
        if self._synergy is None:
            return float("nan")
        open_pose, closed_pose = self._synergy
        span = closed_pose - open_pose
        scale = float(span @ span)
        if scale == 0.0:
            return float("nan")
        s = float((np.asarray(joints_rad, dtype=np.float64) - open_pose) @ span) / scale
        return float(np.clip(s, self._range[0], self._range[1]))

    def full_state(self) -> Stamped[HandTelemetry]:
        """Joints, raw motor counts and the tactile resultant forces: three round trips (5.6)."""
        norm = self._joint_angles()
        ts_ns = int(self.now_ns())
        position = self._bus("getMotorPosition", self.slave_address)
        counts = np.array([int(getattr(position, f"motor{i + 1}_pos")) for i in range(len(self.motor_names))])
        forces = self._bus("getFingerResultantForce", self.slave_address)
        force_xyz = np.array([[p.x, p.y, p.z] for p in forces], dtype=np.float64).reshape(-1, 3)
        telemetry = HandTelemetry(
            joints_norm=_frozen(norm),
            joints_rad=self._real_angles(norm),
            motor_counts=_frozen(counts),
            force_xyz=_frozen(force_xyz),
        )
        return Stamped(ts_ns, telemetry)

    def probe(self) -> HandProbe:
        """What is on the other end of the bus: SN, versions and the link it answered on."""
        connected = self._bus("isModbusDeviceConnected", self.slave_address)
        _ok, serial_number, hardware, firmware, _modbus = self._bus("getDeviceInfo", self.slave_address)
        return HandProbe(
            port=self.port,
            baud=self.baud,
            slave_address=self.slave_address,
            connected=bool(connected),
            joints=len(self._joint_angles()),
            sdk_version=str(self._bus("getSDKVersion")),
            serial_number=str(serial_number),
            hardware_version=str(getattr(hardware, "mainboard_hardware_version", "")),
            firmware_version=str(getattr(firmware, "unified_version", "")),
            pinch_measurable=self.pinch_measurable,
        )

    # -- the bus -----------------------------------------------------------------------------------

    def _bus(self, what: str, *args: Any) -> Any:
        """Run one SDK query by name, turning anything the SDK throws into a :class:`HandUnavailable`.

        By name rather than by bound method so that a closed driver -- whose control object is gone
        -- is a :class:`HandUnavailable` and not an ``AttributeError`` on ``None``.
        """
        control = self._control
        if control is None:
            raise HandUnavailable(f"{getattr(self, 'port', '?')}: driver is closed")
        try:
            return getattr(control, what)(*args)
        except Exception as exc:
            raise HandUnavailable(f"{self.port}: {what} failed: {exc}") from exc

    def _joint_angles(self) -> np.ndarray:
        """The normalised joint angles of one round trip, length-checked against ``joint_order``."""
        ret, values = self._bus("getJointPositionsAngle", self.slave_address)
        angles = np.array(values, dtype=np.float64).reshape(-1)
        if angles.size != len(self.joint_names):
            raise HandUnavailable(
                f"{self.port}: getJointPositionsAngle returned {angles.size} angles (ret {ret}), but "
                f"config/hand.yaml joint_order names {len(self.joint_names)} joints"
            )
        return angles

    def _real_angles(self, normalised: np.ndarray) -> np.ndarray:
        """Normalised angle -> real radians, on the connected slave (docs/sdks.md 4.6)."""
        ret, values = self._bus("calculateRealAngle", self.slave_address, list(normalised))
        angles = np.array(values, dtype=np.float64).reshape(-1)
        if angles.size != normalised.size:
            raise HandUnavailable(
                f"{self.port}: calculateRealAngle returned {angles.size} of {normalised.size} angles "
                f"(ret {ret}); the hand's hardware version may be unknown to the SDK"
            )
        return _frozen(angles)


def _synergy_poses(hand: dict, joints: int) -> tuple[np.ndarray, np.ndarray] | None:
    """``(open_pose, closed_pose)`` from ``config/hand.yaml``, or None while they are UNMEASURED."""
    poses = []
    for key in ("open_pose", "closed_pose"):
        value = hand["pinch"][key]
        if not isinstance(value, list):
            return None
        pose = np.array(value, dtype=np.float64).reshape(-1)
        if pose.size != joints:
            raise config.ConfigError(
                f"config/hand.yaml: pinch.{key} holds {pose.size} values, joint_order holds {joints}"
            )
        poses.append(pose)
    return poses[0], poses[1]
