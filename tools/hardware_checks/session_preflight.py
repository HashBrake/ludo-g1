#!/usr/bin/env python3
"""Read-only go/no-go table to run before a hardware session is opened (T-041; CLAUDE.md 4.6, R1).

One row per check, each PASS, FAIL or SKIP, and one exit code. It is the first step of the Phase 1
session procedure: run this, fix every FAIL that gates motion, then a human runs
``enable_session.py``. Nothing here writes anything, opens no writer and sends no motion command;
the devices are opened through the read-only drivers of ``drivers/`` only (R1, R2), so this tool is
safe with the robot powered and safe with nothing plugged in at all.

The rows, in table order, each documented on the function that produces it: the session gate
(:func:`session_row`), the e-stop named in the checklist (:func:`estop_row`, Q-004), one row per
placeholder that gates motion (:func:`config_rows` over :data:`MOTION_KEYS`), one row per device
opened read-only for the budget (:func:`device_rows` over :data:`DEVICES`), and the board
calibration, the dataset disk and the git tree (:func:`calibration_row`, :func:`disk_row`,
:func:`git_row`).

Only some rows decide the exit code: the placeholders, the e-stop, and the ``arm`` and ``hand``
devices, which are the two a motion command can reach (R1). The rest are reported because a human
about to run a session wants to see them, not because they can make an arm move wrongly, and the
table marks the difference with a ``*``. Exit codes: 0 when every motion-relevant row is PASS, 1
when any is not (a SKIP included), 2 usage error.

Usage:
    .venv/bin/python tools/hardware_checks/session_preflight.py [--json] [--budget 10] [--no-devices]
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Run from anywhere: this script is executed by a human, usually from the repo root, sometimes not.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from runtime import config  # noqa: E402
from runtime.safety import REPO_ROOT  # noqa: E402
from tools.hardware_checks import stream_stats  # noqa: E402

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

#: The placeholders that gate motion: (config file, dotted key, why a motion session needs it).
#: Data, not code -- a Phase 1 measurement lands by editing the yaml, never this tuple. Every other
#: UNMEASURED key in the six config files is a placeholder this tool deliberately does not judge:
#: it cannot make a commanded joint go somewhere wrong.
MOTION_KEYS: tuple[tuple[str, str, str], ...] = (
    ("safety", "workspace_box_m.min", "the box corner that keeps the wrist over the table"),
    ("safety", "workspace_box_m.max", "the box corner that keeps the wrist off the operator"),
    ("safety", "workspace_box_m.margin_m", "slack removed from the box for the hand and the horse"),
    ("safety", "joint_limits_rad", "per-joint stops the command may never ride"),
    ("safety", "waist_yaw_clamp_rad", "how far the hip-mounted torso may swing the arm"),
    ("safety", "joint_velocity_limit_rad_s", "how fast a commanded joint may move"),
    ("safety", "first_command_max_step_rad", "the D-018 lurch cap on the first command of a stream"),
    ("safety", "watchdog_timeout_s", "when the driver hands the arm back to the robot"),
    ("robot", "network.dds_interface", "which interface carries the commands (H-002)"),
    ("robot", "control.kp", "the stiffness every commanded target is executed with"),
    ("robot", "control.kd", "the damping every commanded target is executed with"),
    ("robot", "control.weight_ramp_s", "how slowly arm_sdk takes the arm over"),
    ("robot", "latency.arm_ms", "arm actuation latency, Phase 1's first measurement"),
    ("robot", "latency.hand_ms", "hand actuation latency, Phase 1's first measurement"),
    ("robot", "latency.glove_ms", "glove input latency on the teleop path"),
    ("robot", "latency.pico_ms", "controller input latency on the teleop path"),
    ("robot", "teleop.pico_to_pelvis", "controller frame to robot frame; wrong means wrong targets"),
    ("hand", "device.port", "which bus the DexH15 answers on (H-003)"),
    ("hand", "pinch.open_pose", "the joint pose the pinch scalar 0 expands to"),
    ("hand", "pinch.closed_pose", "the joint pose the pinch scalar 1 expands to"),
    ("cameras", "top.device", "the frame the goal cells are grounded in (H-001, H-003)"),
    ("board", "apriltags.family", "the tag family the homography is detected with"),
    ("board", "apriltags.size_mm", "tag size; wrong means a scaled board frame"),
    ("board", "apriltags.ids", "which tag is which corner"),
    ("board", "apriltags.centres_mm", "where the tags sit on the printed board"),
)

#: Devices opened read-only, in the order the table prints them. ``arm`` and ``hand`` are the two a
#: motion command can reach (R1) and are the only motion-relevant device rows.
DEVICES: tuple[str, ...] = ("arm", "hand", "glove", "pose", "top", "oblique", "palm")
MOTION_DEVICES: frozenset[str] = frozenset({"arm", "hand"})
#: Per-device constructor arguments, so that a driver that would otherwise wait on its own config
#: timeout stays inside this tool's budget.
_BUDGETED: frozenset[str] = frozenset({"arm", "pose"})

#: A checklist item mentioning one of these is talking about an e-stop (Q-004)...
_ESTOP_WORDS: tuple[str, ...] = ("e-stop", "estop", "emergency")
#: ...and it answers Q-004 only if it also names a concrete device or action, not just "within reach".
_DEVICE_WORDS: tuple[str, ...] = (
    "remote", "damp", "chord", "power", "breaker", "switch", "button", "app", "plug", "socket",
    "cable", "mains", "psu", "battery", "killswitch", "kill switch", "unplug", "cut",
)

#: CLAUDE.md 3.4 and Q-002: at least 500 GB under data/ before Phase 2 recording.
DATASET_TARGET_GB = 500.0

#: How long the poll loop sleeps between drains of a queued stream, in seconds.
_DRAIN_POLL_S = 0.005


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the table. ``motion_relevant`` is what the exit code is computed from."""

    check: str
    status: str
    detail: str
    motion_relevant: bool = False


