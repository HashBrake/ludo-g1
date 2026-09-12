"""The pre-flight table: what a motion session is judged on, and how it is printed (T-042, D-013).

Split unchanged out of ``tools/hardware_checks/session_preflight.py``: the :data:`MOTION_KEYS` data,
the :class:`Row` the checks produce, and the two functions that turn a list of rows into a table and
into an exit code. The checks themselves -- which open devices and read config -- stay there.

Since T-045 each motion key also says which runbook step it gates (:attr:`MotionKey.gates`) and
whether a human's approval of the placeholder is enough for it (:attr:`MotionKey.approved_ok`,
D-022). A step is a Phase 1 motion run: ``t021_latency`` is runbook 3.4, ``t024_envelope`` 3.5,
``t022_hand`` 3.6, ``t023_reach`` 3.7, and ``phase2_recording`` is the first recorded teleop. The
default, :data:`ALL_STEPS`, judges every key and is the strictest reading.

This module is data and formatting. It opens nothing, reads nothing and commands nothing (R1, R2);
a Phase 1 measurement lands by editing a yaml file, never by editing :data:`MOTION_KEYS`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ALL_STEPS",
    "FAIL",
    "MOTION_KEYS",
    "NO_STATUS",
    "PASS",
    "SKIP",
    "STEPS",
    "MotionKey",
    "Row",
    "exit_code",
    "render",
]

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

#: Printed in the status column of a row that has no config status word (every non-config row).
NO_STATUS = "-"

#: The Phase 1 motion runs a key can gate, in the order docs/runbook_phase1.md performs them.
STEPS: tuple[str, ...] = ("t021_latency", "t024_envelope", "t022_hand", "t023_reach", "phase2_recording")
#: ``--for all``: judge every key whatever it gates. The default, and the strictest reading.
ALL_STEPS = "all"


@dataclass(frozen=True, slots=True)
class MotionKey:
    """One placeholder that gates motion: where it lives, why, and what it gates.

    ``gates`` names the steps of :data:`STEPS` that cannot be run while this key is a placeholder;
    a step not in the list still prints the key, without a ``*``, because a human reading the table
    wants the whole picture. ``approved_ok`` marks the keys D-022 lets a human pass at
    HUMAN_APPROVED instead of MEASURED: the envelope and the arm gains, whose values are what the
    session they gate is going to measure and which R3 says only a human may set. Every other key
    is measured before the motion day (day 1 and day 2 are read-only) and must read MEASURED.
    """

    name: str
    key: str
    why: str
    gates: tuple[str, ...]
    approved_ok: bool = False

    def gates_step(self, step: str) -> bool:
        """Whether this key gates ``step``. :data:`ALL_STEPS` is gated by every key."""
        return step == ALL_STEPS or step in self.gates


#: Every step: the envelope and the gains are in force for any command, whatever the run is doing.
_EVERY: tuple[str, ...] = STEPS
#: The steps that command an arm joint over DDS (the hand bench drives the DexH15 bus only).
_ARM: tuple[str, ...] = ("t021_latency", "t024_envelope", "t023_reach", "phase2_recording")
#: The steps that close the hand.
_HAND: tuple[str, ...] = ("t022_hand", "t023_reach", "phase2_recording")
#: The steps that run the whole teleop chain onto a board cell, and so need the camera, the tags,
#: the controller-to-pelvis transform and the four latencies that align the streams (D-022).
_TELEOP: tuple[str, ...] = ("t023_reach", "phase2_recording")

#: The placeholders that gate motion. Data, not code -- a Phase 1 measurement lands by editing the
#: yaml, never this tuple. Every other UNMEASURED key in the six config files is a placeholder this
#: tool deliberately does not judge: it cannot make a commanded joint go somewhere wrong.
MOTION_KEYS: tuple[MotionKey, ...] = (
    # --- the envelope and the gains: in force for every motion step, approvable by a human (D-022)
    MotionKey("safety", "workspace_box_m.min", "the box corner that keeps the wrist over the table", _EVERY, True),
    MotionKey("safety", "workspace_box_m.max", "the box corner that keeps the wrist off the operator", _EVERY, True),
    MotionKey("safety", "workspace_box_m.margin_m", "slack removed from the box for the hand", _EVERY, True),
    MotionKey("safety", "joint_limits_rad", "per-joint stops the command may never ride", _EVERY, True),
    MotionKey("safety", "waist_yaw_clamp_rad", "how far the hip-mounted torso may swing the arm", _EVERY, True),
    MotionKey("safety", "joint_velocity_limit_rad_s", "how fast a commanded joint may move", _EVERY, True),
    MotionKey("safety", "first_command_max_step_rad", "the D-018 lurch cap on a stream's first command", _EVERY, True),
    MotionKey("safety", "watchdog_timeout_s", "when the driver hands the arm back to the robot", _EVERY, True),
    MotionKey("robot", "control.kp", "the stiffness every commanded target is executed with", _EVERY, True),
    MotionKey("robot", "control.kd", "the damping every commanded target is executed with", _EVERY, True),
    MotionKey("robot", "control.weight_ramp_s", "how slowly arm_sdk takes the arm over", _EVERY, True),
    # --- measured read-only on day 1 and day 2: MEASURED or nothing
    MotionKey("robot", "network.dds_interface", "which interface carries the commands (H-002)", _ARM),
    MotionKey("hand", "device.port", "which bus the DexH15 answers on (H-003)", _HAND),
    MotionKey("cameras", "top.device", "the frame the goal cells are grounded in (H-001, H-003)", _TELEOP),
    MotionKey("board", "apriltags.family", "the tag family the homography is detected with", _TELEOP),
    MotionKey("board", "apriltags.size_mm", "tag size; wrong means a scaled board frame", _TELEOP),
    MotionKey("board", "apriltags.ids", "which tag is which corner", _TELEOP),
    MotionKey("board", "apriltags.centres_mm", "where the tags sit on the printed board", _TELEOP),
    # --- measured by an earlier motion step, so they gate the later ones only (D-022)
    MotionKey("hand", "pinch.open_pose", "the joint pose the pinch scalar 0 expands to", _HAND),
    MotionKey("hand", "pinch.closed_pose", "the joint pose the pinch scalar 1 expands to", _HAND),
    MotionKey("robot", "latency.arm_ms", "arm actuation latency, measured by t021_latency", _TELEOP),
    MotionKey("robot", "latency.hand_ms", "hand actuation latency, measured by t022_hand", _TELEOP),
    MotionKey("robot", "latency.glove_ms", "glove input latency on the teleop path", _TELEOP),
    MotionKey("robot", "latency.pico_ms", "controller input latency on the teleop path", _TELEOP),
    MotionKey("robot", "teleop.pico_to_pelvis", "controller frame to robot frame; wrong means wrong targets", _TELEOP),
)


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the table. ``motion_relevant`` is what the exit code is computed from.

    ``key_status`` is the config status word behind the verdict (UNMEASURED, MEASURED,
    HUMAN_APPROVED) for a config row, and :data:`NO_STATUS` for every other row.
    """

    check: str
    status: str
    detail: str
    motion_relevant: bool = False
    key_status: str = NO_STATUS


