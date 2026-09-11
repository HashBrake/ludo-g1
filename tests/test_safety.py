"""Session gate, envelope and guard tests (T-005; CLAUDE.md R1, R3).

Every session file these tests look at is written under ``tmp_path``. Nothing here reads, writes or
even touches the real ``hardware/session.enable``, except the one test that proves
``enable_session.py`` refuses to write it when stdin is not a terminal -- and that test only compares
a before/after ``stat`` snapshot of the path.

No test in this file is marked ``motion``: nothing here talks to hardware. The forward kinematics is
a two-line mock; the real one is T-011.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import yaml

from runtime import config
from runtime.safety import (
    REPO_ROOT,
    Envelope,
    Guard,
    SafetyViolation,
    SessionGate,
)
from runtime.types import ACTION_DIM, ARM_DOF, JOINT_DIM, MotionCommand, RobotState
from tools.hardware_checks.enable_session import CHECKLIST, session_text, write_session

BKK = timezone(timedelta(hours=7))
SECOND_NS = 1_000_000_000


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------


def _config_root(tmp_path: Path, mutate=None) -> Path:
    """A copy of ``config/`` holding safety.yaml (optionally mutated) and robot.yaml."""
    root = tmp_path / "cfg"
    root.mkdir(exist_ok=True)
    safety = yaml.safe_load((config.CONFIG_DIR / "safety.yaml").read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(safety)
    (root / "safety.yaml").write_text(yaml.safe_dump(safety, sort_keys=False), encoding="utf-8")
    shutil.copy(config.CONFIG_DIR / "robot.yaml", root / "robot.yaml")
    return root


def _write_session(
    path: Path,
    *,
    enabled_by: str = "Alois",
    enabled_at: datetime | None = None,
    seconds: float = 7200,
    checklist: str = "confirmed",
) -> Path:
    """Write a session file in the 4.6 format at ``path`` (always under tmp_path)."""
    enabled_at = datetime.now(BKK).replace(microsecond=0) if enabled_at is None else enabled_at
    expires_at = enabled_at + timedelta(seconds=seconds)
    path.write_text(
        f"enabled_by: {enabled_by}\n"
        f"enabled_at: {enabled_at.isoformat()}\n"
        f"expires_at: {expires_at.isoformat()}\n"
        f"checklist: {checklist}\n",
        encoding="utf-8",
    )
    return path


def fk_center(_joints: np.ndarray) -> np.ndarray:
    """Mock fk: always in the middle of the workspace box."""
    return np.array([0.40, 0.25, -0.05])


def fk_linear(joints: np.ndarray) -> np.ndarray:
    """Mock fk: the first three joints translate the point, in metres per radian."""
    return np.array([0.40, 0.25, -0.05]) + np.asarray(joints[:3], dtype=np.float64)


def envelope(fk=fk_center, root: Path | None = None) -> Envelope:
    return Envelope.from_config(fk, root=root)


def bare_envelope() -> Envelope:
    """The same envelope with no fk at all: the constructor's default, which fails closed."""
    env = envelope()
    return Envelope(
        names=env.names,
        lower=env.lower,
        upper=env.upper,
        box_min=env.box_min,
        box_max=env.box_max,
        box_frame=env.box_frame,
        box_point=env.box_point,
        velocity_limit_rad_s=env.velocity_limit_rad_s,
        command_rate_limit_hz=env.command_rate_limit_hz,
        command_gap_reset_s=env.command_gap_reset_s,
        first_command_max_step_rad=env.first_command_max_step_rad,
        watchdog_timeout_s=env.watchdog_timeout_s,
        pinch_range=env.pinch_range,
        pinch_rate_limit_per_s=env.pinch_rate_limit_per_s,
    )


def command(joints=0.0, pinch: float = 0.0) -> MotionCommand:
    q = np.full(JOINT_DIM, joints, dtype=np.float64) if np.isscalar(joints) else np.asarray(joints, float)
    return MotionCommand(arm=q[:ARM_DOF], waist_yaw=float(q[ARM_DOF]), pinch=pinch)


