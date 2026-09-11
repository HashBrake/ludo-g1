#!/usr/bin/env python3
"""Read-only: grab one 4K still from the Brio for board calibration (H-001, T-008).

``board/calibration.py`` needs one sharp, top-down photograph of the empty board with all four
AprilTags visible. This grabs it. It opens the V4L2 node named by ``config/cameras.yaml`` ``top``,
asks for 3840x2160 MJPG, throws away the first frames while the camera settles its exposure and
gain, and writes the next one as a PNG (lossless: a JPEG's ringing around the tag edges moves the
detected corners, and the whole point of the still is where those corners are).

This script only reads. It opens a camera, never a robot, and cannot produce a motion command
(R1/R2 do not apply; no hardware session is needed).

Autofocus is switched off after the first frames, and the achieved focus/exposure are reported: a
refocus between this still and a game invalidates the homography, so whatever the camera settles on
here is what has to stay set. Nothing is written to ``config/cameras.yaml`` -- the achieved values
are printed for a human to paste in when the Brio is mounted for good.

Usage:
    .venv/bin/python tools/hardware_checks/brio_still.py --out data/calib/board_empty.png
    .venv/bin/python tools/hardware_checks/brio_still.py --device /dev/video2 --out /tmp/board.png

Exit codes: 0 a still was written, 3 no usable camera (not configured, absent, or gave no frames).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

# Run from anywhere: this script is executed by a human, usually from the repo root, sometimes not
# (the same bootstrap as tools/hardware_checks/enable_session.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from runtime import config  # noqa: E402

#: What H-001 asks for, and the Brio's native sensor size.
DEFAULT_WIDTH = 3840
DEFAULT_HEIGHT = 2160
#: Frames discarded before the one that is kept, so that auto-exposure and auto-gain have settled.
DEFAULT_WARMUP = 10
#: Exit code for "there is no camera to read", so H-001 can tell it apart from a usage error (2).
NO_CAMERA = 3


class NoCamera(RuntimeError):
    """No usable camera: nothing configured, the node is absent, or it produced no frame."""


def resolve_device(override: str | None = None, root: Path | str | None = None) -> str:
    """The V4L2 device to open: ``--device`` if given, else ``top.device`` from the camera config.

    Raises :class:`NoCamera` while ``top.device`` is still the UNMEASURED placeholder it has been
    since T-002 -- guessing ``/dev/video0`` would silently photograph a webcam and call it the board.
    """
    if override:
        return override
    device = config.load("cameras", root)["top"]["device"]
    if device == config.UNMEASURED or not isinstance(device, str):
        raise NoCamera(
            "config/cameras.yaml top.device is still UNMEASURED, so there is no Brio node to open. "
            "Plug the Brio in and run tools/hardware_checks/list_devices.py (H-003) to find its "
            "/dev/v4l/by-id/... path, put it in the config, or pass --device explicitly."
        )
    return device


def _open(device: str, width: int, height: int, fourcc: str) -> cv2.VideoCapture:
    source: int | str = int(device) if device.isdigit() else device
    if isinstance(source, str) and not Path(source).exists():
        raise NoCamera(f"{source} does not exist; is the Brio plugged in? (tools/hardware_checks/list_devices.py)")
    cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise NoCamera(f"cannot open {device} with the V4L2 backend (in use by another process, or not a camera)")
    # Order matters: the fourcc has to be set before the resolution, or the driver caps the request
    # at whatever the raw YUYV pipe can carry (about 1080p over USB).
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def capture_still(
    out: Path,
    device: str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fourcc: str = "MJPG",
    warmup: int = DEFAULT_WARMUP,
    autofocus: bool = False,
) -> dict:
    """Write one still to ``out``. Returns what was actually achieved. Raises :class:`NoCamera`."""
    cap = _open(device, width, height, fourcc)
    try:
        for i in range(warmup):
            if not cap.read()[0]:
                raise NoCamera(f"{device} opened but gave no frame (warm-up frame {i + 1} of {warmup})")
            if i == 0 and not autofocus:
                # Let one frame through first: some UVC drivers ignore the control before streaming.
                cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise NoCamera(f"{device} gave no frame after {warmup} warm-up frames")
        achieved = {
            "device": device,
            "requested": [width, height],
            "resolution": [int(frame.shape[1]), int(frame.shape[0])],
            "fps": float(cap.get(cv2.CAP_PROP_FPS)),
            "autofocus": float(cap.get(cv2.CAP_PROP_AUTOFOCUS)),
            "focus": float(cap.get(cv2.CAP_PROP_FOCUS)),
            "exposure": float(cap.get(cv2.CAP_PROP_EXPOSURE)),
            "warmup_frames": warmup,
        }
    finally:
        cap.release()

    out.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out), frame):
        raise NoCamera(f"cannot write {out} (PNG encoding failed or the directory is not writable)")
    achieved["path"] = str(out)
    achieved["bytes"] = out.stat().st_size
    return achieved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/hardware_checks/brio_still.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", type=Path, default=Path("data/calib/board_empty.png"), help="PNG to write")
    parser.add_argument("--device", help="V4L2 node or index; default: config/cameras.yaml top.device")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--fourcc", default="MJPG")
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP, help="frames discarded before the kept one")
    parser.add_argument("--autofocus", action="store_true", help="leave autofocus on (it moves the homography)")
    args = parser.parse_args(argv)

    if args.out.suffix.lower() != ".png":
        print(f"refusing to write {args.out}: the calibration still must be lossless PNG", file=sys.stderr)
        return 2
    try:
        device = resolve_device(args.device)
        achieved = capture_still(
            out=args.out,
            device=device,
            width=args.width,
            height=args.height,
            fourcc=args.fourcc,
            warmup=args.warmup,
            autofocus=args.autofocus,
        )
    except NoCamera as exc:
        print(f"no still captured: {exc}", file=sys.stderr)
        return NO_CAMERA

    width, height = achieved["resolution"]
    print(f"device       {achieved['device']}")
    print(f"resolution   {width}x{height}  (requested {args.width}x{args.height}, fourcc {args.fourcc})")
    print(f"fps          {achieved['fps']:g}")
    print(f"focus        autofocus={achieved['autofocus']:g} focus={achieved['focus']:g}")
    print(f"exposure     {achieved['exposure']:g}")
    print(f"wrote        {achieved['path']}  ({achieved['bytes'] / 1e6:.1f} MB, after {args.warmup} warm-up frames)")
    if (width, height) != (args.width, args.height):
        print(
            f"WARNING      the camera delivered {width}x{height}, not the {args.width}x{args.height} asked for; "
            f"the tags may be too small to localise well. Check the fourcc and the USB 3 port.",
            file=sys.stderr,
        )
    print(f"next         .venv/bin/python -m board.calibration --image {achieved['path']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