def _lookup(data: dict, dotted: str) -> tuple[bool, Any]:
    """Look a dotted path up through mappings only. Returns ``(found, value)``."""
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def config_rows(root: Path | str | None = None) -> list[Row]:
    """One row per :data:`MOTION_KEYS` entry: PASS when measured, FAIL while it is a placeholder."""
    rows: list[Row] = []
    cache: dict[str, tuple[dict | None, tuple[str, ...], str]] = {}
    for name, key, why in MOTION_KEYS:
        if name not in cache:
            try:
                cache[name] = (config.load(name, root), tuple(config.unmeasured(name, root)), "")
            except config.ConfigError as exc:
                cache[name] = (None, (), str(exc))
        data, placeholders, error = cache[name]
        if data is None:
            status, detail = FAIL, error
        elif not _lookup(data, key)[0]:
            status, detail = FAIL, f"config/{name}.yaml has no key {key!r}"
        elif any(key == p or key.startswith(f"{p}.") for p in placeholders):
            status, detail = FAIL, f"UNMEASURED: {why}"
        else:
            status, detail = PASS, f"measured: {why}"
        rows.append(Row(f"config {name}.{key}", status, detail, True))
    return rows


def estop_row(root: Path | str | None = None, checklist: tuple[str, ...] | None = None) -> Row:
    """Q-004: the session checklist must name the physical e-stop, not just promise one is near."""
    if checklist is None:
        try:
            session = config.load("safety", root)["session"]
        except config.ConfigError as exc:
            return Row("e-stop named", FAIL, str(exc), True)
        from tools.hardware_checks.enable_session import CHECKLIST

        configured = session.get("checklist")
        usable = isinstance(configured, list) and configured
        checklist = tuple(str(item) for item in configured) if usable else CHECKLIST
    items = [item for item in checklist if any(word in item.lower() for word in _ESTOP_WORDS)]
    if not items:
        return Row("e-stop named", FAIL, f"no checklist item mentions an e-stop: {list(checklist)}", True)
    for item in items:
        named = [word for word in _DEVICE_WORDS if word in item.lower()]
        if named:
            return Row("e-stop named", PASS, f"{item!r} names {named[0]!r}", True)
    return Row("e-stop named", FAIL, f"{items[0]!r} names no device; Q-004 is unanswered (D-004)", True)


def session_row(gate: Any | None = None) -> Row:
    """What the gate says now. Informational: this tool runs before the session is opened."""
    if gate is None:
        from runtime.safety import SessionGate

        gate = SessionGate()
    try:
        status = gate.status()
    except Exception as exc:  # the gate fails closed; so does the row
        return Row("session gate", FAIL, f"gate unusable: {exc!r}")
    if getattr(status, "valid", False):
        return Row("session gate", PASS, f"open: {status.reason}")
    return Row("session gate", SKIP, f"closed: {status.reason}")


