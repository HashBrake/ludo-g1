"""Tests for teleop/retarget.py: the 8-DoF arm IK, the Pico frame transform and the pinch scalar.

The IK is checked against targets that are reachable **by construction**: each one is the forward
kinematics of a random joint configuration drawn inside the ``config/safety.yaml`` joint limits whose
wrist lands inside the workspace box, so a failure is the solver's, never the target's. Position is
cross-checked against :func:`runtime.fk.left_arm_fk` -- the function the safety envelope checks the
box with -- so the IK and the envelope cannot quietly disagree about where the wrist is.

What is asserted, in the order it would hurt if it were wrong:

1. every solved configuration is inside the safety joint limits and the waist clamp (R3);
2. no joint the project never commands, and no floating-base dof, moves at all;
3. every solver step moves every joint by at most ``joint_velocity_limit_rad_s * step_dt_s``;
4. at least 90% of 50 cold targets are reached within 5 mm and 3 degrees in at most 30 iterations;
5. warm tracking -- the teleop case, target a few mm from the last solution -- is fast enough to run
   inside a 30 Hz loop.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from runtime import config, fk
from runtime.types import JOINT_DIM
from teleop.retarget import ArmIK, IkResult, pico_to_g1_base, pinch_from_glove

TARGET_COUNT = 50
REQUIRED_PASS_RATE = 0.90
POSITION_TOLERANCE_M = 0.005
ORIENTATION_TOLERANCE_DEG = 3.0


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ik() -> ArmIK:
    return ArmIK()


def angle_between(quat_a: np.ndarray, quat_b: np.ndarray) -> float:
    """Angle in degrees of the rotation taking ``quat_a`` to ``quat_b``; both xyzw, both unit."""
    dot = min(abs(float(np.dot(quat_a, quat_b))), 1.0)
    return float(np.degrees(2.0 * np.arccos(dot)))


Target = tuple[np.ndarray, np.ndarray, np.ndarray]


def reachable_targets(ik: ArmIK, count: int = TARGET_COUNT, seed: int = 0) -> list[Target]:
    """``count`` ``(joints, position_m, quat_xyzw)`` triples, each an achievable wrist pose.

    Rejection sampling: draw the 8 joints uniformly inside the safety limits, keep the draw if its
    wrist lands inside the workspace box with the configured margin removed. The pose is that draw's
    own forward kinematics, so a solution exists.
    """
    box = config.load("safety")["workspace_box_m"]
    margin = float(box["margin_m"])
    low = np.asarray(box["min"], dtype=np.float64) + margin
    high = np.asarray(box["max"], dtype=np.float64) - margin
    rng = np.random.default_rng(seed)
    out: list[Target] = []
    while len(out) < count:
        joints = rng.uniform(ik.lower, ik.upper)
        position, quat = ik.fk_pose(joints)
        if np.all(position >= low) and np.all(position <= high):
            out.append((joints, position, quat))
    return out


@pytest.fixture(scope="module")
def targets(ik: ArmIK) -> list[Target]:
    return reachable_targets(ik)


@pytest.fixture(scope="module")
def cold_solutions(ik: ArmIK, targets: list) -> list[tuple[IkResult, float, float]]:
    """One cold solve per target from the rest pose: ``(result, position error m, angle error deg)``."""
    out = []
    for _, position, quat in targets:
        result = ik.solve_detailed(position, quat, ik.rest, record_steps=True)
        got_position, got_quat = ik.fk_pose(result.joints)
        out.append((result, float(np.linalg.norm(got_position - position)), angle_between(quat, got_quat)))
    return out


def config_copy(tmp_path: Path) -> Path:
    """A writable copy of ``config/`` for tests that need a different value than the repo's."""
    root = tmp_path / "config"
    shutil.copytree(config.CONFIG_DIR, root)
    return root


# --------------------------------------------------------------------------------------------------
# the IK agrees with the kinematics the envelope uses
# --------------------------------------------------------------------------------------------------


def test_fk_pose_position_is_the_one_runtime_fk_checks_the_box_with(ik: ArmIK, targets: list):
    for joints, position, _ in targets[:10]:
        assert np.allclose(ik.fk_pose(joints)[0], fk.left_arm_fk(joints), atol=1e-12)
        assert np.allclose(position, fk.left_arm_fk(joints), atol=1e-12)