def state(joints=0.0, pinch: float = 0.0, ts_ns: int = 0) -> RobotState:
    q = np.full(JOINT_DIM, joints, dtype=np.float64) if np.isscalar(joints) else np.asarray(joints, float)
    return RobotState(arm=q[:ARM_DOF], waist_yaw=float(q[ARM_DOF]), pinch=pinch, ts_ns=ts_ns)


# --------------------------------------------------------------------------------------------------
# runtime/types.py
# --------------------------------------------------------------------------------------------------


def test_motion_command_holds_the_nine_numbers_of_section_5_3() -> None:
    cmd = MotionCommand.from_action(np.arange(ACTION_DIM, dtype=float))
    assert cmd.arm.shape == (ARM_DOF,)
    assert cmd.joints.shape == (JOINT_DIM,)
    assert cmd.waist_yaw == 7.0 and cmd.pinch == 8.0
    assert np.array_equal(cmd.to_action(), np.arange(ACTION_DIM, dtype=float))
    assert cmd.clamped == ()


def test_motion_command_rejects_a_wrong_sized_arm() -> None:
    with pytest.raises(ValueError, match="7 joint values"):
        MotionCommand(arm=np.zeros(6), waist_yaw=0.0, pinch=0.0)
    with pytest.raises(ValueError, match="9 values"):
        MotionCommand.from_action(np.zeros(8))


def test_motion_command_copies_its_input() -> None:
    arm = np.zeros(ARM_DOF)
    cmd = MotionCommand(arm=arm, waist_yaw=0.0, pinch=0.0)
    arm[0] = 99.0
    assert cmd.arm[0] == 0.0, "the command must not alias the caller's array"


# --------------------------------------------------------------------------------------------------
# R1: SessionGate
# --------------------------------------------------------------------------------------------------


def test_gate_default_path_comes_from_the_config_and_is_repo_relative() -> None:
    gate = SessionGate()
    expected = REPO_ROOT / config.load("safety")["session"]["file"]
    assert gate.path == expected
    assert gate.path.name == "session.enable"


def test_gate_without_a_file_is_invalid(tmp_path: Path) -> None:
    status = SessionGate(tmp_path / "absent").status()
    assert status.valid is False
    assert "cannot read" in status.reason
    assert status.enabled_by is None


def test_gate_accepts_a_valid_file(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "session.enable", enabled_by="Alois")
    status = SessionGate(path).status()
    assert status.valid is True, status.reason
    assert status.enabled_by == "Alois"
    assert status.expires_at is not None and status.seconds_left > 7000


def test_gate_rejects_an_expired_file(tmp_path: Path) -> None:
    start = datetime.now(BKK) - timedelta(hours=5)
    path = _write_session(tmp_path / "session.enable", enabled_at=start, seconds=7200)
    status = SessionGate(path).status()
    assert status.valid is False
    assert "expired" in status.reason


def test_gate_rejects_a_file_without_checklist_confirmed(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "session.enable", checklist="pending")
    status = SessionGate(path).status()
    assert status.valid is False
    assert "checklist" in status.reason


def test_gate_rejects_an_unparsable_file(tmp_path: Path) -> None:
    path = tmp_path / "session.enable"
    path.write_text("enabled_by Alois\nthis is not the session file\n", encoding="utf-8")
    status = SessionGate(path).status()
    assert status.valid is False
    assert "unparsable" in status.reason


def test_gate_rejects_an_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "session.enable"
    path.write_text("", encoding="utf-8")
    assert SessionGate(path).status().valid is False


@pytest.mark.parametrize("drop", ["enabled_by", "enabled_at", "expires_at", "checklist"])
def test_gate_rejects_a_file_missing_any_required_field(tmp_path: Path, drop: str) -> None:
    path = _write_session(tmp_path / "session.enable")
    kept = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith(f"{drop}:")]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    status = SessionGate(path).status()
    assert status.valid is False
    assert drop in status.reason


