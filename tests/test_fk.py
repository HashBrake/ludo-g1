"""Tests for runtime/fk.py: the forward kinematics the workspace box is checked on (T-011).

Three things are checked, in increasing order of how much they would cost if they were wrong:

1. **The zero pose lands where the XML says it does.** The expected position is computed here by
   chaining the ``pos``/``quat`` attributes of the bodies from ``pelvis`` down to the box point,
   parsed straight out of ``g1_29dof.xml`` with ElementTree and composed with quaternion arithmetic
   written in this file. Nothing in that path touches mujoco, so it catches a wrong base pose, a
   wrong frame, or the wrong body being read.
2. **A second, differently written mujoco evaluation agrees.** mujoco is the only implementation of
   the general case, so the cross-check is a fresh ``MjModel``/``MjData`` (no shared scratch state)
   written in a different address order, over 20 random configurations inside the safety limits.
   That catches stale scratch state, an address written twice, and order dependence.
3. **The envelope actually uses it.** ``Envelope.from_config()`` injects it, an explicit fk still
   overrides it, and ``Guard.admit`` with it rejects a target whose wrist leaves
   ``config/safety.yaml``'s box. (An envelope with *no* fk fails closed; that is
   ``tests/test_safety.py``'s to assert and it still does.)
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from runtime import config, fk
from runtime.safety import Envelope, Guard, SafetyViolation, SessionGate
from runtime.types import ARM_DOF, JOINT_DIM, MotionCommand, RobotState

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
BKK = timezone(timedelta(hours=7))


# --------------------------------------------------------------------------------------------------
# helpers: an independent chain of the MJCF body tree, and the safety limits
# --------------------------------------------------------------------------------------------------


def _quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product of two (w, x, y, z) quaternions, MJCF's convention."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ]
    )


def _quat_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate ``v`` by the unit quaternion ``q``."""
    w, u = q[0], q[1:]
    return v + 2.0 * np.cross(u, np.cross(u, v) + w * v)


def _numbers(text: str | None, default: list[float]) -> np.ndarray:
    return np.array([float(x) for x in text.split()], dtype=np.float64) if text else np.array(default)


def chain_from_xml(point: str) -> np.ndarray:
    """Position of body ``point`` relative to ``pelvis`` with every joint at zero, from the XML alone.

    At zero joint angles every joint contributes the identity rotation, so the answer is the chain of
    the bodies' own ``pos`` and ``quat`` attributes. Read with ElementTree; mujoco is not involved.
    """
    root = ET.parse(fk.MJCF).getroot()
    parent = {child: node for node in root.iter("body") for child in node if child.tag == "body"}
    by_name = {node.get("name"): node for node in root.iter("body")}
    assert point in by_name, f"{point} is not a body of {fk.MJCF.name}"

    path: list[ET.Element] = []
    node: ET.Element | None = by_name[point]
    while node is not None and node.get("name") != "pelvis":
        path.append(node)
        node = parent.get(node)
    assert node is not None, f"{point} is not a descendant of pelvis"

    pos = np.zeros(3)
    quat = np.array([1.0, 0.0, 0.0, 0.0])
    for body in reversed(path):
        pos = pos + _quat_rotate(quat, _numbers(body.get("pos"), [0.0, 0.0, 0.0]))
        quat = _quat_mul(quat, _numbers(body.get("quat"), [1.0, 0.0, 0.0, 0.0]))
    return pos


def safety_limits() -> tuple[np.ndarray, np.ndarray]:
    """The 8 commanded joints' safety limits, in ``action_order``, from config/safety.yaml."""
    env = Envelope.from_config(fk.left_arm_fk)
    return env.lower, env.upper


def mujoco_reference(joints: np.ndarray) -> np.ndarray:
    """A second mujoco evaluation with its own model and data, written in reverse address order."""
    import mujoco

    model = mujoco.MjModel.from_xml_path(str(fk.MJCF))
    data = mujoco.MjData(model)
    robot = config.load("robot")
    entries = list(robot["arm"]["joints"]) + list(robot["waist"]["joints"])[:1]
    addresses = [int(e["mjcf_qpos_index"]) for e in entries]

    data.qpos[:] = model.qpos0
    for i in reversed(range(JOINT_DIM)):  # deliberately not the order runtime/fk.py writes
        data.qpos[addresses[i]] = float(joints[i])
    data.qpos[3:7] = [1.0, 0.0, 0.0, 0.0]
    data.qpos[0:3] = 0.0
    mujoco.mj_kinematics(model, data)
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "left_wrist_yaw_link")
    return np.array(data.xpos[bid], dtype=np.float64)


def session_file(path: Path) -> Path:
    now = datetime.now(BKK).replace(microsecond=0)
    path.write_text(
        f"enabled_by: test\nenabled_at: {now.isoformat()}\n"
        f"expires_at: {(now + timedelta(seconds=3600)).isoformat()}\nchecklist: confirmed\n",
        encoding="utf-8",
    )
    return path