def test_the_ik_targets_the_point_the_workspace_box_is_checked_on(ik: ArmIK):
    assert ik.point == config.load("safety")["workspace_box_m"]["point"]
    assert ik.names == tuple(config.load("robot")["action_order"][:JOINT_DIM])


def test_joint_limits_are_the_safety_limits_with_the_waist_clamp_applied(ik: ArmIK):
    safety = config.load("safety")
    clamp = float(safety["waist_yaw_clamp_rad"])
    for i, name in enumerate(ik.names):
        low, high = safety["joint_limits_rad"][name]
        expected = (max(low, -clamp), min(high, clamp)) if name == "waist_yaw_joint" else (low, high)
        assert (ik.lower[i], ik.upper[i]) == pytest.approx(expected)
    assert ik.upper[ik.names.index("waist_yaw_joint")] == pytest.approx(clamp)


# --------------------------------------------------------------------------------------------------
# acceptance: 50 reachable targets, cold, from the rest pose
# --------------------------------------------------------------------------------------------------


def test_fifty_reachable_targets_are_reached_within_5_mm_and_3_degrees(cold_solutions: list, capsys):
    passed = [
        error_m <= POSITION_TOLERANCE_M and error_deg <= ORIENTATION_TOLERANCE_DEG
        for _, error_m, error_deg in cold_solutions
    ]
    rate = sum(passed) / len(passed)
    errors_mm = np.array([e * 1e3 for _, e, _ in cold_solutions])
    errors_deg = np.array([e for _, _, e in cold_solutions])
    with capsys.disabled():
        print(
            f"\nArmIK cold solve, {len(passed)} reachable targets from the rest pose: "
            f"pass rate {rate:.0%} ({sum(passed)}/{len(passed)}) "
            f"within {POSITION_TOLERANCE_M * 1e3:g} mm and {ORIENTATION_TOLERANCE_DEG:g} deg; "
            f"position error median {np.median(errors_mm):.3f} mm / p90 {np.percentile(errors_mm, 90):.3f} mm, "
            f"orientation error median {np.median(errors_deg):.3f} deg / p90 {np.percentile(errors_deg, 90):.3f} deg"
        )
    assert rate >= REQUIRED_PASS_RATE, f"pass rate {rate:.0%} is below the required {REQUIRED_PASS_RATE:.0%}"


def test_no_cold_solve_exceeds_the_configured_iteration_budget(ik: ArmIK, cold_solutions: list):
    budget = int(config.load("robot")["teleop"]["ik"]["max_iters"])
    assert budget == 30
    assert max(result.iterations for result, _, _ in cold_solutions) <= budget


def test_every_solution_is_inside_the_safety_joint_limits(ik: ArmIK, cold_solutions: list):
    for result, _, _ in cold_solutions:
        assert np.all(result.joints >= ik.lower) and np.all(result.joints <= ik.upper)
        for step in result.steps:
            assert np.all(step >= ik.lower - 1e-9) and np.all(step <= ik.upper + 1e-9)


def test_every_step_respects_the_configured_joint_velocity_limit(ik: ArmIK, cold_solutions: list):
    dt = float(config.load("robot")["teleop"]["ik"]["step_dt_s"])
    budget = ik.velocity_limit_rad_s * dt
    assert ik.velocity_limit_rad_s == pytest.approx(config.load("safety")["joint_velocity_limit_rad_s"])
    worst = 0.0
    for result, _, _ in cold_solutions:
        steps = np.asarray(result.steps)
        assert len(steps) == result.iterations + 1
        if result.iterations:
            worst = max(worst, float(np.abs(np.diff(steps, axis=0)).max()))
    assert worst <= budget + 1e-9, f"largest step {worst:.6f} rad exceeds {budget:.6f} rad"
    assert worst > 0.5 * budget, "the velocity limit never bound; the test is not exercising it"


def test_no_uncommanded_joint_and_no_base_dof_ever_moves(ik: ArmIK, targets: list):
    """The zero velocity limits hold every other dof still; only QP round-off gets through."""
    seed = ik._full_qpos(ik.rest)
    frozen = np.setdiff1d(np.arange(ik.model.nq), ik.qadr)
    worst = 0.0
    for _, position, quat in targets[:10]:
        ik.solve(position, quat, ik.rest)
        worst = max(worst, float(np.abs(ik.full_qpos[frozen] - seed[frozen]).max()))
    assert worst < 1e-9, f"a frozen dof moved by {worst:.3e}"
    assert ik.full_qpos[0:7] == pytest.approx(np.asarray(fk.BASE_QPOS), abs=1e-9)