def test_gate_rejects_a_window_longer_than_max_seconds(tmp_path: Path) -> None:
    max_seconds = float(config.load("safety")["session"]["max_seconds"])
    path = _write_session(tmp_path / "session.enable", seconds=max_seconds + 60)
    status = SessionGate(path).status()
    assert status.valid is False
    assert "max_seconds" in status.reason


def test_gate_rejects_a_session_that_has_not_started(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "session.enable", enabled_at=datetime.now(BKK) + timedelta(hours=1))
    status = SessionGate(path).status()
    assert status.valid is False
    assert "future" in status.reason


def test_gate_rejects_timestamps_without_an_offset(tmp_path: Path) -> None:
    path = tmp_path / "session.enable"
    now = datetime.now()
    path.write_text(
        f"enabled_by: Alois\nenabled_at: {now.isoformat()}\n"
        f"expires_at: {(now + timedelta(hours=2)).isoformat()}\nchecklist: confirmed\n",
        encoding="utf-8",
    )
    status = SessionGate(path).status()
    assert status.valid is False
    assert "offset" in status.reason


def test_gate_rejects_a_duplicate_key(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "session.enable")
    path.write_text(path.read_text(encoding="utf-8") + "checklist: confirmed\n", encoding="utf-8")
    assert SessionGate(path).status().valid is False


def test_gate_re_judges_expiry_on_every_call(tmp_path: Path) -> None:
    """Expiry must take effect mid-run: same file, later wall clock, no longer valid."""
    enabled_at = datetime.now(BKK).replace(microsecond=0)
    path = _write_session(tmp_path / "session.enable", enabled_at=enabled_at, seconds=60)
    gate = SessionGate(path)
    assert gate.status().valid is True
    later = (enabled_at + timedelta(seconds=61)).astimezone(timezone.utc)
    assert gate.status(now=later).valid is False
    assert "expired" in gate.status(now=later).reason


def test_gate_notices_the_file_changing_under_it(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "session.enable", enabled_by="Alois")
    gate = SessionGate(path)
    assert gate.status().valid is True
    _write_session(path, enabled_by="Alois the second", checklist="pending")
    assert gate.status().valid is False
    _write_session(path, enabled_by="Alois")
    assert gate.status().valid is True
    path.unlink()
    assert gate.status().valid is False


# --------------------------------------------------------------------------------------------------
# R3: Envelope construction and config cross-checks
# --------------------------------------------------------------------------------------------------


def test_envelope_from_config_uses_the_committed_numbers() -> None:
    safety = config.load("safety")
    env = envelope()
    assert env.names[-1] == "waist_yaw_joint"
    assert len(env.names) == JOINT_DIM
    assert env.velocity_limit_rad_s == safety["joint_velocity_limit_rad_s"]
    assert env.command_rate_limit_hz == safety["command_rate_limit_hz"]
    assert env.watchdog_timeout_s == safety["watchdog_timeout_s"]
    assert env.first_command_max_step_rad == safety["first_command_max_step_rad"]
    assert env.pinch_range == tuple(safety["hand"]["pinch_scalar_range"])
    # D-018: the fresh-reference cap is a tightening of the velocity rule, never a widening.
    assert env.first_command_max_step_rad < env.velocity_limit_rad_s * env.command_gap_reset_s


def test_envelope_applies_the_box_margin_inward() -> None:
    box = config.load("safety")["workspace_box_m"]
    env = envelope()
    margin = float(box["margin_m"])
    assert np.allclose(env.box_min, np.asarray(box["min"], float) + margin)
    assert np.allclose(env.box_max, np.asarray(box["max"], float) - margin)