def command(joints: np.ndarray, pinch: float = 0.0) -> MotionCommand:
    return MotionCommand(arm=joints[:ARM_DOF], waist_yaw=float(joints[ARM_DOF]), pinch=pinch)


def state(joints: np.ndarray, pinch: float = 0.0) -> RobotState:
    return RobotState(arm=joints[:ARM_DOF], waist_yaw=float(joints[ARM_DOF]), pinch=pinch, ts_ns=0)


# --------------------------------------------------------------------------------------------------
# what the module computes
# --------------------------------------------------------------------------------------------------


def test_the_model_is_the_vendored_one_and_the_frame_is_the_pelvis() -> None:
    assert fk.MJCF == REPO_ROOT / "third_party" / "unitree_g1_mjcf" / "g1_29dof.xml"
    assert fk.MJCF.is_file()
    assert fk.BOX_FRAME == config.load("safety")["workspace_box_m"]["frame"]
    assert fk.kinematics().point == config.load("safety")["workspace_box_m"]["point"]


def test_zero_pose_matches_the_body_chain_parsed_from_the_mjcf() -> None:
    point = str(config.load("safety")["workspace_box_m"]["point"])
    expected = chain_from_xml(point)
    got = fk.left_arm_fk(np.zeros(ARM_DOF), 0.0)
    print(f"zero-pose {point} in {fk.BOX_FRAME}: {np.round(got, 6).tolist()} m")
    assert got.shape == (3,)
    assert got.dtype == np.float64
    assert np.max(np.abs(got - expected)) < 1e-3, f"fk {got} vs XML chain {expected}"


