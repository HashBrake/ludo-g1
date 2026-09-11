"""Teleop loop tests (T-032; CLAUDE.md 5.2, 5.3, Phase 2, R1, R2, R3, R5).

Everything runs on the mock drivers and a :class:`FakeClock` the loop's injected ``sleep_until``
jumps forward, so a 30 s teleop session costs no wall-clock seconds of waiting and the tick grid is
exactly the one a real 30 s run would have produced. Nothing here is marked ``motion``: the mocks are
simulated robots, which R1 exempts from the session gate and from nothing else -- every command still
goes through :meth:`runtime.safety.Guard.admit`, and two tests count exactly that.

The operator's path is the mock Pico circle (``config/robot.yaml`` ``mock.pose_*``). Three
configurations of that one mock are used, and they are the whole point of the file:

* :func:`engage_center` -- the circle drawn through the wrist pose of the arm's own rest pose, so
  the operator's hand starts where the robot's already is. This is the session that works: the
  clutch engages, the guard admits, the arm tracks.
* :data:`REACHABLE` -- the circle centred inside the workspace box but 0.44 rad of joint travel away
  from the rest pose. Reachable, but not somewhere the arm may jump to: T-032 measured that jump and
  D-018 is the answer. The clutch refuses to engage and the guard refuses the raw target.
* the shipped config -- the circle centred on the pico frame's origin, which under the placeholder
  identity ``teleop.pico_to_pelvis`` is the pelvis itself: unreachable and outside the box. This is
  the session that must not move the robot at all.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import yaml
from lerobot.datasets.lerobot_dataset import LeRobotDataset

from drivers.mock import MockArm, MockCamera, MockGlove, MockHand, MockPose
from engine.stub import StubEngine
from runtime import config, fk
from runtime.safety import SafetyViolation
from runtime.types import ACTION_DIM, ARM_DOF, JOINT_DIM, MotionCommand
from teleop.loop import Clutch, ClutchState, TeleopLoop, build, main
from teleop.recorder import Recorder
from teleop.retarget import pico_to_g1_base

SECOND_NS = 1_000_000_000
#: Centre and radius of the mock controller circle for a session that works, metres in the pelvis
#: frame. The circle spans x 0.22..0.34, y 0.09..0.21, z 0.09..0.15: inside ``config/safety.yaml``
#: ``workspace_box_m`` with its margin removed, near enough to the rest pose that engaging costs less
#: than one velocity-limited step, and where the left wrist can hold the mock's near-identity
#: orientation. All three are measured by the tests below, not asserted here.
REACHABLE = [0.28, 0.15, 0.12]
RADIUS_M = 0.06
#: Radius of the circle a session engages from. Small on purpose: the whole circle has to stay
#: within a couple of centimetres of the rest wrist pose, or the operator's hand is not where the
#: robot's is any more.
ENGAGE_RADIUS_M = 0.01
#: Seconds the loop holds before the operator engages, and the hold after a refusal.
HOLD_S = 0.5
#: Seconds per revolution for that session. The mock turns the wrist one full turn per cycle and a
#: full turn is not reachable; at 120 s a 30 s run sweeps a quarter of it, which is.
CYCLE_S = 120.0
#: Small frames for the recording test: the shapes are asserted at the configured size by
#: tests/test_recorder.py, and a 640x480 PNG costs ~8 ms to write.
SMALL = {"top": [64, 48], "oblique": [64, 48], "palm": [48, 32]}
#: Acceptance: the arm follows the IK target to inside this after the first-order lag settles.
MAX_TRACKING_ERROR_RAD = 0.02


class FakeClock:
    """A monotonic nanosecond clock the test moves by hand."""

    def __init__(self, ns: int = 0) -> None:
        self.ns = int(ns)

    def __call__(self) -> int:
        return self.ns

    def jump_to(self, ts_ns: int) -> None:
        """The loop's ``sleep_until``: time passes only because the loop waited for it."""
        self.ns = max(self.ns, int(ts_ns))