def test_waist_limit_is_the_tighter_of_clamp_and_joint_limit() -> None:
    safety = config.load("safety")
    clamp = float(safety["waist_yaw_clamp_rad"])
    limit = safety["joint_limits_rad"]["waist_yaw_joint"]
    env = envelope()
    i = env.names.index("waist_yaw_joint")
    assert env.lower[i] == max(float(limit[0]), -clamp)
    assert env.upper[i] == min(float(limit[1]), clamp)
    assert env.upper[i] == clamp, "the clamp is the tighter one in the committed config"


def test_envelope_refuses_a_joint_list_that_disagrees_with_robot_yaml(tmp_path: Path) -> None:
    def rename(safety: dict) -> None:
        safety["joint_limits_rad"]["left_elbow_joint_typo"] = safety["joint_limits_rad"].pop("left_elbow_joint")

    root = _config_root(tmp_path, rename)
    with pytest.raises(config.ConfigError, match="must match in name and order"):
        envelope(root=root)


def test_envelope_refuses_a_limit_wider_than_the_mechanical_range(tmp_path: Path) -> None:
    def widen(safety: dict) -> None:
        safety["joint_limits_rad"]["left_elbow_joint"] = [-3.5, 3.5]

    root = _config_root(tmp_path, widen)
    with pytest.raises(config.ConfigError, match="wider than the mechanical range"):
        envelope(root=root)


def test_envelope_refuses_an_empty_box(tmp_path: Path) -> None:
    def shrink(safety: dict) -> None:
        safety["workspace_box_m"]["margin_m"] = 1.0

    root = _config_root(tmp_path, shrink)
    with pytest.raises(config.ConfigError, match="workspace box is empty"):
        envelope(root=root)


# --------------------------------------------------------------------------------------------------
# R3: Envelope.check
# --------------------------------------------------------------------------------------------------


def test_out_of_limit_joints_are_clamped_and_reported() -> None:
    env = envelope()
    over = np.zeros(JOINT_DIM)
    over[3] = 99.0  # left_elbow_joint, far past its limit
    over[7] = 99.0  # waist yaw, far past its clamp
    at_limit = np.clip(over, env.lower, env.upper)  # state already there, so only clamping is tested
    out = env.check(command(over), state(at_limit), now_ns=0)
    assert out.clamped == ("left_elbow_joint", "waist_yaw_joint")
    assert out.arm[3] == pytest.approx(env.upper[3])
    assert out.waist_yaw == pytest.approx(env.upper[7])
    assert np.all(out.joints <= env.upper + 1e-12) and np.all(out.joints >= env.lower - 1e-12)


def test_a_command_inside_every_limit_is_returned_unchanged() -> None:
    env = envelope()
    q = np.full(JOINT_DIM, 0.1)
    out = env.check(command(q, pinch=0.5), state(0.1, pinch=0.5), now_ns=0)
    assert out.clamped == ()
    assert np.allclose(out.joints, q) and out.pinch == 0.5


def test_rate_limit_rejects_the_second_command_inside_one_period_and_accepts_after() -> None:
    env = envelope()
    period_ns = int(round(SECOND_NS / env.command_rate_limit_hz))
    cmd, st = command(0.0), state(0.0)
    env.check(cmd, st, now_ns=0)
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(cmd, st, now_ns=period_ns - 1)
    assert excinfo.value.rule == "command_rate"
    env.check(cmd, st, now_ns=period_ns)  # exactly one period later: accepted


def test_a_fresh_command_may_not_step_further_than_the_first_command_cap(capsys) -> None:
    """D-018: the first command of a stream is measured against the state and may barely move it."""
    env = envelope()
    cap = env.first_command_max_step_rad
    st = state(0.0, ts_ns=0)
    env.check(command(cap), st, now_ns=0)  # exactly at the cap: accepted
    env.reset()
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(cap * 1.1), st, now_ns=0)
    assert excinfo.value.rule == "first_command_step"
    assert "left_shoulder_pitch_joint" in str(excinfo.value)  # the worst joint is named
    with capsys.disabled():
        print(f"\nfresh-reference cap: {cap:g} rad admitted, {cap * 1.1:g} rad refused as "
              f"{excinfo.value.rule} (the velocity rule alone would have allowed "
              f"{env.velocity_limit_rad_s * env.command_gap_reset_s:g} rad)")


