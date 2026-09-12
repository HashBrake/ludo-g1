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

T-043 added the second checked point, the DexH15 fingertip (:data:`runtime.fk.PINCH_POINT`), and the
last section of this file is about it: it is the wrist frame offset by ``config/robot.yaml``
``tool.pinch_offset_m`` **rotated** by the wrist body's orientation, cross-checked here against a
quaternion evaluation that never touches ``xmat``; it moves when the wrist rolls, pitches or yaws,
which the wrist origin does not (D-010); and a pose whose wrist is inside the box but whose fingertip
is outside is refused, naming the fingertip.
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


def mujoco_reference_pinch(joints: np.ndarray, offset: np.ndarray) -> np.ndarray:
    """The pinch point from a fresh model, rotating the offset with ``xquat`` instead of ``xmat``.

    ``runtime/fk.py`` composes ``xpos + xmat @ offset``. Here the same point is built from the body
    *quaternion* and the Hamilton-product rotation written at the top of this file, so nothing but
    mujoco's own kinematics is shared with the implementation.
    """
    import mujoco

    model = mujoco.MjModel.from_xml_path(str(fk.MJCF))
    data = mujoco.MjData(model)
    robot = config.load("robot")
    entries = list(robot["arm"]["joints"]) + list(robot["waist"]["joints"])[:1]

    data.qpos[:] = model.qpos0
    for i in reversed(range(JOINT_DIM)):
        data.qpos[int(entries[i]["mjcf_qpos_index"])] = float(joints[i])
    data.qpos[3:7] = [1.0, 0.0, 0.0, 0.0]
    data.qpos[0:3] = 0.0
    mujoco.mj_kinematics(model, data)
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "left_wrist_yaw_link")
    return np.array(data.xpos[bid], dtype=np.float64) + _quat_rotate(np.array(data.xquat[bid]), np.asarray(offset))


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
    the chain lies along. Both still move the wrist's *orientation*, so both move the fingertip
    pinch point, which is why that point is checked too (T-043, D-010, and the last section here).
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
    """The default is the multi-point fk, and its wrist entry is what ``left_arm_fk`` returns."""
    env = Envelope.from_config()
    assert env.fk is fk.left_arm_points
    points = env.fk(np.zeros(JOINT_DIM))
    assert tuple(points) == fk.kinematics().names_out
    assert np.array_equal(points[env.box_point], fk.left_arm_fk(np.zeros(JOINT_DIM)))


def test_an_explicit_fk_still_overrides_the_default() -> None:
    mock = Envelope.from_config(lambda _q: np.array([0.4, 0.25, -0.05]))
    assert mock.fk is not fk.left_arm_points


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


# --------------------------------------------------------------------------------------------------
# the second checked point: the DexH15 fingertip (T-043, D-010)
# --------------------------------------------------------------------------------------------------


def tool_offset() -> np.ndarray:
    return np.asarray(config.load("robot")["tool"]["pinch_offset_m"], dtype=np.float64)


def test_the_pinch_offset_is_read_from_the_config_and_is_still_a_placeholder() -> None:
    """The offset is configuration, not a constant in code (section 7), and T-022 measures it."""
    assert np.array_equal(fk.kinematics().tool_offset, tool_offset())
    assert tool_offset().shape == (3,)
    assert "tool.pinch_offset_m" in config.unmeasured("robot")


def test_left_arm_points_returns_exactly_the_points_the_box_is_checked_at() -> None:
    box = config.load("safety")["workspace_box_m"]
    points = fk.left_arm_points(np.zeros(ARM_DOF), 0.0)
    assert tuple(points) == (str(box["point"]), fk.PINCH_POINT) == fk.kinematics().names_out
    assert set(str(p) for p in box["points"]) <= set(points), "every configured point must be produced"
    for name, value in points.items():
        assert value.shape == (3,) and value.dtype == np.float64, name


