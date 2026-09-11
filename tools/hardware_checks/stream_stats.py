#!/usr/bin/env python3
"""Read-only: stream one device for N seconds and report rate, drops and jitter (T-010, T-018, T-019).

This is the Phase 1 read-only check of CLAUDE.md section 6 ("stream every device at target rate for
10 minutes and report drop rates and jitter"). ``--stream`` picks the device: one of the three
camera streams of ``config/cameras.yaml`` (``top``, ``oblique``, ``palm``), ``arm`` for the G1's
``rt/lowstate`` state stream, or ``hand`` for the DexH15's joint angles. It takes samples as fast as
the device delivers them and reports what the stream actually did:

* **achieved rate** -- ``(samples - 1) / span``, the rate the timestamps imply, not what the device
  claims through ``CAP_PROP_FPS`` or what ``config/robot.yaml`` ``control.state_hz`` says;
* **drops** -- gaps longer than 1.5 nominal periods, and the number of frames those gaps swallowed;
* **jitter** -- ``|interval - nominal period|`` at p50 and p99, in milliseconds.

Timestamps come from the driver, i.e. from ``runtime.clock.now_ns`` at the instant the frame, the
``LowState_`` message or the Modbus reply arrived, which is the same clock the recorder aligns
streams on (docs/clock.md). Cameras are polled with ``grab()`` and the hand with ``read_state()``,
both de-duplicated by timestamp; the arm is drained with ``poll()``, which hands over every message
its subscriber callback stamped, so a slow poll loop cannot invent a drop that the stream did not
have. The hand has no queue to drain: one ``read_state()`` is one synchronous Modbus round trip, so
what is measured there is the achieved rate of back-to-back reads.

``--backend mock`` needs no hardware at all and exits 0 with nothing plugged in: it streams
``drivers.mock.MockCamera``, ``drivers.mock.MockArm`` or ``drivers.mock.MockHand``, whose samples are
a grid on the same clock, so it exercises this tool's statistics end to end and is what the
acceptance tests run. ``--backend real`` opens the device.

This script only reads: a camera is a sensor, the arm driver has no writer at all (T-018) and the
hand driver has none either (T-019), so no motion command can be produced from here and no hardware
session is needed (R1, R2).

Usage:
    .venv/bin/python tools/hardware_checks/stream_stats.py --backend mock --seconds 5
    .venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream oblique --seconds 10
    .venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream top --device /dev/video2 --json
    .venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream arm --seconds 600
    .venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream hand --seconds 600

Exit codes: 0 statistics were produced, 2 usage error, 3 no usable stream (absent, busy or silent).
"""

from __future__ import annotations

import argparse
import dataclasses
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

#: Exit code for "there is no stream to read", distinct from a usage error (2).
NO_STREAM = 3
#: The name this exit code had when cameras were the only stream (T-010); kept for callers.
NO_CAMERA = NO_STREAM

#: Streams this tool can read: the camera names of config/cameras.yaml, the G1 state stream and the
#: DexH15's joint angles.
CAMERAS: tuple[str, ...] = ("top", "oblique", "palm")
#: Streams that are not cameras, i.e. that carry no frame and no policy resolution.
NOT_CAMERAS: tuple[str, ...] = ("arm", "hand")
STREAMS: tuple[str, ...] = (*CAMERAS, *NOT_CAMERAS)

#: A gap longer than this many nominal periods counts as a drop.
DROP_FACTOR = 1.5

#: Fraction of the nominal period slept between polls of a mock camera. A mock reports the newest
#: grid point at or before now, so it has to be polled rather than waited on; sleeping a twentieth
#: of a period keeps the loop off a busy-wait without ever being able to miss a grid point.
MOCK_POLL_FRACTION = 0.05

#: Fraction of the nominal period slept between drains of a state stream. Larger than the camera
#: fraction because `poll()` drains a backlog rather than reporting one sample: sleeping cannot lose
#: a message, it only delays its collection, and the backlog holds thousands.
DRAIN_POLL_FRACTION = 0.25


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