def test_the_first_command_cap_is_judged_on_the_clamped_target() -> None:
    """A target outside the joint limits is clamped first, and the step is measured on the clamp."""
    env = envelope()
    beyond = np.zeros(JOINT_DIM)
    beyond[-1] = 100.0  # far outside the waist clamp, which pulls it back to waist_yaw_clamp_rad
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(beyond), state(0.0), now_ns=0)
    assert excinfo.value.rule == "first_command_step"
    assert f"{float(config.load('safety')['waist_yaw_clamp_rad']):.4f} rad" in str(excinfo.value)


def test_the_first_command_cap_applies_only_while_the_reference_is_fresh() -> None:
    """R3: once a stream is running the velocity rule takes over, unchanged, and allows far more."""
    env = envelope()
    step = 0.3  # 6x the fresh cap ...
    env.check(command(0.0), state(0.0), now_ns=0)
    dt_ns = int(0.4 * SECOND_NS)  # ... but 0.75 rad/s over 0.4 s, inside the gap and the limit
    assert 0.4 <= env.command_gap_reset_s and step / 0.4 < env.velocity_limit_rad_s
    out = env.check(command(step), state(0.0), now_ns=dt_ns)
    assert out.clamped == ()


def test_velocity_limit_rejects_a_target_too_far_from_the_previous_accepted_command() -> None:
    env = envelope()
    st = state(0.0, ts_ns=0)
    env.check(command(0.0), st, now_ns=0)
    dt_ns = int(0.4 * SECOND_NS)
    reach = env.velocity_limit_rad_s * 0.4  # what the velocity rule allows over that interval
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(reach * 1.1), st, now_ns=dt_ns)
    assert excinfo.value.rule == "joint_velocity"
    assert "rad/s" in str(excinfo.value)


def test_velocity_limit_uses_the_previous_accepted_command_inside_the_gap() -> None:
    env = envelope()
    period_ns = int(round(SECOND_NS / env.command_rate_limit_hz))
    st = state(0.0)
    env.check(command(0.0), st, now_ns=0)
    step = env.velocity_limit_rad_s * (period_ns / SECOND_NS)
    env.check(command(step * 0.9), st, now_ns=period_ns)
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(step * 3.0), st, now_ns=2 * period_ns)
    assert excinfo.value.rule == "joint_velocity"


def test_a_gap_longer_than_command_gap_reset_falls_back_to_the_state() -> None:
    env = envelope()
    st = state(0.0)
    env.check(command(0.0), st, now_ns=0)
    gap_ns = int(env.command_gap_reset_s * SECOND_NS) + SECOND_NS
    # Within the fresh cap of the measured state: accepted, and it is the state it is judged against.
    env.check(command(env.first_command_max_step_rad * 0.9), st, now_ns=gap_ns)
    # A resume is a fresh reference too, so the same cap applies to it (D-018).
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(env.first_command_max_step_rad * 1.5), st, now_ns=gap_ns + 2 * SECOND_NS)
    assert excinfo.value.rule == "first_command_step"


def test_workspace_box_rejects_a_point_outside_it() -> None:
    env = envelope(fk_linear)
    q = np.zeros(JOINT_DIM)
    q[0] = 0.4  # pushes x to 0.80 m, past the box maximum of 0.63 m
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(q), state(q), now_ns=0)
    assert excinfo.value.rule == "workspace_box"
    assert "left_wrist_yaw_link" in str(excinfo.value)


