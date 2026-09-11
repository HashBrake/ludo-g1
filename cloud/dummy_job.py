"""Smoke-test job for the cloud path: wait, then write one file naming the machine that ran it.

It stands in for ``policy/train.py`` until that exists, and it is the Phase 0 exit check: the same
command run against the real Greennode VM proves that up -> train -> down works end to end
(docs/cloud.md). ``--seconds`` defaults to the 60 s the exit check uses; the local-mode test runs it
with 1 s.

Output path, in order of precedence: ``--out``, then ``$LUDO_G1_CHECKPOINT_DIR/dummy/result.txt``
(``greennode.sh train`` sets that variable), then ``data/checkpoints/dummy/result.txt``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import socket
import sys
import time
from pathlib import Path


def default_out() -> Path:
    root = os.environ.get("LUDO_G1_CHECKPOINT_DIR")
    if root:
        return Path(root) / "dummy" / "result.txt"
    return Path("data/checkpoints/dummy/result.txt")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=60.0, help="wait this long before writing (default: 60)")
    parser.add_argument("--out", type=Path, default=None, help="output file (default: see module docstring)")
    parser.add_argument("--note", default="", help="free text copied into the result file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = args.out if args.out is not None else default_out()
    started = dt.datetime.now(dt.timezone.utc)
    print(f"dummy_job: waiting {args.seconds} s, will write {out}", flush=True)
    time.sleep(args.seconds)
    finished = dt.datetime.now(dt.timezone.utc)

    lines = [
        f"hostname: {socket.gethostname()}",
        f"platform: {platform.platform()}",
        f"python: {platform.python_version()} ({sys.executable})",
        f"started_utc: {started.isoformat(timespec='seconds')}",
        f"finished_utc: {finished.isoformat(timespec='seconds')}",
        f"waited_seconds: {args.seconds}",
        f"cwd: {Path.cwd()}",
    ]
    if args.note:
        lines.append(f"note: {args.note}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"dummy_job: wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