def stream(source: Any, seconds: float, poll_s: float = 0.0, warmup: int = 0) -> list[int]:
    """Take samples from ``source`` for ``seconds`` and return one timestamp per distinct sample.

    One acquisition is one call: ``grab()`` on a camera, ``read_state()`` on the hand -- both return
    a :class:`runtime.clock.Stamped` and both block until the device has answered, so every call to
    a real device yields a new sample. A mock reports whatever its grid currently shows, so repeated
    timestamps are polls of the same sample and are dropped here; ``poll_s`` keeps that loop from
    spinning.

    ``warmup`` samples are taken and discarded first, so that auto-exposure settling and the first
    allocation do not show up as jitter.
    """
    take = getattr(source, "grab", None) or source.read_state
    for _ in range(warmup):
        take()
    out: list[int] = []
    deadline = clock.now_ns() + int(seconds * 1e9)
    last: int | None = None
    while clock.now_ns() < deadline:
        ts = take().ts_ns
        if last is None or ts > last:
            out.append(ts)
            last = ts
        elif poll_s:
            time.sleep(poll_s)
    return out


def drain(source: Any, seconds: float, poll_s: float, warmup: int = 0) -> list[int]:
    """Drain ``source.poll()`` for ``seconds`` and return the arrival timestamp of every sample.

    The arm driver stamps each ``LowState_`` in its subscriber callback and queues it, so draining
    the queue reports when the messages *arrived*, not when this loop got round to asking. ``warmup``
    samples are drained and discarded first, so that the DDS match and the first allocation do not
    show up as jitter.
    """
    seen = 0
    while seen < warmup:
        seen += len(source.poll())
        if poll_s:
            time.sleep(poll_s)
    out: list[int] = []
    deadline = clock.now_ns() + int(seconds * 1e9)
    while clock.now_ns() < deadline:
        out.extend(sample.ts_ns for sample in source.poll())
        if poll_s:
            time.sleep(poll_s)
    return out


def _build(backend: str, name: str, device: str | None) -> tuple[Any, float, float]:
    """``(driver, expected_hz, poll_s)`` for the chosen stream and backend."""
    if name == "arm":
        robot = config.load("robot")
        if backend == "mock":
            from drivers.mock import MockArm

            expected_hz = float(robot["mock"]["state_hz"])
            return MockArm(), expected_hz, DRAIN_POLL_FRACTION / expected_hz
        from drivers.g1_arm import G1Arm

        expected_hz = float(robot["control"]["state_hz"])
        return G1Arm(), expected_hz, DRAIN_POLL_FRACTION / expected_hz

    if name == "hand":
        # No queue to drain: read_state() is one synchronous Modbus round trip (docs/drivers.md).
        expected_hz = float(config.load("hand")["device"]["command_hz"])
        if backend == "mock":
            from drivers.mock import MockHand

            return MockHand(), expected_hz, MOCK_POLL_FRACTION / expected_hz
        from drivers.dexh15 import DexH15

        return DexH15(), expected_hz, 0.0

    expected_hz = float(config.load("cameras")[name]["fps"])
    if backend == "mock":
        from drivers.mock import MockCamera

        return MockCamera(name), expected_hz, MOCK_POLL_FRACTION / expected_hz
    if name == "palm":
        # The palm camera is the hand's, and is opened through the Paxini SDK (T-019).
        from drivers.dexh15 import PalmCamera

        return PalmCamera(device=device), expected_hz, 0.0
    from drivers.cameras import V4L2Camera

    return V4L2Camera(name, device=device), expected_hz, 0.0