def test_workspace_box_margin_is_enforced() -> None:
    """A point between the raw box face and the margin is outside: the margin is real."""
    env = envelope(fk_linear)
    raw_max = np.asarray(config.load("safety")["workspace_box_m"]["max"], float)
    margin = float(config.load("safety")["workspace_box_m"]["margin_m"])
    q = np.zeros(JOINT_DIM)
    q[0] = raw_max[0] - 0.40 - margin / 2  # inside the raw box, inside the margin band
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(q), state(q), now_ns=0)
    assert excinfo.value.rule == "workspace_box"


def test_the_box_is_checked_on_the_clamped_target_not_the_raw_one() -> None:
    """A wild joint value is clamped first, and the clamped pose is what the box sees."""
    env = envelope(fk_linear)
    q = np.zeros(JOINT_DIM)
    q[0] = 99.0  # clamps to the shoulder-pitch limit, which fk_linear puts far outside the box
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(q), state(np.clip(q, env.lower, env.upper)), now_ns=0)
    assert excinfo.value.rule == "workspace_box"


def test_an_envelope_without_fk_fails_closed() -> None:
    # T-011 gave Envelope.from_config() a default fk (runtime.fk.left_arm_fk, exercised in
    # tests/test_fk.py), so the no-fk envelope this asserts about is now built directly.
    env = bare_envelope()
    assert env.fk is None
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(0.0), state(0.0), now_ns=0)
    assert excinfo.value.rule == "workspace_box"
    assert "forward kinematics" in str(excinfo.value)


def test_a_broken_fk_fails_closed() -> None:
    def boom(_q: np.ndarray) -> np.ndarray:
        raise RuntimeError("no solution")

    with pytest.raises(SafetyViolation, match="RuntimeError"):
        envelope(boom).check(command(0.0), state(0.0), now_ns=0)

    with pytest.raises(SafetyViolation, match="expected 3 finite metres"):
        envelope(lambda _q: np.array([0.4, 0.25])).check(command(0.0), state(0.0), now_ns=0)

    with pytest.raises(SafetyViolation, match="expected 3 finite metres"):
        envelope(lambda _q: np.array([np.nan, 0.25, 0.0])).check(command(0.0), state(0.0), now_ns=0)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_non_finite_targets_are_rejected(bad: float) -> None:
    env = envelope()
    q = np.zeros(JOINT_DIM)
    q[2] = bad
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(q), state(0.0), now_ns=0)
    assert excinfo.value.rule == "non_finite"
    with pytest.raises(SafetyViolation) as excinfo:
        env.check(command(0.0, pinch=bad), state(0.0), now_ns=0)
    assert excinfo.value.rule == "non_finite"


def test_pinch_scalar_is_clamped_to_its_range() -> None:
    env = envelope()
    lo, hi = env.pinch_range
    out = env.check(command(0.0, pinch=hi + 5.0), state(0.0), now_ns=0)
    assert out.pinch == hi and "pinch_scalar" in out.clamped
    env.reset()
    out = env.check(command(0.0, pinch=lo - 5.0), state(0.0), now_ns=0)
    assert out.pinch == lo and "pinch_scalar" in out.clamped


def test_pinch_slew_is_rate_limited_against_the_previous_command() -> None:
    env = envelope()
    period_ns = int(round(SECOND_NS / env.command_rate_limit_hz))
    st = state(0.0)
    env.check(command(0.0, pinch=0.0), st, now_ns=0)
    out = env.check(command(0.0, pinch=1.0), st, now_ns=period_ns)
    allowed = env.pinch_rate_limit_per_s * period_ns / SECOND_NS
    assert out.pinch == pytest.approx(allowed)
    assert "pinch_rate" in out.clamped


def test_reset_drops_the_rate_and_velocity_reference() -> None:
    env = envelope()
    env.check(command(0.0), state(0.0), now_ns=0)
    env.reset()
    env.check(command(0.0), state(0.0), now_ns=0)  # would be a rate violation without reset


