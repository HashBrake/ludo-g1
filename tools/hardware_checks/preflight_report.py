"""The pre-flight table: what a motion session is judged on, and how it is printed (T-042, D-013).

Split unchanged out of ``tools/hardware_checks/session_preflight.py``: the :data:`MOTION_KEYS` data,
the :class:`Row` the checks produce, and the two functions that turn a list of rows into a table and
into an exit code. The checks themselves -- which open devices and read config -- stay there.

This module is data and formatting. It opens nothing, reads nothing and commands nothing (R1, R2);
a Phase 1 measurement lands by editing a yaml file, never by editing :data:`MOTION_KEYS`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FAIL", "MOTION_KEYS", "PASS", "SKIP", "Row", "exit_code", "render"]

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


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the table. ``motion_relevant`` is what the exit code is computed from."""

    check: str
    status: str
    detail: str
    motion_relevant: bool = False


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