def test_the_base_is_pinned_so_positions_are_pelvis_relative() -> None:
    """The pelvis body sits at z = 0.76 in the model; the fk must not inherit that world offset."""
    import mujoco

    model = mujoco.MjModel.from_xml_path(str(fk.MJCF))
    assert float(model.qpos0[2]) == pytest.approx(0.76), "the world-frame base height the fk removes"
    assert fk.BASE_QPOS == (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
    assert abs(fk.left_arm_fk(np.zeros(JOINT_DIM))[2]) < 0.5


def test_twenty_random_configurations_agree_with_an_independent_evaluation() -> None:
    lower, upper = safety_limits()
    rng = np.random.default_rng(20260911)
    worst = 0.0
    for _ in range(20):
        q = rng.uniform(lower, upper)
        got = fk.left_arm_fk(q[:ARM_DOF], float(q[ARM_DOF]))
        worst = max(worst, float(np.max(np.abs(got - mujoco_reference(q)))))
    print(f"worst disagreement over 20 random configurations: {worst:.3e} m")
    assert worst < 1e-9


def test_the_two_call_shapes_are_the_same_function() -> None:
    rng = np.random.default_rng(7)
    lower, upper = safety_limits()
    q = rng.uniform(lower, upper)
    assert np.array_equal(fk.left_arm_fk(q[:ARM_DOF], float(q[ARM_DOF])), fk.left_arm_fk(q))


def test_the_wrist_moves_when_the_waist_yaws() -> None:
    straight = fk.left_arm_fk(np.zeros(ARM_DOF), 0.0)
    yawed = fk.left_arm_fk(np.zeros(ARM_DOF), 0.5)
    assert np.linalg.norm(yawed - straight) > 0.01, "waist yaw must carry the whole arm"
    # A yaw about the pelvis z axis: the height is unchanged and the radius is preserved.
    assert yawed[2] == pytest.approx(straight[2], abs=1e-9)
    assert np.linalg.norm(yawed[:2]) == pytest.approx(np.linalg.norm(straight[:2]), abs=1e-9)


def test_every_commanded_joint_reaches_the_point() -> None:
    """Each of the 8 joints is actually wired to the wrist, and none of them is written twice.

    Two of them cannot move the wrist-yaw *origin* however far they turn: wrist yaw rotates that
    frame about itself, and wrist roll turns about the x axis that the remaining 0.038 + 0.046 m of
    the chain lies along. Both still move the wrist's orientation, which is why the tool offset to
    the fingertip is a Phase 1 measurement (docs/safety.md).
    """
    spin_in_place = {"left_wrist_roll_joint", "left_wrist_yaw_joint"}
    base = fk.left_arm_fk(np.zeros(JOINT_DIM))
    for i, name in enumerate(fk.kinematics().names):
        q = np.zeros(JOINT_DIM)
        q[i] = 0.3
        moved = float(np.linalg.norm(fk.left_arm_fk(q) - base))
        if name in spin_in_place:
            assert moved < 1e-9, f"{name} moved the wrist origin by {moved} m"
        else:
            assert moved > 1e-3, f"{name} moved the wrist origin by only {moved} m"


def test_repeated_calls_are_deterministic_and_do_not_leak_state() -> None:
    lower, upper = safety_limits()
    rng = np.random.default_rng(3)
    a, b = rng.uniform(lower, upper), rng.uniform(lower, upper)
    first = fk.left_arm_fk(a)
    for _ in range(5):
        fk.left_arm_fk(b)
        assert np.array_equal(fk.left_arm_fk(a), first)


def test_the_returned_array_is_a_fresh_copy() -> None:
    out = fk.left_arm_fk(np.zeros(JOINT_DIM))
    out[:] = 99.0
    assert np.max(np.abs(fk.left_arm_fk(np.zeros(JOINT_DIM)))) < 99.0


def test_the_model_is_compiled_once_and_then_reused() -> None:
    first = fk.kinematics()
    fk.left_arm_fk(np.zeros(JOINT_DIM))
    assert fk.kinematics() is first, "compiling the MJCF per call would cost ~0.2 s of every command"


@pytest.mark.parametrize(
    "args",
    [(np.zeros(6), 0.0), (np.zeros(8), 0.0), (np.zeros(7),), (np.zeros(9),)],
)
def test_a_wrong_sized_input_is_refused(args: tuple) -> None:
    with pytest.raises(ValueError, match="wants"):
        fk.left_arm_fk(*args)


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_a_non_finite_joint_is_refused(bad: float) -> None:
    q = np.zeros(JOINT_DIM)
    q[2] = bad
    with pytest.raises(ValueError, match="non-finite"):
        fk.left_arm_fk(q)


def test_call_time_is_fast_enough_for_the_control_loop() -> None:
    """The envelope calls this on every command; at 60 Hz it must cost far less than a period."""
    q = np.zeros(JOINT_DIM)
    fk.left_arm_fk(q)
    start = time.perf_counter()
    for _ in range(1000):
        fk.left_arm_fk(q)
    mean_us = (time.perf_counter() - start) / 1000 * 1e6
    print(f"left_arm_fk mean call time over 1000 calls: {mean_us:.1f} us")
    assert mean_us < 1000.0


# --------------------------------------------------------------------------------------------------
# the envelope uses it
# --------------------------------------------------------------------------------------------------


def test_from_config_injects_the_real_fk_by_default() -> None:
    env = Envelope.from_config()
    assert env.fk is fk.left_arm_fk
    assert np.array_equal(env.fk(np.zeros(JOINT_DIM)), fk.left_arm_fk(np.zeros(JOINT_DIM)))


def test_an_explicit_fk_still_overrides_the_default() -> None:
    mock = Envelope.from_config(lambda _q: np.array([0.4, 0.25, -0.05]))
    assert mock.fk is not fk.left_arm_fk


def test_guard_with_the_real_fk_rejects_a_target_whose_wrist_leaves_the_box(tmp_path: Path) -> None:
    """Shoulder pitch back and the elbow open puts the wrist behind the box's x minimum."""
    guard = Guard(SessionGate(session_file(tmp_path / "session.enable")), Envelope.from_config())
    assert guard.session_status().valid

    outside = np.zeros(JOINT_DIM)
    outside[0] = -2.5  # shoulder pitch: swings the wrist up and behind the chest
    point = fk.left_arm_fk(outside)
    assert np.any(point < guard.envelope.box_min) or np.any(point > guard.envelope.box_max), (
        f"the test target must be outside the box to prove anything; it is at {point}"
    )
    with pytest.raises(SafetyViolation) as excinfo:
        guard.admit(command(outside), state(outside), now_ns=0)
    assert excinfo.value.rule == "workspace_box"
    assert "left_wrist_yaw_link" in str(excinfo.value)
    assert guard.admitted == 0


def test_guard_with_the_real_fk_admits_a_target_whose_wrist_is_inside_the_box(tmp_path: Path) -> None:
    guard = Guard(SessionGate(session_file(tmp_path / "session.enable")), Envelope.from_config())
    inside = np.zeros(JOINT_DIM)
    point = fk.left_arm_fk(inside)
    assert np.all(point >= guard.envelope.box_min) and np.all(point <= guard.envelope.box_max)
    out = guard.admit(command(inside), state(inside), now_ns=0)
    assert out.clamped == ()
    assert guard.admitted == 1


def test_where_the_all_zero_pose_sits_relative_to_the_placeholder_box() -> None:
    """A recorded fact for the envelope review, not a requirement: see docs/safety.md and BUILD_LOG."""
    env = Envelope.from_config()
    point = fk.left_arm_fk(np.zeros(JOINT_DIM))
    inside = bool(np.all(point >= env.box_min) and np.all(point <= env.box_max))
    print(
        f"all-zero pose: wrist at {np.round(point, 6).tolist()} m, box "
        f"{env.box_min.tolist()} .. {env.box_max.tolist()} -> {'INSIDE' if inside else 'OUTSIDE'}"
    )
    assert inside, (
        "docs/safety.md and agents/BUILD_LOG.md record that the all-zero pose is inside the current "
        "placeholder box; if this fails the box changed and both must be re-checked"
    )