def test_a_rejected_command_does_not_become_the_new_reference() -> None:
    env = envelope(fk_linear)
    env.check(command(0.0), state(0.0), now_ns=0)
    q = np.zeros(JOINT_DIM)
    q[0] = 0.4
    with pytest.raises(SafetyViolation):
        env.check(command(q), state(q), now_ns=SECOND_NS)
    # The reference is still the accepted command at t=0, so the velocity check runs against it.
    period_ns = int(round(SECOND_NS / env.command_rate_limit_hz))
    out = env.check(command(0.0), state(0.0), now_ns=SECOND_NS + period_ns)
    assert out.clamped == ()


# --------------------------------------------------------------------------------------------------
# Guard: the two together
# --------------------------------------------------------------------------------------------------


def _bad_session_files(tmp_path: Path) -> dict[str, Path]:
    return {
        "absent": tmp_path / "absent.enable",
        "expired": _write_session(
            tmp_path / "expired.enable", enabled_at=datetime.now(BKK) - timedelta(hours=5), seconds=7200
        ),
        "unconfirmed": _write_session(tmp_path / "unconfirmed.enable", checklist="pending"),
        "unparsable": (tmp_path / "unparsable.enable"),
    }


@pytest.mark.parametrize("kind", ["absent", "expired", "unconfirmed", "unparsable"])
def test_guard_on_hardware_refuses_without_a_valid_session(tmp_path: Path, kind: str) -> None:
    paths = _bad_session_files(tmp_path)
    paths["unparsable"].write_text("nonsense\n", encoding="utf-8")
    guard = Guard(SessionGate(paths[kind]), envelope(), simulated=False)
    with pytest.raises(SafetyViolation) as excinfo:
        guard.admit(command(0.0), state(0.0), now_ns=0)
    assert excinfo.value.rule == "session_gate"
    assert guard.admitted == 0


def test_guard_on_hardware_accepts_with_a_valid_session(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "session.enable")
    guard = Guard(SessionGate(path), envelope(), simulated=False)
    out = guard.admit(command(0.1), state(0.1), now_ns=0)
    assert out.clamped == () and guard.admitted == 1


def test_guard_on_hardware_stops_the_moment_the_session_expires(tmp_path: Path) -> None:
    enabled_at = datetime.now(BKK).replace(microsecond=0) - timedelta(seconds=30)
    path = _write_session(tmp_path / "session.enable", enabled_at=enabled_at, seconds=60)
    guard = Guard(SessionGate(path), envelope(), simulated=False)
    guard.admit(command(0.0), state(0.0), now_ns=0)
    _write_session(path, enabled_at=enabled_at - timedelta(hours=3), seconds=60)  # now expired
    with pytest.raises(SafetyViolation) as excinfo:
        guard.admit(command(0.0), state(0.0), now_ns=SECOND_NS)
    assert excinfo.value.rule == "session_gate"


def test_guard_simulated_skips_the_gate_but_never_the_envelope(tmp_path: Path) -> None:
    gate = SessionGate(tmp_path / "absent")  # no session anywhere
    guard = Guard(gate, envelope(fk_linear), simulated=True)

    # accepted without any session file
    assert guard.admit(command(0.0), state(0.0), now_ns=0).clamped == ()

    # ... but an out-of-box fk position is still refused
    guard.envelope.reset()
    q = np.zeros(JOINT_DIM)
    q[0] = 0.4
    with pytest.raises(SafetyViolation) as excinfo:
        guard.admit(command(q), state(q), now_ns=SECOND_NS)
    assert excinfo.value.rule == "workspace_box"

    # ... and an out-of-limit joint is still clamped
    guard.envelope.reset()
    wild = np.zeros(JOINT_DIM)
    wild[3] = 99.0
    at_limit = np.clip(wild, guard.envelope.lower, guard.envelope.upper)
    out = guard.admit(command(wild), state(at_limit), now_ns=2 * SECOND_NS)
    assert out.clamped == ("left_elbow_joint",)
    assert out.arm[3] == pytest.approx(guard.envelope.upper[3])
    assert guard.session_status().valid is False, "simulated must not fake a session"


