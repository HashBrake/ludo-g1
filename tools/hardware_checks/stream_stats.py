#!/usr/bin/env python3
"""Read-only: stream one camera for N seconds and report fps, drops and jitter (T-010).

This is the Phase 1 read-only check of CLAUDE.md section 6 ("stream every device at target rate for
10 minutes and report drop rates and jitter") for the camera paths. It opens a camera, grabs frames
as fast as the device delivers them, and reports what the stream actually did:

* **achieved fps** -- ``(frames - 1) / span``, the rate the timestamps imply, not what the device
  claims through ``CAP_PROP_FPS``;
* **drops** -- gaps longer than 1.5 nominal periods, and the number of frames those gaps swallowed;
* **jitter** -- ``|interval - nominal period|`` at p50 and p99, in milliseconds.

Timestamps come from the driver, i.e. from ``runtime.clock.now_ns`` at the instant the frame
arrived, which is the same clock the recorder aligns streams on (docs/clock.md).

``--backend mock`` needs no hardware at all and exits 0 with nothing plugged in: it streams
``drivers.mock.MockCamera``, whose frames are a grid on the same clock, so it exercises this tool's
statistics end to end and is what the acceptance test runs. ``--backend real`` opens the device.

This script only reads. It opens a camera, never a robot, and cannot produce a motion command
(R1/R2 do not apply; no hardware session is needed).

Usage:
    .venv/bin/python tools/hardware_checks/stream_stats.py --backend mock --seconds 5
    .venv/bin/python tools/hardware_checks/stream_stats.py --backend real --camera oblique --seconds 10
    .venv/bin/python tools/hardware_checks/stream_stats.py --backend real --camera top --device /dev/video2 --json

Exit codes: 0 statistics were produced, 2 usage error, 3 no usable camera (absent, busy or silent).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

# Run from anywhere: this script is executed by a human, usually from the repo root, sometimes not
# (the same bootstrap as tools/hardware_checks/brio_still.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from runtime import clock, config  # noqa: E402

#: Exit code for "there is no camera to read", distinct from a usage error (2).
NO_CAMERA = 3

#: A gap longer than this many nominal periods counts as a drop.
DROP_FACTOR = 1.5

#: Fraction of the nominal period slept between polls of a mock camera. A mock reports the newest
#: grid point at or before now, so it has to be polled rather than waited on; sleeping a twentieth
#: of a period keeps the loop off a busy-wait without ever being able to miss a grid point.
MOCK_POLL_FRACTION = 0.05


def stats(ts_ns: list[int], expected_hz: float) -> dict[str, Any]:
    """Rate, drop and jitter statistics for one stream of arrival timestamps.

    ``ts_ns`` must be strictly increasing. Returns plain JSON-able types so that the same dict is
    what ``--json`` prints and what a test asserts on.
    """
    if expected_hz <= 0:
        raise ValueError(f"expected_hz must be positive, got {expected_hz!r}")
    n = len(ts_ns)
    if n < 2:
        raise ValueError(f"need at least 2 frames for statistics, got {n}")
    ts = np.asarray(ts_ns, dtype=np.int64)
    if np.any(np.diff(ts) <= 0):
        raise ValueError("timestamps must be strictly increasing")

    period_ns = 1e9 / expected_hz
    deltas = np.diff(ts).astype(np.float64)
    span_s = float(ts[-1] - ts[0]) / 1e9
    gaps = deltas > DROP_FACTOR * period_ns
    # A gap of k nominal periods swallowed k-1 frames; round because the boundary frame itself is
    # late, not missing.
    missed = int(np.sum(np.maximum(np.round(deltas[gaps] / period_ns) - 1.0, 1.0))) if gaps.any() else 0
    jitter_ms = np.abs(deltas - period_ns) / 1e6
    interval_ms = deltas / 1e6
    return {
        "frames": n,
        "span_s": round(span_s, 4),
        "expected_hz": expected_hz,
        "fps": round((n - 1) / span_s, 3) if span_s > 0 else float("inf"),
        "drops": int(gaps.sum()),
        "frames_missed": missed,
        "interval_ms_p50": round(float(np.percentile(interval_ms, 50)), 4),
        "interval_ms_p99": round(float(np.percentile(interval_ms, 99)), 4),
        "interval_ms_max": round(float(interval_ms.max()), 4),
        "jitter_ms_p50": round(float(np.percentile(jitter_ms, 50)), 4),
        "jitter_ms_p99": round(float(np.percentile(jitter_ms, 99)), 4),
        "jitter_ms_max": round(float(jitter_ms.max()), 4),
    }


def stream(camera: Any, seconds: float, poll_s: float = 0.0, warmup: int = 0) -> list[int]:
    """Grab from ``camera`` for ``seconds`` and return one timestamp per distinct frame.

    A real camera blocks in ``read()`` until the next frame, so every ``grab()`` is a new frame. A
    mock reports whatever the grid currently shows, so repeated timestamps are polls of the same
    frame and are dropped here; ``poll_s`` keeps that loop from spinning.

    ``warmup`` frames are grabbed and discarded first, so that auto-exposure settling and the first
    allocation do not show up as jitter.
    """
    for _ in range(warmup):
        camera.grab()
    out: list[int] = []
    deadline = clock.now_ns() + int(seconds * 1e9)
    last: int | None = None
    while clock.now_ns() < deadline:
        ts = camera.grab().ts_ns
        if last is None or ts > last:
            out.append(ts)
            last = ts
        elif poll_s:
            time.sleep(poll_s)
    return out


def _build(backend: str, camera: str, device: str | None) -> tuple[Any, float, float]:
    """``(camera, expected_hz, poll_s)`` for the chosen backend."""
    expected_hz = float(config.load("cameras")[camera]["fps"])
    if backend == "mock":
        from drivers.mock import MockCamera

        return MockCamera(camera), expected_hz, MOCK_POLL_FRACTION / expected_hz
    from drivers.cameras import V4L2Camera

    return V4L2Camera(camera, device=device), expected_hz, 0.0


def _print_human(report: dict[str, Any]) -> None:
    s = report["stats"]
    print(f"camera       {report['camera']} ({report['backend']})")
    print(f"device       {report['device']}")
    probe = report.get("probe")
    if probe:
        print(f"negotiated   {probe['width']}x{probe['height']} @ {probe['fps']:g} fps {probe['fourcc'] or '?'}")
    print(f"policy size  {report['policy_resolution'][0]}x{report['policy_resolution'][1]}")
    print(f"frames       {s['frames']} in {s['span_s']:.2f} s (warmup {report['warmup']} discarded)")
    print(f"fps          {s['fps']:.2f}  (expected {s['expected_hz']:g})")
    print(f"drops        {s['drops']} gaps > {DROP_FACTOR:g} periods, {s['frames_missed']} frames missed")
    for what in ("interval", "jitter"):
        p50, p99, top = (s[f"{what}_ms_{k}"] for k in ("p50", "p99", "max"))
        print(f"{what + ' ms':<12} p50 {p50:.2f}  p99 {p99:.2f}  max {top:.2f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/hardware_checks/stream_stats.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--backend", choices=("mock", "real"), default="mock")
    parser.add_argument("--camera", choices=("top", "oblique", "palm"), default="top")
    parser.add_argument("--seconds", type=float, default=10.0, help="how long to stream")
    parser.add_argument("--device", help="V4L2 node or index, overriding config/cameras.yaml (real only)")
    parser.add_argument("--warmup", type=int, default=0, help="frames grabbed and discarded before timing")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    if args.seconds <= 0:
        print("--seconds must be positive", file=sys.stderr)
        return 2
    if args.warmup < 0:
        print("--warmup must be >= 0", file=sys.stderr)
        return 2
    if args.device and args.backend != "real":
        print("--device only means anything with --backend real", file=sys.stderr)
        return 2

    # CameraUnavailable lives in drivers.cameras, which imports cv2; import it lazily so that the
    # mock path costs nothing, but name it before the try so both paths can catch it.
    from drivers.cameras import CameraUnavailable

    try:
        camera, expected_hz, poll_s = _build(args.backend, args.camera, args.device)
    except CameraUnavailable as exc:
        print(f"no statistics: {exc}", file=sys.stderr)
        return NO_CAMERA

    try:
        probe = camera.probe() if hasattr(camera, "probe") else None
        ts = stream(camera, args.seconds, poll_s=poll_s, warmup=args.warmup)
        if len(ts) < 2:
            print(
                f"no statistics: {args.camera} delivered {len(ts)} frame(s) in {args.seconds:g} s",
                file=sys.stderr,
            )
            return NO_CAMERA
        measured = stats(ts, expected_hz)
    except CameraUnavailable as exc:
        print(f"no statistics: {exc}", file=sys.stderr)
        return NO_CAMERA
    finally:
        if hasattr(camera, "close"):
            camera.close()

    selection = getattr(camera, "selection", None)
    # MockCamera carries its policy size as width/height; V4L2Camera as a policy_resolution pair.
    size = list(getattr(camera, "policy_resolution", (getattr(camera, "width", 0), getattr(camera, "height", 0))))
    report: dict[str, Any] = {
        "camera": args.camera,
        "backend": args.backend,
        "device": selection.describe() if selection is not None else "mock",
        "policy_resolution": size,
        "warmup": args.warmup,
        "probe": None
        if probe is None
        else {
            "width": probe.width,
            "height": probe.height,
            "fps": probe.fps,
            "fourcc": probe.fourcc,
            "card": probe.card,
        },
        "stats": measured,
    }
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