def _measure(source: Any, budget: float) -> tuple[float, int, float, dict[str, Any]]:
    """``(rate_hz, samples, span_s, probe)`` from ``budget`` seconds of a read-only stream.

    A queued stream (the arm, the real glove) is drained with no warmup, so that a device which is
    open but silent reports zero samples rather than blocking. A polled one (the cameras, the hand,
    the controller) discards one sample first: its first read blocks until the device has started --
    the Orbbec Ego's first frame takes ~2.5 s -- and that wait would otherwise eat the budget and be
    reported as a rate. A polled device that is silent raises instead of hanging.
    """
    if hasattr(source, "poll"):
        ts = stream_stats.drain(source, budget, poll_s=_DRAIN_POLL_S)
    else:
        ts = stream_stats.stream(source, budget, poll_s=0.0, warmup=1)
    span = (ts[-1] - ts[0]) / 1e9 if len(ts) > 1 else 0.0
    probe: dict[str, Any] = {}
    if hasattr(source, "probe"):
        takes_window = "window_s" in inspect.signature(source.probe).parameters
        result = source.probe(window_s=budget) if takes_window else source.probe()
        probe = dataclasses.asdict(result) if dataclasses.is_dataclass(result) else {}
    return ((len(ts) - 1) / span if span > 0 else 0.0, len(ts), span, probe)


def device_rows(
    budget: float = 3.0, make_fn: Callable[..., Any] | None = None, devices: tuple[str, ...] = DEVICES
) -> list[Row]:
    """Open each device read-only for ``budget`` seconds and report the rate it actually delivered."""
    from drivers.cameras import CameraUnavailable
    from drivers.dexh15 import HandUnavailable
    from drivers.g1_arm import ArmUnavailable
    from drivers.pico import PoseUnavailable
    from drivers.pxcap import GloveUnavailable

    unavailable = (CameraUnavailable, ArmUnavailable, HandUnavailable, GloveUnavailable, PoseUnavailable)
    if make_fn is None:
        from drivers import make as make_fn  # type: ignore[assignment]

    rows: list[Row] = []
    for name in devices:
        check, motion = f"device {name}", name in MOTION_DEVICES
        kwargs = {"timeout_s": budget} if name in _BUDGETED else {}
        try:
            source = make_fn(name, backend="real", **kwargs)
        except unavailable as exc:
            rows.append(Row(check, SKIP, f"absent: {exc}", motion))
            continue
        try:
            rate, samples, span, probe = _measure(source, budget)
        except unavailable as exc:
            rows.append(Row(check, FAIL, f"opened, then stopped: {exc}", motion))
            continue
        finally:
            if hasattr(source, "close"):
                source.close()
        extra = ", ".join(
            f"{key} {probe[key]}" for key in ("connected", "card", "serial_number", "device_sn", "mode_machine")
            if probe.get(key) not in (None, "")
        )
        if probe.get("connected") is False:
            status, detail = FAIL, f"opened but reports connected=False ({extra})"
        elif samples < 2:
            status, detail = FAIL, f"opened but delivered {samples} sample(s) in {budget:g} s"
        else:
            rate_s = f"{rate:.1f} Hz, {samples} samples in {span:.1f} s"
            status, detail = PASS, f"{rate_s}{'; ' + extra if extra else ''}"
        rows.append(Row(check, status, detail, motion))
    return rows


def calibration_row(path: Path | str | None = None, root: Path | str | None = None) -> Row:
    """The board homography: present, readable, and taken under the current ``config/board.yaml``."""
    path = Path(config.CONFIG_DIR / "board_calib.yaml" if path is None else path)
    if not path.exists():
        return Row("board calibration", FAIL, f"{path} does not exist; H-001 then `python -m board.calibration`")
    from board.calibration import CalibrationError, load

    try:
        calib = load(path)
        current = config.config_hash("board", root)
    except (CalibrationError, config.ConfigError) as exc:
        return Row("board calibration", FAIL, str(exc))
    if calib.board_config_hash != current:
        stale = f"stale: taken under board.yaml {calib.board_config_hash[:12]}, now {current[:12]}"
        return Row("board calibration", FAIL, stale)
    return Row("board calibration", PASS, f"{path.name}, rms {calib.rms_px:.3f} px, from {calib.image_path}")