def test_simulated_is_keyword_only_and_defaults_to_false(tmp_path: Path) -> None:
    gate = SessionGate(tmp_path / "absent")
    with pytest.raises(TypeError):
        Guard(gate, envelope(), True)  # type: ignore[misc]
    assert Guard(gate, envelope()).simulated is False
    assert Guard.from_config(fk_center, session_path=tmp_path / "absent").simulated is False


def test_safety_module_holds_no_environment_bypass() -> None:
    """R1: the only way past the gate is simulated=True. No env var, no flag, no dev mode."""
    source = (REPO_ROOT / "runtime" / "safety.py").read_text(encoding="utf-8")
    for forbidden in ("os.environ", "getenv", "LUDO_", "dev_mode", "force"):
        assert forbidden not in source, f"runtime/safety.py mentions {forbidden!r}"


def test_only_safety_and_enable_session_name_the_session_file() -> None:
    """Acceptance criterion 6: one reader, one writer, plus the tests."""
    allowed = {
        REPO_ROOT / "runtime" / "safety.py",
        REPO_ROOT / "tools" / "hardware_checks" / "enable_session.py",
    }
    skip = {"third_party", ".venv", "data", ".git", "__pycache__", "tests"}
    found = set()
    for path in REPO_ROOT.rglob("*.py"):
        if skip & set(path.relative_to(REPO_ROOT).parts):
            continue
        if "session.enable" in path.read_text(encoding="utf-8"):
            found.add(path)
    assert found == allowed, "exactly one reader and one writer of the session file, outside tests/"


# --------------------------------------------------------------------------------------------------
# tools/hardware_checks/enable_session.py
# --------------------------------------------------------------------------------------------------


def test_enable_session_without_a_tty_exits_2_and_writes_nothing() -> None:
    target = SessionGate().path
    before = (target.exists(), target.stat().st_mtime_ns if target.exists() else None)
    with open("/dev/null", "rb") as devnull:
        proc = subprocess.run(
            [sys.executable, "tools/hardware_checks/enable_session.py"],
            cwd=str(REPO_ROOT),
            stdin=devnull,
            capture_output=True,
            text=True,
            timeout=60,
        )
    after = (target.exists(), target.stat().st_mtime_ns if target.exists() else None)
    assert proc.returncode == 2, proc.stderr
    assert "not a terminal" in proc.stderr
    assert after == before, "enable_session touched the session file without a terminal"


def test_enable_session_writes_the_exact_4_6_format(tmp_path: Path) -> None:
    enabled_at = datetime(2026, 9, 15, 14, 2, 11, tzinfo=BKK)
    text = session_text("Alois", enabled_at, 7200, "confirmed")
    assert text == (
        "enabled_by: Alois\n"
        "enabled_at: 2026-09-15T14:02:11+07:00\n"
        "expires_at: 2026-09-15T16:02:11+07:00\n"
        "checklist: confirmed\n"
    )
    path = tmp_path / "session.enable"
    write_session(path, text)
    assert path.read_text(encoding="utf-8") == text
    assert list(tmp_path.iterdir()) == [path], "the atomic write left a temporary file behind"


def test_a_freshly_written_session_is_accepted_by_the_gate(tmp_path: Path) -> None:
    cfg = config.load("safety")["session"]
    enabled_at = datetime.now(BKK).replace(microsecond=0)
    path = tmp_path / "session.enable"
    write_session(path, session_text("Alois", enabled_at, cfg["default_seconds"], cfg["required_checklist_value"]))
    status = SessionGate(path).status()
    assert status.valid is True, status.reason
    assert status.enabled_by == "Alois"


def test_the_checklist_is_the_four_items_of_section_4_6() -> None:
    assert CHECKLIST == (
        "e-stop within reach",
        "legs locked",
        "workspace clear",
        "humans out of the arm envelope",
    )
