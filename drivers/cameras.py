"""Real camera streams over V4L2: ``top`` (Brio), ``oblique`` (Orbbec Ego), ``palm`` (T-010).

One class, :class:`V4L2Camera`, serves every stream in ``config/cameras.yaml`` and satisfies the
same :class:`drivers.interfaces.CameraDriver` protocol as :class:`drivers.mock.MockCamera`, so that
nothing downstream of a driver can tell them apart: :meth:`V4L2Camera.grab` returns a
:class:`runtime.clock.Stamped` frame, ``(h, w, 3)`` uint8, already cropped and resized to the
stream's ``policy_resolution``. The capture resolution never leaves this module.

**Read-only.** Opening a camera is not a motion command, so R1 does not apply and no hardware
session is needed (CLAUDE.md 4.6). There is no write call on this protocol: a camera is a sensor.

**Timestamps.** ``grab()`` stamps with :func:`runtime.clock.now_ns` at the instant ``cap.read()``
returns, i.e. as close to the arrival of the frame as this process can observe. The residual --
exposure, USB transfer, and the driver's own buffering -- is the camera path latency measured in
Phase 1 and compensated through ``runtime.clock.shift``; it is not corrected here.

**Depth is not available** (D-009): the Orbbec Ego enumerates as a plain UVC stereo pair and
``pyorbbecsdk`` is not installed, so ``oblique`` is the left RGB stream and asking this driver for
depth raises :class:`NotImplementedError` naming the missing package.

Device selection, in order, is the "device discovery" of T-010:

1. an explicit ``device=`` argument (a ``/dev/...`` path or a numeric index);
2. ``config/cameras.yaml`` ``<name>.device`` when it is not the ``UNMEASURED`` placeholder -- give
   it a ``/dev/v4l/by-id/...`` path, never ``/dev/videoN``: node numbers move when devices are
   replugged and a policy trained on ``top`` must never be fed ``oblique``;
3. discovery by ``<name>.usb_id``: the lowest-numbered VIDEO_CAPTURE node whose USB
   ``vendor:product`` matches, reported through its ``/dev/v4l/by-id/`` link when udev made one.
   This can only ever match the device the config already declares, so it cannot silently open the
   wrong camera; it exists so that the streams work before anyone has pinned a by-id path.

Anything that means "there is no camera to read" -- unconfigured, absent, busy, or silent -- is a
:class:`CameraUnavailable`, so that a read-only check can skip rather than fail (T-010 acceptance).
"""

from __future__ import annotations

import ctypes
import fcntl
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from runtime import clock, config
from runtime.clock import Stamped

__all__ = [
    "CameraUnavailable",
    "Probe",
    "Selection",
    "V4L2Camera",
    "VideoNode",
    "find_node",
    "list_video_nodes",
    "resolve_device",
]

#: linux/videodev2.h: ``VIDIOC_QUERYCAP = _IOR('V', 0, struct v4l2_capability)``.
_VIDIOC_QUERYCAP = 0x80685600
_V4L2_CAP_VIDEO_CAPTURE = 0x00000001
_V4L2_CAP_DEVICE_CAPS = 0x80000000

_BY_ID_DIR = Path("/dev/v4l/by-id")


class CameraUnavailable(RuntimeError):
    """There is no camera to read: not configured, not present, busy, or producing no frames."""