# --------------------------------------------------------------------------------------------------
# acceptance: solve time
# --------------------------------------------------------------------------------------------------


def test_warm_solve_is_fast_enough_for_a_30_hz_teleop_loop(ik: ArmIK, targets: list, capsys):
    """The teleop case: the target moves a few mm per tick and the solver is warm-started.

    Also reports the cold-start time from the rest pose, which is the worst case and is not what the
    30 Hz loop pays.
    """
    rng = np.random.default_rng(1)
    warm: list[float] = []
    for _, position, quat in targets:
        joints = ik.solve(position, quat, ik.rest)
        for _ in range(5):
            position = position + rng.uniform(-0.003, 0.003, size=3)
            started = time.perf_counter()
            joints = ik.solve(position, quat, joints)
            warm.append(time.perf_counter() - started)

    cold: list[float] = []
    for _, position, quat in targets:
        started = time.perf_counter()
        ik.solve(position, quat, ik.rest)
        cold.append(time.perf_counter() - started)

    mean_warm_ms = float(np.mean(warm)) * 1e3
    with capsys.disabled():
        print(
            f"\nArmIK solve time on this laptop: warm (tracking, {len(warm)} calls) "
            f"mean {mean_warm_ms:.3f} ms, p99 {np.percentile(warm, 99) * 1e3:.3f} ms; "
            f"cold from the rest pose ({len(cold)} calls) mean {np.mean(cold) * 1e3:.3f} ms, "
            f"max {np.max(cold) * 1e3:.3f} ms"
        )
    assert mean_warm_ms < 5.0, f"mean warm solve {mean_warm_ms:.3f} ms is over the 5 ms budget"


# --------------------------------------------------------------------------------------------------
# behaviour at the edges
# --------------------------------------------------------------------------------------------------


def test_an_unreachable_target_is_reported_not_faked(ik: ArmIK):
    result = ik.solve_detailed(np.array([2.0, 0.0, 0.0]), np.array([0.0, 0.0, 0.0, 1.0]), ik.rest)
    assert not result.converged
    assert result.position_error_m > POSITION_TOLERANCE_M
    assert np.all(result.joints >= ik.lower) and np.all(result.joints <= ik.upper)


def test_a_seed_outside_the_limits_is_clamped_before_solving(ik: ArmIK, targets: list):
    _, position, quat = targets[0]
    joints = ik.solve(position, quat, ik.upper + 10.0)
    assert np.all(joints >= ik.lower) and np.all(joints <= ik.upper)


def test_an_already_solved_target_costs_no_iterations(ik: ArmIK, targets: list):
    joints, position, quat = targets[0]
    result = ik.solve_detailed(position, quat, joints)
    assert result.iterations == 0 and result.converged


@pytest.mark.parametrize(
    "position, quat, current",
    [
        (np.zeros(2), np.array([0.0, 0.0, 0.0, 1.0]), np.zeros(JOINT_DIM)),
        (np.zeros(3), np.zeros(3), np.zeros(JOINT_DIM)),
        (np.zeros(3), np.array([0.0, 0.0, 0.0, 1.0]), np.zeros(JOINT_DIM - 1)),
        (np.array([np.nan, 0.0, 0.0]), np.array([0.0, 0.0, 0.0, 1.0]), np.zeros(JOINT_DIM)),
        (np.zeros(3), np.zeros(4), np.zeros(JOINT_DIM)),
    ],
)
def test_solve_rejects_a_malformed_argument(ik: ArmIK, position, quat, current):
    with pytest.raises(ValueError):
        ik.solve(position, quat, current)


def test_fk_pose_rejects_the_wrong_number_of_joints(ik: ArmIK):
    with pytest.raises(ValueError, match="action_order"):
        ik.fk_pose(np.zeros(JOINT_DIM + 1))


def test_a_rest_pose_outside_the_safety_limits_is_refused(tmp_path: Path):
    root = config_copy(tmp_path)
    text = (root / "robot.yaml").read_text(encoding="utf-8")
    (root / "robot.yaml").write_text(
        text.replace(
            "rest_pose_rad: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]",
            "rest_pose_rad: [9.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]",
        ),
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError, match="rest_pose_rad"):
        ArmIK(root=root)