def test_the_wrist_entry_is_left_arm_fk_unchanged() -> None:
    """T-043 must not have moved the wrist point: teleop/retarget.py's IK is checked against it."""
    lower, upper = safety_limits()
    rng = np.random.default_rng(43)
    for _ in range(10):
        q = rng.uniform(lower, upper)
        assert np.array_equal(fk.left_arm_points(q)[fk.kinematics().point], fk.left_arm_fk(q))
        assert np.array_equal(fk.left_arm_points(q[:ARM_DOF], float(q[ARM_DOF]))[fk.kinematics().point],
                              fk.left_arm_fk(q[:ARM_DOF], float(q[ARM_DOF])))


def test_the_pinch_point_agrees_with_a_quaternion_evaluation_over_random_configurations() -> None:
    """``xpos + xmat @ offset`` against ``xpos + quat_rotate(xquat, offset)`` on a fresh model."""
    lower, upper = safety_limits()
    offset = tool_offset()
    rng = np.random.default_rng(20260912)
    worst = 0.0
    for _ in range(20):
        q = rng.uniform(lower, upper)
        got = fk.left_arm_points(q)[fk.PINCH_POINT]
        worst = max(worst, float(np.max(np.abs(got - mujoco_reference_pinch(q, offset)))))
    print(f"worst pinch-point disagreement over 20 random configurations: {worst:.3e} m")
    assert worst < 1e-9


def test_the_pinch_point_is_the_offset_expressed_in_the_wrist_frame_not_the_pelvis_frame() -> None:
    """Rotated, not added: at a rolled wrist the offset is not parallel to the pelvis axes.

    Adding the offset in the pelvis frame would leave the pinch point a fixed vector from the wrist
    in *every* pose, which is exactly the bug that would make wrist roll invisible again.
    """
    offset = tool_offset()
    q = np.zeros(JOINT_DIM)
    q[4] = 1.0  # wrist roll
    points = fk.left_arm_points(q)
    delta = points[fk.PINCH_POINT] - points[fk.kinematics().point]
    assert float(np.linalg.norm(delta)) == pytest.approx(float(np.linalg.norm(offset)), abs=1e-9)
    assert float(np.max(np.abs(delta - offset))) > 0.01, "the offset was added, not rotated"


def test_every_wrist_joint_moves_the_pinch_point_by_more_than_a_centimetre(capsys) -> None:
    """D-010's gap closed: wrist roll and yaw move nothing at the wrist origin and the fingertip.

    0.3 rad on one joint at a time from the all-zero pose. The threshold is 10 mm, which is about
    the width of the pinch on a horse: a joint that moves the checked point less than that is a
    joint the box still cannot see.
    """
    base = fk.left_arm_points(np.zeros(JOINT_DIM))
    wrist_joints = {"left_wrist_roll_joint", "left_wrist_pitch_joint", "left_wrist_yaw_joint"}
    moved: dict[str, tuple[float, float]] = {}
    for i, name in enumerate(fk.kinematics().names):
        q = np.zeros(JOINT_DIM)
        q[i] = 0.3
        points = fk.left_arm_points(q)
        moved[name] = (
            float(np.linalg.norm(points[fk.kinematics().point] - base[fk.kinematics().point])),
            float(np.linalg.norm(points[fk.PINCH_POINT] - base[fk.PINCH_POINT])),
        )
    with capsys.disabled():
        print(f"\n0.3 rad on one joint, offset {tool_offset().tolist()} m: displacement of each checked point")
        for name, (wrist, pinch) in moved.items():
            print(f"  {name:28s} wrist {wrist * 1e3:7.2f} mm   pinch_point {pinch * 1e3:7.2f} mm")
    for name in wrist_joints:
        assert moved[name][1] > 0.01, f"{name} moves the pinch point only {moved[name][1] * 1e3:.2f} mm"
    assert moved["left_wrist_roll_joint"][0] < 1e-9 and moved["left_wrist_yaw_joint"][0] < 1e-9
    for name, (_wrist, pinch) in moved.items():
        assert pinch > 0.01, f"{name} moves the pinch point only {pinch * 1e3:.2f} mm"