def _print_human(report: dict[str, Any]) -> None:
    s = report["stats"]
    print(f"stream       {report['stream']} ({report['backend']})")
    print(f"device       {report['device']}")
    probe = report.get("probe") or {}
    if "width" in probe:
        print(f"negotiated   {probe['width']}x{probe['height']} @ {probe['fps']:g} fps {probe['fourcc'] or '?'}")
    if "mode_machine" in probe:
        print(f"robot        mode_machine {probe['mode_machine']}, mode_pr {probe['mode_pr']}, tick {probe['tick']}")
        print(f"probe rate   {probe['state_hz']:.1f} Hz over {probe['samples']} samples in {probe['window_s']:g} s")
    if "slave_address" in probe:
        print(
            f"hand         slave 0x{probe['slave_address']:02x} at {probe['baud']} baud, "
            f"{probe['joints']} joints, connected {probe['connected']}"
        )
        print(
            f"versions     sn {probe['serial_number'] or '?'}, hardware {probe['hardware_version'] or '?'}, "
            f"firmware {probe['firmware_version'] or '?'}, sdk {probe['sdk_version'] or '?'}"
        )
        measurable = "measurable" if probe["pinch_measurable"] else "nan (config/hand.yaml pinch.* UNMEASURED)"
        print(f"pinch        {measurable}")
    if report.get("policy_resolution"):
        print(f"policy size  {report['policy_resolution'][0]}x{report['policy_resolution'][1]}")
    print(f"samples      {s['frames']} in {s['span_s']:.2f} s (warmup {report['warmup']} discarded)")
    print(f"rate         {s['fps']:.2f} Hz  (expected {s['expected_hz']:g})")
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
    parser.add_argument("--stream", choices=STREAMS, help=f"what to read: {', '.join(STREAMS)} (default top)")
    parser.add_argument("--camera", choices=CAMERAS, help="older spelling of --stream for a camera")
    parser.add_argument("--seconds", type=float, default=10.0, help="how long to stream")
    parser.add_argument("--device", help="V4L2 node or index, overriding config/cameras.yaml (real cameras only)")
    parser.add_argument("--warmup", type=int, default=0, help="samples taken and discarded before timing")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    if args.stream and args.camera and args.stream != args.camera:
        print(f"--stream {args.stream} and --camera {args.camera} disagree; pass one", file=sys.stderr)
        return 2
    name = args.stream or args.camera or "top"
    if args.seconds <= 0:
        print("--seconds must be positive", file=sys.stderr)
        return 2
    if args.warmup < 0:
        print("--warmup must be >= 0", file=sys.stderr)
        return 2
    if args.device and (args.backend != "real" or name in NOT_CAMERAS):
        print("--device only means anything for a real camera", file=sys.stderr)
        return 2

    # Both live in modules with heavy imports (cv2, the DDS idl), so they are imported lazily; name
    # them before the try so that every path below can catch them.
    from drivers.cameras import CameraUnavailable
    from drivers.dexh15 import HandUnavailable
    from drivers.g1_arm import ArmUnavailable

    unavailable = (CameraUnavailable, ArmUnavailable, HandUnavailable)
    try:
        source, expected_hz, poll_s = _build(args.backend, name, args.device)
    except unavailable as exc:
        print(f"no statistics: {exc}", file=sys.stderr)
        return NO_STREAM

    try:
        probe = source.probe() if hasattr(source, "probe") else None
        if name == "arm":
            ts = drain(source, args.seconds, poll_s=poll_s, warmup=args.warmup)
        else:
            ts = stream(source, args.seconds, poll_s=poll_s, warmup=args.warmup)
        if len(ts) < 2:
            print(f"no statistics: {name} delivered {len(ts)} sample(s) in {args.seconds:g} s", file=sys.stderr)
            return NO_STREAM
        measured = stats(ts, expected_hz)
    except unavailable as exc:
        print(f"no statistics: {exc}", file=sys.stderr)
        return NO_STREAM
    finally:
        if hasattr(source, "close"):
            source.close()

    selection = getattr(source, "selection", None)
    # MockCamera carries its policy size as width/height; V4L2Camera as a policy_resolution pair.
    size = list(getattr(source, "policy_resolution", (getattr(source, "width", 0), getattr(source, "height", 0))))
    device: str | None = None
    if args.backend == "mock":
        device = "mock"
    elif name == "arm":
        device = f"{source.interface} {source.topic}"
    elif name == "hand":
        device = f"{source.port} slave 0x{source.slave_address:02x}"
    report: dict[str, Any] = {
        "stream": name,
        "backend": args.backend,
        "device": device if device is not None else selection.describe() if selection is not None else "mock",
        "policy_resolution": None if name in NOT_CAMERAS else size,
        "warmup": args.warmup,
        "probe": None if probe is None else dataclasses.asdict(probe),
        "stats": measured,
    }
    if name in CAMERAS:
        report["camera"] = name  # the key this report carried before --stream existed (T-010)
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