def engage_center(radius_m: float = ENGAGE_RADIUS_M) -> list[float]:
    """The circle centre that puts the operator's hand exactly on the arm's rest wrist pose at t=0.

    ``MockPose`` starts at ``centre + [radius, 0, 0]`` with an identity quaternion, and the wrist of
    the all-zero pose sits at ``runtime.fk.left_arm_fk(0)`` with a quaternion that is identity to
    1e-4 (measured in ``tests/test_retarget.py``'s model). So at t=0 the IK target is the pose the
    arm is already in, its solution is the rest pose, and the clutch may engage. This is the test
    driving the operator's hand to the robot, which is what a human does before pressing the key.
    """
    return (fk.left_arm_fk(np.zeros(JOINT_DIM)) - np.array([radius_m, 0.0, 0.0])).tolist()


def config_root(tmp_path: Path, *, center: list[float] | None = None, radius_m: float = RADIUS_M,
                small: bool = False) -> Path:
    """A copy of ``config/``, optionally with the mock circle moved and the camera frames shrunk."""
    root = tmp_path / "config"
    root.mkdir(exist_ok=True)
    for name in config.NAMES:
        data = config.load(name)
        if center is not None and name == "robot":
            data["mock"]["pose_center_m"] = list(center)
            data["mock"]["pose_cycle_s"] = CYCLE_S
            data["mock"]["pose_radius_m"] = radius_m
        if small and name == "cameras":
            for cam, size in SMALL.items():
                data[cam]["policy_resolution"] = list(size)
        if small and name == "training":
            data["observation"]["images"] = {k: list(v) for k, v in SMALL.items()}
        (root / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


class SpyLoop(TeleopLoop):
    """The loop, keeping every command the guard admitted so a test can compare the dataset to it."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.admitted: list = []

    def tick(self):
        admitted = super().tick()
        if admitted is not None:
            self.admitted.append(admitted)
        return admitted


class Rig:
    """Mock drivers, one fake clock, and the loop over them. ``record=True`` adds recorder and UI."""

    def __init__(self, tmp_path: Path, *, center: list[float] | None = REACHABLE,
                 radius_m: float = RADIUS_M, record: bool = False) -> None:
        self.cfg = config_root(tmp_path, center=center, radius_m=radius_m, small=record)
        self.clk = FakeClock()
        kw = {"now_ns": self.clk, "config_root": self.cfg}
        self.arm, self.hand = MockArm(**kw), MockHand(**kw)
        self.pose, self.glove = MockPose(**kw), MockGlove(**kw)
        self.cameras = {name: MockCamera(name, **kw) for name in ("top", "oblique")}
        self.recorder = self.engine = None
        if record:
            self.engine = StubEngine(0)
            self.recorder = Recorder(
                "20260913T120000", arm=self.arm, hand=self.hand, cameras=self.cameras, glove=self.glove,
                operator="test", now_ns=self.clk, root=tmp_path / "sessions", config_root=self.cfg,
            )
        self.loop = SpyLoop(
            pose_driver=self.pose, glove_driver=self.glove, arm=self.arm, hand=self.hand,
            cameras=self.cameras, engine=self.engine, recorder=self.recorder, ui=None,
            now_ns=self.clk, sleep_until=self.clk.jump_to, config_root=self.cfg,
        )

    def tracking_error_rad(self) -> float:
        """Worst of the 8 joints between the measured state and the last command the guard admitted."""
        assert self.loop.last_admitted is not None, "nothing was ever admitted, so nothing is tracking"
        return float(np.max(np.abs(self.arm.read_state().payload.joints - self.loop.last_admitted.joints)))

    def engage(self) -> bool:
        """Hold for a moment, as an operator lining up would, then press the operator's key."""
        self.loop.run(HOLD_S)
        return self.loop.clutch.request_engage()

    def raw_ik_target(self) -> np.ndarray:
        """The 8 joints the IK solves for right now: what the loop would send with no clutch."""
        pose = self.pose.read().payload
        position, quat = pico_to_g1_base(pose.position_m, pose.quat_xyzw, root=self.cfg)
        return self.loop.ik.solve(position, quat, self.arm.read_state().payload.joints)

    def cap_rad(self) -> float:
        return float(config.load("safety", root=self.cfg)["first_command_max_step_rad"])


# --------------------------------------------------------------------------------------------------
# acceptance 1: 30 s at 30 Hz, the arm follows the IK target, IK solve time measured
# --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def long_run(tmp_path_factory) -> Rig:
    """One 30 s teleop session on the fake clock, engaged from the rest pose, inspected below."""
    rig = Rig(tmp_path_factory.mktemp("long"), center=engage_center(), radius_m=ENGAGE_RADIUS_M)
    assert rig.engage(), "the operator's hand is on the rest pose, so the clutch must engage"
    rig.loop.run(30.0)
    assert rig.loop.clutch.state is ClutchState.ENGAGED, "the ramp is 1 s and the run was 30 s"
    return rig


def test_the_loop_holds_the_30_hz_grid(long_run: Rig, capsys) -> None:
    stats = long_run.loop.stats
    with capsys.disabled():
        print(f"\nTeleop loop, {HOLD_S} s holding + 30 s engaged on mocks: {stats.ticks} ticks in "
              f"{stats.elapsed_s:.3f} s = {stats.hz:.3f} Hz, {stats.sent} admitted, "
              f"{sum(stats.refused.values())} refused")
    # Two runs, each of which stops at the first tick past its end: up to one period long each.
    assert stats.elapsed_s == pytest.approx(30.0 + HOLD_S, abs=2e-9 * long_run.loop.period_ns + 0.01)
    assert abs(stats.hz - 30.0) <= 0.5, f"tick rate {stats.hz:.3f} Hz is not 30 +/- 0.5"


def test_every_command_of_a_reachable_session_is_admitted(long_run: Rig) -> None:
    """R1/R3: the count the guard admitted is the count the loop sent, and nothing was refused."""
    stats = long_run.loop.stats
    assert stats.refused == {}, f"a reachable session was refused: {stats.refused}"
    assert stats.sent == stats.ticks == long_run.arm.guard.admitted == len(long_run.loop.admitted)


def test_the_arm_state_follows_the_ik_target(long_run: Rig, capsys) -> None:
    error = long_run.tracking_error_rad()
    with capsys.disabled():
        print(f"Teleop loop tracking: |state - last admitted target| = {error:.5f} rad "
              f"(worst of {JOINT_DIM} joints, mock arm tau "
              f"{config.load('robot', root=long_run.cfg)['mock']['arm_tau_s']} s)")
    assert error < MAX_TRACKING_ERROR_RAD, f"tracking error {error:.5f} rad is above {MAX_TRACKING_ERROR_RAD}"


def test_ik_solve_time_per_tick(long_run: Rig, capsys) -> None:
    stats = long_run.loop.stats
    with capsys.disabled():
        print(f"Teleop loop IK solve on this laptop: mean {stats.ik_mean_ms:.3f} ms, p99 {stats.ik_p99_ms:.3f} ms "
              f"over {len(stats.ik_ms)} ticks (budget one 30 Hz period = 33.3 ms)")
    assert len(stats.ik_ms) == stats.ticks
    assert stats.ik_p99_ms < 1e3 / 30.0, "the IK alone does not fit in a 30 Hz tick"


def test_the_commanded_wrist_stays_inside_the_workspace_box(long_run: Rig) -> None:
    """What the guard admitted is what the box allows -- checked again here, independently."""
    box = config.load("safety", root=long_run.cfg)["workspace_box_m"]
    margin = float(box["margin_m"])
    low = np.asarray(box["min"], dtype=float) + margin
    high = np.asarray(box["max"], dtype=float) - margin
    point = fk.left_arm_fk(long_run.loop.last_admitted.joints)
    assert np.all(point >= low) and np.all(point <= high), f"wrist at {point} is outside {low}..{high}"


def test_no_admitted_command_ever_steps_more_than_one_tick_allows(long_run: Rig, capsys) -> None:
    """Measured: engaging costs nothing and every later command is a 30 Hz step (D-018).

    The first admitted command of the session is the arm's own measured state -- the clutch was
    holding -- and the first one after the operator engaged is inside
    ``first_command_max_step_rad`` on every joint, because the blend starts at alpha 0.
    """
    safety = config.load("safety", root=long_run.cfg)
    limit = float(safety["joint_velocity_limit_rad_s"])
    joints = np.array([cmd.joints for cmd in long_run.loop.admitted])
    hold = float(np.max(np.abs(joints[0])))  # the rest pose is zero for all 8 commanded joints
    steps = np.max(np.abs(np.diff(joints, axis=0)), axis=1) * 1e9 / long_run.loop.period_ns
    with capsys.disabled():
        print(f"Teleop loop engage: first admitted command is the measured state ({hold:.2e} rad from "
              f"it); over the whole session the worst commanded step is {steps.max():.3f} rad/s, "
              f"p99 {np.percentile(steps, 99):.3f} rad/s (limit {limit:g} rad/s)")
    assert hold == 0.0, "the first command of a holding loop is the measured state itself"
    assert steps.max() < limit, "a tracking tick asked for more than the velocity limit"


def test_the_first_admitted_command_after_engaging_is_under_the_step_cap(tmp_path, capsys) -> None:
    """Acceptance: engaging the clutch costs less than ``first_command_max_step_rad`` per joint."""
    rig = Rig(tmp_path, center=engage_center(), radius_m=ENGAGE_RADIUS_M)
    rig.loop.run(HOLD_S)                     # the operator lines up while the arm holds ...
    held = len(rig.loop.admitted)            # ... so everything from here on is post-engage
    assert rig.loop.clutch.request_engage() is True
    distance_before = rig.loop.clutch.worst_distance_rad
    rig.clk.ns += rig.loop.period_ns         # the key is pressed between two ticks, so the next
    state_before = rig.arm.read_state().payload.joints.copy()   # tick already has alpha > 0
    rig.loop.run(2.0 * float(config.load("robot", root=rig.cfg)["teleop"]["clutch_ramp_s"]))

    first = rig.loop.admitted[held].joints - state_before
    ramp = np.abs(np.diff(np.array([cmd.joints for cmd in rig.loop.admitted[held:]]), axis=0))
    cap, tolerance = rig.cap_rad(), rig.loop.clutch.tolerance_rad
    with capsys.disabled():
        print(f"Clutch engage: IK target was {distance_before:.4f} rad from the state (tolerance "
              f"{tolerance:g}); the first admitted command after engaging steps "
              f"{np.array2string(np.abs(first), precision=6, separator=', ')} rad, worst "
              f"{np.max(np.abs(first)):.6f} rad (cap {cap:g}); the worst single tick of the whole "
              f"1 s ramp is {ramp.max():.6f} rad")
    assert np.all(np.abs(first) < cap), "engaging stepped further than the guard's fresh-command cap"
    assert ramp.max() < cap, "a tick of the ramp stepped further than a fresh command may"
    assert rig.loop.clutch.state is ClutchState.ENGAGED  # the ramp finished
    assert rig.loop.stats.refused == {}, "engaging through the clutch is refused by nothing"


def test_the_raw_ik_target_that_T_032_measured_is_now_refused_by_the_guard(tmp_path, capsys) -> None:
    """The 0.443 rad engage step of T-032, sent straight at the arm, is refused (D-018).

    This is the command the loop used to send on its first tick. It never reaches a driver any more
    -- the clutch holds instead -- so the test builds it by hand and hands it to the guard.
    """
    rig = Rig(tmp_path)  # the far circle: reachable, but not from where the arm is
    before = rig.arm.read_state().payload.joints.copy()
    target = rig.raw_ik_target()
    step = np.abs(target - before)
    with pytest.raises(SafetyViolation) as excinfo:
        rig.arm.send_targets(MotionCommand(arm=target[:ARM_DOF], waist_yaw=target[ARM_DOF], pinch=0.0))
    after = rig.arm.read_state().payload.joints
    with capsys.disabled():
        print(f"Raw engage (no clutch): the IK target is {step.max():.3f} rad from the measured state; "
              f"the guard refuses it as {excinfo.value.rule} (cap {rig.cap_rad():g} rad) and the arm "
              f"stayed at {np.max(np.abs(after)):.2e} rad")
    assert excinfo.value.rule == "first_command_step"
    assert step.max() > rig.cap_rad() and step.max() == pytest.approx(0.443, abs=0.01)
    assert rig.arm.guard.admitted == 0
    assert np.array_equal(after, before) and np.array_equal(after, np.zeros(JOINT_DIM))


def test_the_clutch_refuses_to_engage_from_a_pose_the_arm_is_not_in(tmp_path, capsys) -> None:
    """The same far circle through the loop: the clutch holds, and the key does not engage it."""
    rig = Rig(tmp_path)
    assert rig.engage() is False, "the operator's hand is 0.44 rad away; engaging must be refused"
    stats = rig.loop.run(1.0)
    joints = np.array([cmd.joints for cmd in rig.loop.admitted])
    with capsys.disabled():
        print(f"Clutch refusal: worst joint {rig.loop.clutch.worst_distance_rad:.3f} rad from the state "
              f"against a tolerance of {rig.loop.clutch.tolerance_rad:g}; {stats.sent} holds sent, "
              f"{sum(stats.refused.values())} refused, arm at {np.max(np.abs(joints)):.2e} rad")
    assert rig.loop.clutch.state is ClutchState.DISENGAGED
    assert stats.sent == stats.ticks and stats.refused == {}  # a hold is a legitimate command
    assert np.max(np.abs(joints)) == 0.0, "a holding loop commanded a joint somewhere else"


def test_a_guard_refusal_while_engaged_disengages_the_clutch(tmp_path, capsys) -> None:
    """The operator re-engages deliberately; the loop never resumes tracking on its own (D-018)."""
    rig = Rig(tmp_path, center=engage_center(), radius_m=ENGAGE_RADIUS_M)
    assert rig.engage() is True
    rig.loop.run(1.5)
    assert rig.loop.clutch.state is ClutchState.ENGAGED

    rig.loop.tick()                      # two ticks at the same instant: the second one is inside
    assert rig.loop.tick() is None       # the 60 Hz command rate limit, so the guard refuses it
    with capsys.disabled():
        print(f"Refusal while engaged: rules {dict(rig.loop.stats.refused)} -> clutch "
              f"{rig.loop.clutch.state.value}")
    assert rig.loop.stats.refused == {"command_rate": 1}
    assert rig.loop.clutch.state is ClutchState.DISENGAGED

    sent = rig.loop.stats.sent
    rig.clk.ns += rig.loop.period_ns     # the next tick is a legal one again ...
    rig.loop.run(HOLD_S)                 # ... and the loop keeps holding until the operator engages
    assert rig.loop.stats.sent > sent and rig.loop.clutch.holding
    assert rig.loop.stats.refused == {"command_rate": 1}


def test_teleop_imports_nothing_from_tools_hardware_checks() -> None:
    """R2: scripted motion lives only in tools/hardware_checks/ and never reaches the teleop path."""
    for path in sorted((Path(__file__).resolve().parent.parent / "teleop").rglob("*.py")):
        assert "hardware_checks" not in path.read_text(encoding="utf-8"), path


# --------------------------------------------------------------------------------------------------
# acceptance 2: an out-of-box pose is refused and the robot does not move
# --------------------------------------------------------------------------------------------------


def test_an_out_of_box_pose_is_never_commanded_and_nothing_moves(tmp_path, capsys) -> None:
    """The shipped mock circle sits on the pelvis: unreachable, outside the box, never commanded.

    Two layers, and the test checks both: the clutch will not engage into it (so the loop only ever
    sends the arm's own state), and the target it would have sent is outside the workspace box, so
    the guard would refuse it as well.
    """
    rig = Rig(tmp_path, center=None)
    before = rig.arm.read_state().payload.joints
    box = config.load("safety", root=rig.cfg)["workspace_box_m"]
    point = fk.left_arm_fk(rig.raw_ik_target())
    stats = rig.loop.run(2.0)
    after = rig.arm.read_state().payload.joints
    with capsys.disabled():
        print(f"Teleop loop, out-of-box pose: {stats.ticks} ticks, {stats.sent} holds admitted, "
              f"{sum(stats.refused.values())} refused {dict(stats.refused)}; the IK target's wrist is at "
              f"{np.round(point, 3).tolist()} m, outside {box['min']}..{box['max']}")
    assert np.any(point < np.asarray(box["min"], float)) or np.any(point > np.asarray(box["max"], float))
    assert rig.engage() is False, "the clutch must not engage into an unreachable pose"
    assert stats.ticks > 50 and stats.sent == stats.ticks  # every command was a zero-motion hold
    assert stats.refused == {}
    assert np.array_equal(before, after) and np.array_equal(after, np.zeros(JOINT_DIM))
    assert np.max(np.abs([cmd.joints for cmd in rig.loop.admitted])) == 0.0
    assert rig.loop.stats.frames == 0


# --------------------------------------------------------------------------------------------------
# acceptance 3: the recorded action rows are the commands the guard admitted
# --------------------------------------------------------------------------------------------------


def test_recorded_action_rows_are_the_admitted_commands(tmp_path) -> None:
    rig = Rig(tmp_path, center=engage_center(), radius_m=ENGAGE_RADIUS_M, record=True)
    ui = rig.loop.ui
    assert ui is not None, "the loop builds the operator UI from recorder + engine + cameras"
    assert ui.clutch is rig.loop.clutch, "the operator's engage key reaches the loop's own clutch"
    rig.loop.run(HOLD_S)
    ui.handle_key("e")  # the operator engages through the UI, from a pose the arm is already in
    assert rig.loop.clutch.state is not ClutchState.DISENGAGED
    assert ui.handle_key("s").value == "recording"
    rig.loop.run(3.0)
    ui.handle_key("y")  # the operator marks the episode: it is written and the outcome reported
    rig.recorder.close()

    episode = rig.recorder.episodes[0]
    assert episode.frames == rig.loop.stats.frames > 30
    dataset = LeRobotDataset(f"ludo-g1/{rig.recorder.session_id}", root=rig.recorder.root)
    rows = [np.asarray(dataset[i]["action"], dtype=np.float64) for i in range(episode.frames)]
    sent = [cmd.to_action() for cmd in rig.loop.admitted]

    # Every row is a command the guard admitted, and they appear in the order they were admitted:
    # the recorder writes the action nearest each frame instant, which is the newest one or the one
    # before it, never an interpolation and never a refused target.
    previous = -1
    for i, row in enumerate(rows):
        assert row.shape == (ACTION_DIM,)
        matches = [k for k, action in enumerate(sent) if np.allclose(action, row, atol=1e-6)]
        assert matches, f"action row {i} = {row} was never admitted by the guard"
        assert matches[-1] > previous, f"action row {i} is older than row {i - 1}"
        previous = matches[-1]


def test_the_loop_polls_the_streams_when_no_episode_is_open(tmp_path) -> None:
    """Read-only polling is allowed with no session (R1) and keeps the alignment buffers warm."""
    rig = Rig(tmp_path, record=True)
    rig.loop.run(1.0)
    assert rig.loop.stats.frames == 0 and rig.recorder.episodes == []
    assert rig.loop.stats.sent > 25, "the arm is still commanded while nothing is being recorded"


# --------------------------------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------------------------------


def test_cli_refuses_a_real_backend_it_cannot_build(capsys) -> None:
    """R1/R2: with the devices absent the CLI says which one and why, instead of inventing a loop.

    Every device has a real read-only driver since T-020, so the refusal is now "this device is not
    there" rather than "this driver does not exist"; either way it is exit 2 and a reason, and
    `build` leaves nothing open behind it.
    """
    assert main(["--backend", "real", "--seconds", "0.1"]) == 2
    assert "cannot run on backend 'real'" in capsys.readouterr().out


def test_cli_runs_on_mocks(capsys) -> None:
    """The one test on the real clock; it runs for a fraction of a second."""
    assert main(["--seconds", "0.3"]) == 0
    out = capsys.readouterr().out
    assert "teleop loop summary" in out and "ik solve" in out
    # The CLI has no keyboard, so the clutch never engages and the summary says so.
    assert "clutch           disengaged" in out


def test_build_wires_the_mock_backend() -> None:
    loop = build("mock")
    assert isinstance(loop, TeleopLoop) and loop.recorder is None and loop.ui is None
    assert loop.period_ns == round(1e9 / float(config.load("training")["rates"]["action_hz"]))


def test_the_mock_circle_is_where_the_two_sessions_say_it_is(tmp_path) -> None:
    """The two configurations differ only in ``mock.pose_center_m``, and that is what moves the path."""
    rig = Rig(tmp_path)
    radius = float(config.load("robot", root=rig.cfg)["mock"]["pose_radius_m"])
    at_zero = rig.pose.pose_at(0).position_m
    assert np.allclose(at_zero, np.asarray(REACHABLE) + [radius, 0.0, 0.0])
    quarter = rig.pose.pose_at(round(0.25 * CYCLE_S * SECOND_NS)).position_m
    assert math.isclose(float(np.linalg.norm((quarter - np.asarray(REACHABLE))[:2])), radius, rel_tol=1e-9)
    shipped = float(config.load("robot")["mock"]["pose_radius_m"])
    assert np.allclose(MockPose(now_ns=FakeClock()).pose_at(0).position_m, [shipped, 0.0, 0.0])


# --------------------------------------------------------------------------------------------------
# the clutch on its own: the ramp, the tolerance and the config it reads (D-018)
# --------------------------------------------------------------------------------------------------


def test_clutch_reads_its_two_numbers_from_the_config() -> None:
    teleop = config.load("robot")["teleop"]
    clutch = Clutch.from_config()
    assert clutch.tolerance_rad == teleop["clutch_engage_tolerance_rad"]
    assert clutch.ramp_s == teleop["clutch_ramp_s"]
    # The clutch may never let through a step the guard would refuse (D-018).
    assert clutch.tolerance_rad <= config.load("safety")["first_command_max_step_rad"]
    assert clutch.state is ClutchState.DISENGAGED and clutch.holding


@pytest.mark.parametrize("bad", [{"tolerance_rad": 0.0, "ramp_s": 1.0}, {"tolerance_rad": 0.05, "ramp_s": -1.0}])
def test_clutch_refuses_nonsense_settings(bad: dict) -> None:
    with pytest.raises(config.ConfigError):
        Clutch(**bad)


def test_clutch_blends_over_the_ramp_and_holds_before_and_after(capsys) -> None:
    """The blend is what makes engaging cost nothing: alpha starts at 0, on the tick of the key."""
    clk = FakeClock()
    clutch = Clutch(tolerance_rad=0.05, ramp_s=1.0, now_ns=clk)
    ik, state = np.full(JOINT_DIM, 0.04), np.zeros(JOINT_DIM)

    # Disengaged: the command is the measured state, whatever the operator's hand is doing.
    assert np.array_equal(clutch.target(ik, state, clk.ns), state)
    assert clutch.worst_distance_rad == pytest.approx(0.04)
    assert clutch.request_engage() is True
    assert clutch.request_engage() is False, "engaging an engaged clutch is not an event"

    seen = []
    for ms in (0, 250, 500, 1000, 1500):
        clk.ns = ms * 1_000_000
        seen.append(float(clutch.target(ik, state, clk.ns)[0]))
    with capsys.disabled():
        print(f"\nClutch ramp over 1.0 s at |ik - state| = 0.04 rad: {[round(v, 4) for v in seen]} rad "
              f"at 0, 250, 500, 1000, 1500 ms")
    assert seen == pytest.approx([0.0, 0.01, 0.02, 0.04, 0.04])
    assert clutch.state is ClutchState.ENGAGED
    assert np.array_equal(clutch.target(ik, state, clk.ns), ik)  # engaged: the IK target untouched

    clutch.disengage("the test asked")
    assert clutch.holding and np.array_equal(clutch.target(ik, state, clk.ns), state)


def test_clutch_engage_needs_every_joint_inside_the_tolerance() -> None:
    clk = FakeClock()
    clutch = Clutch(tolerance_rad=0.05, ramp_s=1.0, now_ns=clk)
    assert clutch.request_engage() is False, "a clutch that has never seen a tick cannot engage"
    ik = np.zeros(JOINT_DIM)
    ik[4] = 0.051  # one joint, just outside
    clutch.target(ik, np.zeros(JOINT_DIM), clk.ns)
    assert clutch.request_engage() is False
    ik[4] = 0.05
    clutch.target(ik, np.zeros(JOINT_DIM), clk.ns)
    assert clutch.request_engage() is True