# --------------------------------------------------------------------------------------------------
# pico_to_g1_base
# --------------------------------------------------------------------------------------------------


def test_the_placeholder_transform_is_the_identity_and_says_so():
    assert "teleop.pico_to_pelvis" in config.unmeasured("robot")
    position, quat = pico_to_g1_base(np.array([0.3, 0.2, 0.1]), np.array([0.0, 0.0, 0.0, 1.0]))
    assert position == pytest.approx([0.3, 0.2, 0.1])
    assert quat == pytest.approx([0.0, 0.0, 0.0, 1.0])


def test_a_real_transform_rotates_and_translates_both_halves_of_the_pose(tmp_path: Path):
    """A 90 degree yaw plus a 1 m x offset: +x_pico becomes +y_pelvis, and the rotation composes."""
    root = config_copy(tmp_path)
    text = (root / "robot.yaml").read_text(encoding="utf-8")
    (root / "robot.yaml").write_text(
        text.replace(
            "    - [1.0, 0.0, 0.0, 0.0]\n    - [0.0, 1.0, 0.0, 0.0]",
            "    - [0.0, -1.0, 0.0, 1.0]\n    - [1.0, 0.0, 0.0, 0.0]",
        ),
        encoding="utf-8",
    )
    position, quat = pico_to_g1_base(np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 0.0, 1.0]), root=root)
    assert position == pytest.approx([1.0, 1.0, 0.0])
    # A 90 degree rotation about +z is (0, 0, sin 45, cos 45) in xyzw.
    assert quat == pytest.approx([0.0, 0.0, np.sqrt(0.5), np.sqrt(0.5)])


def test_a_malformed_transform_is_a_config_error(tmp_path: Path):
    root = config_copy(tmp_path)
    text = (root / "robot.yaml").read_text(encoding="utf-8")
    (root / "robot.yaml").write_text(text.replace("    - [0.0, 0.0, 0.0, 1.0]\n", "", 1), encoding="utf-8")
    with pytest.raises(config.ConfigError, match="pico_to_pelvis"):
        pico_to_g1_base(np.zeros(3), np.array([0.0, 0.0, 0.0, 1.0]), root=root)


@pytest.mark.parametrize(
    "position, quat",
    [(np.zeros(2), np.array([0.0, 0.0, 0.0, 1.0])), (np.zeros(3), np.zeros(3)), (np.zeros(3), np.zeros(4))],
)
def test_pico_to_g1_base_rejects_a_malformed_pose(position, quat):
    with pytest.raises(ValueError):
        pico_to_g1_base(position, quat)


# --------------------------------------------------------------------------------------------------
# pinch_from_glove
# --------------------------------------------------------------------------------------------------


def test_the_pinch_scalar_spans_the_operators_calibration_bounds():
    glove = config.load("hand")["glove"]
    open_m, closed_m = glove["open_distance_mm"] / 1e3, glove["closed_distance_mm"] / 1e3
    assert pinch_from_glove(open_m) == pytest.approx(0.0)
    assert pinch_from_glove(closed_m) == pytest.approx(1.0)
    assert pinch_from_glove(0.5 * (open_m + closed_m)) == pytest.approx(0.5)


def test_the_pinch_scalar_is_clamped_to_the_configured_range():
    low, high = config.load("hand")["pinch"]["scalar_range"]
    assert pinch_from_glove(10.0) == pytest.approx(low)
    assert pinch_from_glove(-10.0) == pytest.approx(high)
    assert all(low <= pinch_from_glove(d) <= high for d in np.linspace(-0.1, 0.5, 61))


def test_the_pinch_bounds_are_still_placeholders():
    unmeasured = config.unmeasured("hand")
    assert "glove.open_distance_mm" in unmeasured and "glove.closed_distance_mm" in unmeasured


def test_pinch_from_glove_rejects_a_non_finite_distance():
    with pytest.raises(ValueError, match="non-finite"):
        pinch_from_glove(float("nan"))


def test_identical_calibration_bounds_are_a_config_error(tmp_path: Path):
    root = config_copy(tmp_path)
    text = (root / "hand.yaml").read_text(encoding="utf-8")
    text = text.replace("closed_distance_mm: 10.0", "closed_distance_mm: 90.0")
    (root / "hand.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(config.ConfigError, match="must differ"):
        pinch_from_glove(0.05, root=root)