def test_the_returned_points_are_fresh_copies() -> None:
    points = fk.left_arm_points(np.zeros(JOINT_DIM))
    for value in points.values():
        value[:] = 99.0
    again = fk.left_arm_points(np.zeros(JOINT_DIM))
    assert max(float(np.max(np.abs(v))) for v in again.values()) < 99.0


@pytest.mark.parametrize("args", [(np.zeros(6), 0.0), (np.zeros(8), 0.0), (np.zeros(7),), (np.zeros(9),)])
def test_left_arm_points_refuses_a_wrong_sized_input(args: tuple) -> None:
    with pytest.raises(ValueError, match="left_arm_points"):
        fk.left_arm_points(*args)


def test_left_arm_points_refuses_a_non_finite_joint() -> None:
    q = np.zeros(JOINT_DIM)
    q[2] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        fk.left_arm_points(q)


def test_a_configured_point_the_fk_cannot_produce_is_a_config_error(monkeypatch) -> None:
    """A typo in ``workspace_box_m.points`` must fail at build, not silently check fewer points."""
    real = config.load

    def patched(name: str, root=None):
        data = real(name, root)
        if name == "safety":
            data["workspace_box_m"]["points"] = ["left_wrist_yaw_link", "fingertip_typo"]
        return data

    monkeypatch.setattr(fk.config, "load", patched)
    with pytest.raises(config.ConfigError, match="fingertip_typo"):
        fk._Kinematics()


@pytest.mark.parametrize("offset", [None, [0.12, 0.0], "0.12", [0.12, 0.0, float("nan")]])
def test_a_missing_or_malformed_tool_offset_is_a_config_error(monkeypatch, offset) -> None:
    real = config.load

    def patched(name: str, root=None):
        data = real(name, root)
        if name == "robot":
            if offset is None:
                data.pop("tool")
            else:
                data["tool"]["pinch_offset_m"] = offset
        return data

    monkeypatch.setattr(fk.config, "load", patched)
    with pytest.raises(config.ConfigError, match="tool.pinch_offset_m"):
        fk._Kinematics()


def test_guard_refuses_a_pose_whose_wrist_is_inside_the_box_but_whose_fingertip_is_not(
    tmp_path: Path, capsys
) -> None:
    """The whole point of T-043: shoulder pitch back tips the hand over the box's z ceiling.

    The wrist origin is still comfortably inside; the fingertip, 120 mm out and 50 mm down the wrist
    frame, is not. Before T-043 this command was admitted.
    """
    guard = Guard(SessionGate(session_file(tmp_path / "session.enable")), Envelope.from_config())
    env = guard.envelope
    q = np.zeros(JOINT_DIM)
    q[0] = -0.70  # shoulder pitch: swings the forearm up and the hand over the top of the box
    points = fk.left_arm_points(q)
    wrist, pinch = points[env.box_point], points[fk.PINCH_POINT]
    with capsys.disabled():
        print(f"\nwrist-in / fingertip-out pose (shoulder pitch {q[0]} rad): wrist {np.round(wrist, 4).tolist()} m "
              f"INSIDE, pinch_point {np.round(pinch, 4).tolist()} m outside "
              f"{env.box_min.tolist()}..{env.box_max.tolist()}")
    assert np.all(wrist >= env.box_min) and np.all(wrist <= env.box_max), "the wrist must be inside to prove anything"
    with pytest.raises(SafetyViolation) as excinfo:
        guard.admit(command(q), state(q), now_ns=0)
    assert excinfo.value.rule == "workspace_box"
    assert "pinch_point" in str(excinfo.value)
    assert "on axis z by" in str(excinfo.value)
    assert guard.admitted == 0

    # ... and the same pose is admitted by an envelope that only knows the wrist, which is what the
    # box did before T-043. This is the tightening, measured.
    wrist_only = Envelope.from_config(fk.left_arm_fk)
    assert wrist_only.check(command(q), state(q), now_ns=0).clamped == ()