def disk_row(path: Path | str | None = None, target_gb: float = DATASET_TARGET_GB) -> Row:
    """Free space where the datasets go, against the brief's target (CLAUDE.md 3.4, Q-002)."""
    path = Path(REPO_ROOT / "data" if path is None else path)
    probe = path if path.exists() else path.parent
    try:
        free_gb = shutil.disk_usage(probe).free / 1e9
    except OSError as exc:
        return Row("dataset disk", FAIL, f"cannot stat {probe}: {exc}")
    status = PASS if free_gb >= target_gb else FAIL
    return Row("dataset disk", status, f"{free_gb:.1f} GB free at {probe} (target {target_gb:g} GB, Q-002)")


def git_row(repo: Path | str = REPO_ROOT) -> Row:
    """A clean tree and the HEAD hash, so a session's log line can be tied to a commit.

    Every ``GIT_*`` variable is dropped from the child's environment first. A git hook exports
    ``GIT_DIR`` and ``GIT_INDEX_FILE``, and a run started from one would otherwise report a
    different repository than ``repo``.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*args: str) -> str:
        done = ["git", "-C", str(repo), *args]
        return subprocess.run(done, capture_output=True, text=True, timeout=20, check=True, env=env).stdout

    try:
        head, dirty = git("rev-parse", "--short", "HEAD").strip(), git("status", "--porcelain").splitlines()
    except (OSError, subprocess.SubprocessError) as exc:
        return Row("git", FAIL, f"cannot read the repo at {repo}: {exc}")
    if dirty:
        return Row("git", FAIL, f"HEAD {head}, {len(dirty)} uncommitted path(s): {dirty[0].strip()} ...")
    return Row("git", PASS, f"HEAD {head}, tree clean")


def collect(
    *,
    config_root: Path | str | None = None, gate: Any | None = None,
    make_fn: Callable[..., Any] | None = None, budget: float = 3.0,
    devices: tuple[str, ...] = DEVICES,
) -> list[Row]:
    """Every row, in table order. Each argument is a seam a test injects a fake through; the three
    rows that take no seam here (calibration, disk, git) are informational and take their own."""
    rows = [session_row(gate), estop_row(config_root)]
    rows += config_rows(config_root)
    rows += device_rows(budget, make_fn, devices)
    return rows + [calibration_row(root=config_root), disk_row(), git_row()]


def exit_code(rows: list[Row]) -> int:
    """0 only when every motion-relevant row is PASS (a SKIP on one of them is not a pass)."""
    return 0 if all(row.status == PASS for row in rows if row.motion_relevant) else 1


def render(rows: list[Row]) -> str:
    """The table, widest check name first, with ``*`` marking the rows the exit code is made of."""
    width = max((len(row.check) for row in rows), default=10)
    out = [f"{'':2}{'check'.ljust(width)}  status  detail", f"{'':2}{'-' * width}  ------  {'-' * 40}"]
    for row in rows:
        out.append(f"{'* ' if row.motion_relevant else '  '}{row.check.ljust(width)}  {row.status:<6}  {row.detail}")
    motion = [row for row in rows if row.motion_relevant]
    failed = [row for row in motion if row.status != PASS]
    named = "" if not failed else ": " + ", ".join(row.check for row in failed)
    out += ["", f"* {len(motion) - len(failed)}/{len(motion)} motion-relevant checks pass{named}"]
    out.append("NO-GO for a motion session." if failed else "GO: every motion-relevant check passes.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/hardware_checks/session_preflight.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--budget", type=float, default=3.0, help="seconds to sample each device")
    parser.add_argument("--no-devices", action="store_true", help="skip the device rows entirely")
    parser.add_argument("--json", action="store_true", help="emit the rows as a JSON list")
    args = parser.parse_args(argv)
    if args.budget <= 0:
        print("--budget must be positive", file=sys.stderr)
        return 2

    rows = collect(budget=args.budget, devices=() if args.no_devices else DEVICES)
    if args.json:
        json.dump([dataclasses.asdict(row) for row in rows], sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(render(rows))
    return exit_code(rows)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