def exit_code(rows: list[Row]) -> int:
    """0 only when every motion-relevant row is PASS (a SKIP on one of them is not a pass)."""
    return 0 if all(row.status == PASS for row in rows if row.motion_relevant) else 1


def render(rows: list[Row], step: str = ALL_STEPS) -> str:
    """The table, widest check name first, with ``*`` marking the rows the exit code is made of.

    ``step`` only changes the wording of the verdict: which rows carry a ``*`` was decided when they
    were built. The default wording is the one docs/runbook_phase1.md and docs/safety.md quote.
    """
    width = max((len(row.check) for row in rows), default=10)
    swidth = max([len("status"), *(len(row.key_status) for row in rows)])
    head = f"{'check'.ljust(width)}  result  {'status'.ljust(swidth)}  detail"
    out = [f"{'':2}{head}", f"{'':2}{'-' * width}  ------  {'-' * swidth}  {'-' * 40}"]
    for row in rows:
        mark = "* " if row.motion_relevant else "  "
        out.append(f"{mark}{row.check.ljust(width)}  {row.status:<6}  {row.key_status.ljust(swidth)}  {row.detail}")
    motion = [row for row in rows if row.motion_relevant]
    failed = [row for row in motion if row.status != PASS]
    named = "" if not failed else ": " + ", ".join(row.check for row in failed)
    one = "motion-relevant check" if step == ALL_STEPS else f"check that gates {step}"
    many = "motion-relevant checks" if step == ALL_STEPS else f"checks that gate {step}"
    out += ["", f"* {len(motion) - len(failed)}/{len(motion)} {many} pass{named}"]
    if failed:
        out.append("NO-GO for a motion session." if step == ALL_STEPS else f"NO-GO for {step}.")
    else:
        out.append(f"GO: every {one} passes.")
    return "\n".join(out)