class _V4l2Capability(ctypes.Structure):
    """``struct v4l2_capability`` (linux/videodev2.h), 104 bytes."""

    _fields_ = [
        ("driver", ctypes.c_char * 16),
        ("card", ctypes.c_char * 32),
        ("bus_info", ctypes.c_char * 32),
        ("version", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("device_caps", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    ]


@dataclass(frozen=True, slots=True)
class VideoNode:
    """One ``/dev/videoN`` node as enumerated read-only from sysfs and one VIDIOC_QUERYCAP."""

    path: str
    card: str
    usb_id: str | None
    by_id: str | None
    is_capture: bool

    @property
    def selector(self) -> str:
        """The stable path to open this node by: its by-id link when udev made one."""
        return self.by_id or self.path


@dataclass(frozen=True, slots=True)
class Selection:
    """Which device a stream resolved to, and how -- what a read-only check reports and skips on."""

    device: str | int
    source: str
    node: VideoNode | None = None

    def describe(self) -> str:
        card = f" {self.node.card!r}" if self.node is not None and self.node.card else ""
        return f"{self.device}{card} (via {self.source})"


@dataclass(frozen=True, slots=True)
class Probe:
    """What the driver actually negotiated with the device, as opposed to what it asked for."""

    device: str
    width: int
    height: int
    fps: float
    fourcc: str
    card: str
    policy_resolution: tuple[int, int]


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _usb_id_for(sysfs_device: Path) -> str | None:
    """Walk up a sysfs device path to the USB node and return ``vendor:product`` lowercase."""
    node: Path | None = sysfs_device
    for _ in range(10):
        if node is None or str(node) == node.anchor:
            return None
        vendor = _read_text(node / "idVendor")
        product = _read_text(node / "idProduct")
        if vendor and product:
            return f"{vendor.lower()}:{product.lower()}"
        node = node.parent
    return None


def _by_id_links() -> dict[str, str]:
    """``{/dev/videoN: /dev/v4l/by-id/...}`` for every by-id link udev created."""
    out: dict[str, str] = {}
    if not _BY_ID_DIR.is_dir():
        return out
    for link in sorted(_BY_ID_DIR.iterdir()):
        try:
            target = str(link.resolve())
        except OSError:
            continue
        out.setdefault(target, str(link))
    return out


def _query_capture(path: str) -> tuple[bool, str]:
    """``(is_capture, card)`` from one VIDIOC_QUERYCAP. Opens O_RDONLY and never streams."""
    cap = _V4l2Capability()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return False, ""
    try:
        fcntl.ioctl(fd, _VIDIOC_QUERYCAP, cap)
    except OSError:
        return False, ""
    finally:
        os.close(fd)
    caps = cap.device_caps if cap.capabilities & _V4L2_CAP_DEVICE_CAPS else cap.capabilities
    return bool(caps & _V4L2_CAP_VIDEO_CAPTURE), cap.card.decode(errors="replace")


def list_video_nodes() -> list[VideoNode]:
    """Every ``/dev/video*`` node, in node-number order. Read-only: sysfs plus VIDIOC_QUERYCAP."""
    links = _by_id_links()
    nodes: list[VideoNode] = []
    for node in sorted(Path("/dev").glob("video*"), key=lambda p: (len(p.name), p.name)):
        path = str(node)
        sysfs = Path("/sys/class/video4linux") / node.name / "device"
        try:
            usb_id = _usb_id_for(sysfs.resolve())
        except OSError:
            usb_id = None
        is_capture, card = _query_capture(path)
        nodes.append(
            VideoNode(path=path, card=card, usb_id=usb_id, by_id=links.get(path), is_capture=is_capture)
        )
    return nodes


def find_node(usb_id: str) -> VideoNode | None:
    """The lowest-numbered capture node with this ``vendor:product`` id, or None.

    Lowest-numbered because a multi-interface device (the Ego's stereo pair) enumerates its
    interfaces in order, so the first capture node is the first stream -- the Ego's *left* camera,
    which is what ``oblique`` is (D-009).
    """
    wanted = usb_id.strip().lower()
    for node in list_video_nodes():
        if node.is_capture and node.usb_id == wanted:
            return node
    return None


def _spec(name: str, root: Path | str | None = None) -> dict:
    cameras = config.load("cameras", root)
    spec = cameras.get(name)
    if not isinstance(spec, dict) or "policy_resolution" not in spec:
        known = sorted(k for k, v in cameras.items() if isinstance(v, dict) and "policy_resolution" in v)
        raise KeyError(f"config/cameras.yaml has no camera named {name!r}; known cameras: {', '.join(known)}")
    return spec


def resolve_device(name: str, override: str | int | None = None, root: Path | str | None = None) -> Selection:
    """Decide which V4L2 device the stream ``name`` should open (the three-step order above).

    Raises :class:`CameraUnavailable` when nothing resolves, naming the config key that would fix
    it, so that a read-only check can print the reason and skip.
    """
    spec = _spec(name, root)
    if override is not None:
        device: str | int = int(override) if isinstance(override, str) and override.isdigit() else override
        if isinstance(device, str) and not Path(device).exists():
            raise CameraUnavailable(f"{name}: {device} does not exist (passed explicitly)")
        return Selection(device=device, source="explicit")

    configured = spec.get("device")
    if isinstance(configured, str) and configured != config.UNMEASURED:
        if configured.isdigit():
            return Selection(device=int(configured), source="config/cameras.yaml")
        if not Path(configured).exists():
            raise CameraUnavailable(
                f"{name}: config/cameras.yaml {name}.device is {configured} but that path does not exist; "
                f"is the camera plugged in? (run the read-only device inventory, list_devices.py)"
            )
        return Selection(device=configured, source="config/cameras.yaml")

    usb_id = spec.get("usb_id")
    if isinstance(usb_id, str) and usb_id != config.UNMEASURED:
        node = find_node(usb_id)
        if node is not None:
            return Selection(device=node.selector, source=f"usb_id {usb_id}", node=node)
        raise CameraUnavailable(
            f"{name}: no VIDEO_CAPTURE node with usb id {usb_id} (config/cameras.yaml {name}.usb_id); "
            f"is the camera plugged in? (run the read-only device inventory, list_devices.py)"
        )

    raise CameraUnavailable(
        f"{name}: config/cameras.yaml {name}.device is {config.UNMEASURED} and {name}.usb_id gives nothing "
        f"to discover with; run the read-only device inventory (list_devices.py) and put the /dev/v4l/by-id/... path "
        f"in the config, or pass a device explicitly"
    )


def _fourcc_name(value: float) -> str:
    """Decode the packed fourcc ``cv2.CAP_PROP_FOURCC`` reports back."""
    code = int(value)
    if code <= 0:
        return ""
    return "".join(chr((code >> shift) & 0xFF) for shift in (0, 8, 16, 24)).strip("\x00 ")


class V4L2Camera:
    """One real camera stream, named as in ``config/cameras.yaml`` (``top``/``oblique``/``palm``).

    The device is opened in the constructor, so a missing or busy camera fails immediately with
    :class:`CameraUnavailable` rather than at the first :meth:`grab`. Close it with :meth:`close`
    or use it as a context manager.
    """

    def __init__(
        self,
        name: str,
        *,
        device: str | int | None = None,
        depth: bool = False,
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        if depth:
            raise NotImplementedError(
                f"{name}: depth is not available. The Orbbec Ego enumerates as a UVC stereo pair and the "
                f"'pyorbbecsdk' package is not installed (D-009, docs/sdks.md 8.2); this driver delivers RGB "
                f"over V4L2 only."
            )
        spec = _spec(name, config_root)
        self.name = name
        self.now_ns: Callable[[], int] = clock.now_ns if now_ns is None else now_ns

        width, height = (int(v) for v in spec["resolution"])
        self.requested = (width, height)
        self.fps = float(spec["fps"])
        defaults = config.load("cameras", config_root).get("defaults", {})
        self.fourcc = str(spec.get("fourcc") or defaults.get("fourcc") or "MJPG")
        pw, ph = (int(v) for v in spec["policy_resolution"])
        self.policy_resolution = (pw, ph)
        crop = spec.get("crop")
        self.crop: tuple[int, int, int, int] | None = (
            (int(crop["x"]), int(crop["y"]), int(crop["w"]), int(crop["h"])) if isinstance(crop, dict) else None
        )

        self.selection = resolve_device(name, device, config_root)
        self._cap = self._open(self.selection.device)

    # ----------------------------------------------------------------------------------------
    # lifecycle
    # ----------------------------------------------------------------------------------------

    def _open(self, device: str | int) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise CameraUnavailable(
                f"{self.name}: cannot open {device} with the V4L2 backend "
                f"(in use by another process, or not a capture node)"
            )
        # Order matters: the fourcc has to be set before the resolution, or the driver caps the
        # request at whatever the raw YUYV pipe can carry over USB (about 1080p).
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*self.fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.requested[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.requested[1])
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        return cap

    def close(self) -> None:
        """Release the device. Idempotent."""
        cap, self._cap = getattr(self, "_cap", None), None
        if cap is not None:
            cap.release()

    def __enter__(self) -> V4L2Camera:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown ordering
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        w, h = self.policy_resolution
        return f"V4L2Camera(name={self.name!r}, device={self.selection.device!r}, {w}x{h}, fps={self.fps:g})"

    # ----------------------------------------------------------------------------------------
    # the protocol
    # ----------------------------------------------------------------------------------------

    def grab(self) -> Stamped[np.ndarray]:
        """The next frame, ``(h, w, 3)`` uint8 at ``policy_resolution``, stamped on arrival.

        Blocks until the device delivers a frame. Raises :class:`CameraUnavailable` when it does
        not (unplugged mid-stream, or the node went silent).
        """
        if self._cap is None:
            raise CameraUnavailable(f"{self.name}: camera is closed")
        ok, frame = self._cap.read()
        ts_ns = int(self.now_ns())
        if not ok or frame is None:
            raise CameraUnavailable(f"{self.name}: {self.selection.device} gave no frame")
        return Stamped(ts_ns, self._to_policy(frame))

    def probe(self) -> Probe:
        """What the device actually negotiated: resolution, rate and pixel format.

        Read back from the open handle; it does not consume a frame. A camera that ignored the
        request reports what it settled on here, which is what Phase 1 records as measured.
        """
        if self._cap is None:
            raise CameraUnavailable(f"{self.name}: camera is closed")
        card = self.selection.node.card if self.selection.node is not None else ""
        return Probe(
            device=str(self.selection.device),
            width=int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(self._cap.get(cv2.CAP_PROP_FPS)),
            fourcc=_fourcc_name(self._cap.get(cv2.CAP_PROP_FOURCC)),
            card=card,
            policy_resolution=self.policy_resolution,
        )

    # ----------------------------------------------------------------------------------------
    # capture frame -> policy frame
    # ----------------------------------------------------------------------------------------

    def _to_policy(self, frame: np.ndarray) -> np.ndarray:
        """Crop to the configured region and resize to ``policy_resolution``, as the mock emits.

        Colour order is OpenCV's own BGR, carried through unchanged: ``board/calibration.py`` and
        every other consumer in this repo reads images with ``cv2.imread``, which is also BGR.
        """
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.ndim == 3 and frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise CameraUnavailable(f"{self.name}: unexpected frame shape {frame.shape}, want (h, w, 3)")
        if frame.dtype != np.uint8:
            raise CameraUnavailable(f"{self.name}: unexpected frame dtype {frame.dtype}, want uint8")

        if self.crop is not None:
            x, y, w, h = self.crop
            height, width = frame.shape[:2]
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
                raise ValueError(
                    f"{self.name}: config/cameras.yaml {self.name}.crop (x={x}, y={y}, w={w}, h={h}) does not "
                    f"fit the {width}x{height} frame the camera delivered"
                )
            if (x, y, w, h) != (0, 0, width, height):
                frame = frame[y : y + h, x : x + w]

        pw, ph = self.policy_resolution
        if (frame.shape[1], frame.shape[0]) != (pw, ph):
            # INTER_AREA is the correct kernel for downscaling; it is also what cv2.resize falls
            # back to being sensible about when the frame happens to be smaller.
            frame = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
        return np.ascontiguousarray(frame)
