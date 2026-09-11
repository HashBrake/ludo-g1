"""Teleop loop tests (T-032; CLAUDE.md 5.2, 5.3, Phase 2, R1, R2, R3, R5).

Everything runs on the mock drivers and a :class:`FakeClock` the loop's injected ``sleep_until``
jumps forward, so a 30 s teleop session costs no wall-clock seconds of waiting and the tick grid is
exactly the one a real 30 s run would have produced. Nothing here is marked ``motion``: the mocks are
simulated robots, which R1 exempts from the session gate and from nothing else -- every command still
goes through :meth:`runtime.safety.Guard.admit`, and two tests count exactly that.

The operator's path is the mock Pico circle (``config/robot.yaml`` ``mock.pose_*``). Two
configurations of that one mock are used, and they are the whole point of the file:

* :data:`REACHABLE` -- the circle centred inside the workspace box, turning slowly enough that the
  wrist pose stays reachable. This is a session that works: the guard admits, the arm tracks.
* the shipped config -- the circle centred on the pico frame's origin, which under the placeholder
  identity ``teleop.pico_to_pelvis`` is the pelvis itself: unreachable and outside the box. This is
  the session that must not move the robot.
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
from runtime.types import ACTION_DIM, JOINT_DIM
from teleop.loop import TeleopLoop, build, main
from teleop.recorder import Recorder

SECOND_NS = 1_000_000_000
#: Centre and radius of the mock controller circle for a session that works, metres in the pelvis
#: frame. The circle spans x 0.22..0.34, y 0.09..0.21, z 0.09..0.15: inside ``config/safety.yaml``
#: ``workspace_box_m`` with its margin removed, near enough to the rest pose that engaging costs less
#: than one velocity-limited step, and where the left wrist can hold the mock's near-identity
#: orientation. All three are measured by the tests below, not asserted here.
REACHABLE = [0.28, 0.15, 0.12]
RADIUS_M = 0.06
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


def config_root(tmp_path: Path, *, center: list[float] | None = None, small: bool = False) -> Path:
    """A copy of ``config/``, optionally with the mock circle moved and the camera frames shrunk."""
    root = tmp_path / "config"
    root.mkdir(exist_ok=True)
    for name in config.NAMES:
        data = config.load(name)
        if center is not None and name == "robot":
            data["mock"]["pose_center_m"] = list(center)
            data["mock"]["pose_cycle_s"] = CYCLE_S
            data["mock"]["pose_radius_m"] = RADIUS_M
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

    def __init__(self, tmp_path: Path, *, center: list[float] | None = REACHABLE, record: bool = False) -> None:
        self.cfg = config_root(tmp_path, center=center, small=record)
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


# --------------------------------------------------------------------------------------------------
# acceptance 1: 30 s at 30 Hz, the arm follows the IK target, IK solve time measured
# --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def long_run(tmp_path_factory) -> Rig:
    """One 30 s teleop session on the fake clock, inspected by the four tests below."""
    rig = Rig(tmp_path_factory.mktemp("long"))
    rig.loop.run(30.0)
    return rig


def test_the_loop_holds_the_30_hz_grid(long_run: Rig, capsys) -> None:
    stats = long_run.loop.stats
    with capsys.disabled():
        print(f"\nTeleop loop, 30 s on mocks: {stats.ticks} ticks in {stats.elapsed_s:.3f} s = {stats.hz:.3f} Hz, "
              f"{stats.sent} admitted, {sum(stats.refused.values())} refused")
    assert stats.elapsed_s == pytest.approx(30.0, abs=0.05)
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


def test_engaging_costs_one_large_step_because_the_loop_has_no_clutch(long_run: Rig, capsys) -> None:
    """Measured: the first command is a jump, every later one is a 30 Hz step. See docs/teleop.md.

    The operator's hand is wherever it is when the loop starts, so the first solved pose is far from
    the robot's. The guard allows that first jump -- a fresh command is measured against the state
    aged ``command_gap_reset_s`` -- and this test is what says how big it is.
    """
    safety = config.load("safety", root=long_run.cfg)
    limit, gap = float(safety["joint_velocity_limit_rad_s"]), float(safety["command_gap_reset_s"])
    joints = np.array([cmd.joints for cmd in long_run.loop.admitted])
    engage = float(np.max(np.abs(joints[0])))  # the rest pose is zero for all 8 commanded joints
    steps = np.max(np.abs(np.diff(joints, axis=0)), axis=1) * 1e9 / long_run.loop.period_ns
    with capsys.disabled():
        print(f"Teleop loop engage: first admitted command steps {engage:.3f} rad from the rest pose "
              f"in one tick; afterwards max {steps.max():.3f} rad/s, p99 {np.percentile(steps, 99):.3f} rad/s "
              f"(limit {limit:g} rad/s)")
    assert engage <= limit * gap, "the guard would have refused the engage step"
    assert steps.max() < limit, "a tracking tick asked for more than the velocity limit"


def test_teleop_imports_nothing_from_tools_hardware_checks() -> None:
    """R2: scripted motion lives only in tools/hardware_checks/ and never reaches the teleop path."""
    for path in sorted((Path(__file__).resolve().parent.parent / "teleop").rglob("*.py")):
        assert "hardware_checks" not in path.read_text(encoding="utf-8"), path


# --------------------------------------------------------------------------------------------------
# acceptance 2: an out-of-box pose is refused and the robot does not move
# --------------------------------------------------------------------------------------------------


def test_an_out_of_box_pose_is_refused_and_nothing_moves(tmp_path, capsys) -> None:
    """The shipped mock circle sits on the pelvis: unreachable, outside the box, so nothing is sent."""
    rig = Rig(tmp_path, center=None)
    before = rig.arm.read_state().payload.joints
    stats = rig.loop.run(2.0)
    after = rig.arm.read_state().payload.joints
    with capsys.disabled():
        print(f"Teleop loop, out-of-box pose: {stats.ticks} ticks, {stats.sent} admitted, "
              f"{sum(stats.refused.values())} refused {dict(stats.refused)}")
    assert stats.ticks > 50 and stats.sent == 0
    assert sum(stats.refused.values()) == stats.ticks
    assert rig.arm.guard.admitted == 0 and rig.loop.last_admitted is None
    assert np.array_equal(before, after) and np.array_equal(after, np.zeros(JOINT_DIM))
    assert rig.loop.stats.frames == 0


# --------------------------------------------------------------------------------------------------
# acceptance 3: the recorded action rows are the commands the guard admitted
# --------------------------------------------------------------------------------------------------


def test_recorded_action_rows_are_the_admitted_commands(tmp_path) -> None:
    rig = Rig(tmp_path, record=True)
    ui = rig.loop.ui
    assert ui is not None, "the loop builds the operator UI from recorder + engine + cameras"
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


def test_cli_refuses_the_real_backend(capsys) -> None:
    """R1/R2: there are no real actuated drivers, and the CLI says so instead of inventing one."""
    assert main(["--backend", "real", "--seconds", "0.1"]) == 2
    assert "does not exist yet" in capsys.readouterr().out


def test_cli_runs_on_mocks(capsys) -> None:
    """The one test on the real clock; it runs for a fraction of a second."""
    assert main(["--seconds", "0.3"]) == 0
    out = capsys.readouterr().out
    assert "teleop loop summary" in out and "ik solve" in out


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
