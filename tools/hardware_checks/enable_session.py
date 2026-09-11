#!/usr/bin/env python3
"""Open a hardware motion session (CLAUDE.md 4.6). A human runs this; no agent ever does.

This is the only writer of ``hardware/session.enable``, the file :mod:`runtime.safety` requires
before any motion command may leave this machine (R1). It is interactive on purpose: it asks who you
are and makes you confirm the four checklist items out loud, and it refuses to do anything at all
when stdin is not a terminal, so that no script, hook, agent or CI job can produce the file.

    .venv/bin/python tools/hardware_checks/enable_session.py

Exit codes: 0 session written, 1 refused (a checklist item was not confirmed, or no name), 2 not a
terminal. The session runs for ``session.default_seconds`` from ``config/safety.yaml`` (2 h). To end
a session early, delete the file.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Run from anywhere: this script is executed by a human, usually from the repo root, sometimes not.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from runtime import config  # noqa: E402
from runtime.safety import SessionGate  # noqa: E402

#: Timestamps in the session file are local wall clock with an explicit offset (CLAUDE.md 4.3, 4.6).
TZ = ZoneInfo("Asia/Bangkok")

#: The four items of CLAUDE.md 4.6. Every one must be confirmed; there is no "skip".
CHECKLIST: tuple[str, ...] = (
    "e-stop within reach",
    "legs locked",
    "workspace clear",
    "humans out of the arm envelope",
)

_YES = frozenset({"y", "yes"})


def session_text(enabled_by: str, enabled_at: datetime, seconds: float, checklist_value: str) -> str:
    """The exact four lines of CLAUDE.md 4.6, in order, newline-terminated."""
    expires_at = enabled_at + timedelta(seconds=seconds)
    return (
        f"enabled_by: {enabled_by}\n"
        f"enabled_at: {enabled_at.isoformat()}\n"
        f"expires_at: {expires_at.isoformat()}\n"
        f"checklist: {checklist_value}\n"
    )


def write_session(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically: a half-written gate file must never be readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".session.enable.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def main(argv: list[str] | None = None) -> int:
    if argv:
        print(
            f"{Path(__file__).name}: takes no arguments; the session length comes from "
            "config/safety.yaml",
            file=sys.stderr,
        )
        return 2
    if not sys.stdin.isatty():
        print(
            "enable_session: stdin is not a terminal. A hardware session is enabled by a human at a "
            "keyboard and by nobody else (CLAUDE.md R1, 4.6). Nothing was written.",
            file=sys.stderr,
        )
        return 2

    gate = SessionGate()
    cfg = config.load("safety")["session"]
    seconds = float(cfg["default_seconds"])
    checklist_value = str(cfg["required_checklist_value"])

    current = gate.status()
    if current.valid:
        print(f"A session is already open: {current.reason}")
        print("Continuing replaces it with a fresh one.")
    print(f"Session file: {gate.path}")
    print(f"Length:       {seconds / 3600:.2f} h (config/safety.yaml session.default_seconds)")
    print(f"Envelope:     config/safety.yaml, hash {config.config_hash('safety')[:12]}")
    print()

    name = _ask("Your name: ")
    if not name:
        print("enable_session: no name given; nothing was written.", file=sys.stderr)
        return 1

    print("\nConfirm each item with 'yes'. Anything else aborts.")
    for item in CHECKLIST:
        answer = _ask(f"  {item}? ").lower()
        if answer not in _YES:
            print(f"enable_session: {item!r} not confirmed; nothing was written.", file=sys.stderr)
            return 1

    enabled_at = datetime.now(TZ).replace(microsecond=0)
    write_session(gate.path, session_text(name, enabled_at, seconds, checklist_value))

    status = gate.status()
    if not status.valid:
        print(f"enable_session: wrote {gate.path} but the gate still refuses it: {status.reason}", file=sys.stderr)
        return 1
    print(f"\nSession open until {status.expires_at.isoformat() if status.expires_at else '?'}.")
    print(f"Delete {gate.path} to end it early.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
