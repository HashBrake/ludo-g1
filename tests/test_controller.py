"""Controller, goal and perception tests (T-016; CLAUDE.md 5.2, 5.3, 5.5, R1, R2, R3, R5).

Nothing here touches hardware and nothing here is marked ``motion``: the mocks are simulated robots,
which R1 exempts from the session gate and from nothing else. Every send still goes through
:meth:`runtime.safety.Guard.admit`, and several tests count exactly that.

Time comes from a :class:`FakeClock` that the controller's injected ``sleep_until`` jumps forward,
never from ``time.sleep``: a 20 s primitive execution runs here in milliseconds and its loop rate is
exact, because the fake clock has no scheduling jitter. The one test that uses the real clock
(:func:`test_cli_runs_on_mocks_and_writes_a_heartbeat`) runs for a fraction of a second.
"""

from __future__ import annotations

import numpy as np
import pytest

from board.perception import NO_PROGRESS, MockPerception, Perception, state_delta
from drivers.mock import MockArm, MockCamera, MockHand
from engine.cells import load_cells
from engine.interface import Cell, Command, Outcome, Primitive
from engine.stub import StubEngine
from runtime import config
from runtime.controller import Controller, RunSummary, build, main
from runtime.goal import GoalRenderer
from runtime.policy_api import ActionChunk, HoldPolicy, Observation, Policy
from runtime.safety import REPO_ROOT
from runtime.types import ACTION_DIM, ARM_DOF

SECOND_NS = 1_000_000_000


class FakeClock:
    """A monotonic nanosecond clock the test moves by hand."""

    def __init__(self, ns: int = 0) -> None:
        self.ns = int(ns)

    def __call__(self) -> int:
        return self.ns

    def jump_to(self, ts_ns: int) -> None:
        """The controller's ``sleep_until``: time passes only because the loop waited for it."""
        self.ns = max(self.ns, int(ts_ns))


class SpyEngine:
    """Wraps a :class:`~engine.stub.StubEngine` and records every command and every report."""

    def __init__(self, inner: StubEngine) -> None:
        self.inner = inner
        self.commands: list[Command] = []
        self.reports: list[Outcome] = []

    def next_command(self) -> Command | None:
        cmd = self.inner.next_command()
        if cmd is not None:
            self.commands.append(cmd)
        return cmd

    def report(self, outcome: Outcome) -> None:
        self.reports.append(outcome)
        self.inner.report(outcome)

    def board_state(self) -> dict:
        return self.inner.board_state()


class IndexPolicy(HoldPolicy):
    """HoldPolicy that keeps every chunk it returned, so a test can see which entries were executed.

    Row ``i`` is tagged in the first arm joint with ``i * 1e-6``: one microradian, far inside every
    envelope limit, so the chunk is still a hold and the rows are still distinguishable.
    """

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[ActionChunk] = []

    def act(self, observation: Observation) -> ActionChunk:
        rows = np.repeat(observation.state[None, :], self.chunk, axis=0).copy()
        rows[:, 0] += np.arange(self.chunk) * 1e-6
        self.calls += 1
        self.chunks.append(ActionChunk(actions=rows, hz=self.hz))
        return self.chunks[-1]


class DonePolicy(HoldPolicy):
    """HoldPolicy that reports done on its ``after`` th observation, to exercise the 5.5 stop."""

    def __init__(self, after: int = 3) -> None:
        super().__init__()
        self.after = after
        self.seen = 0

    def done(self, observation: Observation) -> bool:
        self.seen += 1
        return self.seen > self.after


class LungePolicy(HoldPolicy):
    """A policy whose actions the envelope must refuse: 2 rad away from a standstill in one step."""

    def act(self, observation: Observation) -> ActionChunk:
        rows = np.repeat(observation.state[None, :], self.chunk, axis=0).copy()
        rows[:, :ARM_DOF] = 2.0
        self.calls += 1
        return ActionChunk(actions=rows, hz=self.hz)


def move_command() -> Command:
    cells = load_cells()
    return Command(Primitive.MOVE, cells["track-0"], cells["track-5"], "R0")


def make_controller(tmp_path, policy=None, script=None, seed: int = 0):
    """A controller over mocks on one shared fake clock. Returns everything a test needs to assert."""
    clk = FakeClock()
    arm, hand = MockArm(now_ns=clk), MockHand(now_ns=clk)
    engine = SpyEngine(StubEngine(seed, script=script))
    controller = Controller(
        arm=arm,
        hand=hand,
        cameras={name: MockCamera(name, now_ns=clk) for name in ("top", "oblique")},
        engine=engine,
        policy=HoldPolicy() if policy is None else policy,
        perception=MockPerception(),
        now_ns=clk,
        sleep_until=clk.jump_to,
        session="test",
        log_dir=tmp_path,
    )
    return controller, clk, engine, arm, hand


# --------------------------------------------------------------------------------------------------
# the loop: one MOVE to the timeout, at 10 Hz, every action through the guard
# --------------------------------------------------------------------------------------------------


def test_one_move_runs_to_timeout_and_is_reported(tmp_path) -> None:
    controller, _clk, engine, _arm, _hand = make_controller(tmp_path, script=[move_command()])
    summary = controller.run(max_commands=1)

    assert summary.commands == 1
    assert engine.commands[0].primitive is Primitive.MOVE
    assert summary.stopped_by["timeout"] == 1
    # 20 s of CLAUDE.md 5.5, measured on the fake clock, to within one 30 Hz action period.
    assert summary.elapsed_s == pytest.approx(config.load("training")["runtime"]["primitive_timeout_s"], abs=0.05)
    assert len(engine.reports) == 1
    outcome = engine.reports[0]
    assert isinstance(outcome, Outcome)
    assert outcome.success is False and outcome.failure_mode == NO_PROGRESS
    assert summary.failure_modes[NO_PROGRESS] == 1


def test_loop_runs_at_10_hz_on_the_fake_clock(tmp_path) -> None:
    controller, _clk, _engine, _arm, _hand = make_controller(tmp_path, script=[move_command()])
    summary = controller.run(max_commands=1)

    assert summary.policy_hz == pytest.approx(10.0, abs=0.5)
    assert summary.action_hz == pytest.approx(30.0, abs=1.5)
    assert summary.align_failures == 0


def test_every_action_went_through_the_guard(tmp_path) -> None:
    """R1/R3: the only door to the actuators is Guard.admit, and it counts what it let through."""
    controller, _clk, _engine, arm, hand = make_controller(tmp_path, script=[move_command()])
    summary = controller.run(max_commands=1)

    assert summary.actions_sent > 0
    assert summary.refused == 0
    assert arm.guard.admitted == summary.actions_sent
    assert hand.guard.admitted == summary.actions_sent
    assert arm.guard.simulated and hand.guard.simulated  # R1: simulated robots need no session


def test_chunk_is_executed_as_a_receding_horizon(tmp_path) -> None:
    """At most ``diffusion.execute`` of the 16 predicted actions are played before the next chunk."""
    policy = IndexPolicy()
    controller, _clk, _engine, _arm, _hand = make_controller(tmp_path, policy=policy, script=[move_command()])
    per_chunk: list[list[np.ndarray]] = []
    inner_send, inner_act = controller.send, policy.act

    def act(observation):
        per_chunk.append([])
        return inner_act(observation)

    controller.send = lambda action: (per_chunk[-1].append(np.asarray(action)), inner_send(action))[1]
    policy.act = act
    summary = controller.run(max_commands=1)

    assert len(policy.chunks) == summary.policy_calls
    assert sum(len(played) for played in per_chunk) == summary.actions_sent
    for chunk, played in zip(policy.chunks, per_chunk, strict=True):
        assert len(chunk) == 16                         # config/training.yaml diffusion.chunk
        assert len(played) <= controller.execute == 8   # ... and diffusion.execute of them are played
        for i, action in enumerate(played):
            assert np.array_equal(action, chunk[i])     # in order, from the head of the chunk
    assert [len(played) for played in per_chunk[1:-1]] == [3] * (len(per_chunk) - 2)  # 30 Hz / 10 Hz


def test_policy_done_stops_the_primitive_early(tmp_path) -> None:
    controller, _clk, _engine, _arm, _hand = make_controller(
        tmp_path, policy=DonePolicy(after=3), script=[move_command()]
    )
    summary = controller.run(max_commands=1)

    assert summary.stopped_by["policy_done"] == 1
    assert summary.policy_calls == 3
    assert summary.elapsed_s < 1.0  # nowhere near the 20 s timeout


def test_a_refused_action_is_counted_and_the_loop_continues(tmp_path) -> None:
    """R3: the envelope is the limiter, not a crash. A refusal is logged, counted, and survived."""
    controller, _clk, engine, arm, _hand = make_controller(
        tmp_path, policy=LungePolicy(), script=[move_command()]
    )
    summary = controller.run(max_commands=1)

    assert summary.refused == summary.actions_sent > 0  # the arm refused every one of them
    assert arm.guard.admitted == 0
    assert summary.commands == 1 and len(engine.reports) == 1


# --------------------------------------------------------------------------------------------------
# the engine cycle: a failed outcome brings a RECOVER, and the controller executes it
# --------------------------------------------------------------------------------------------------


def test_failure_makes_the_stub_issue_a_recover_which_is_executed(tmp_path) -> None:
    controller, _clk, engine, _arm, _hand = make_controller(tmp_path, script=[move_command()])
    controller.run(max_commands=1)
    assert engine.reports[0].success is False

    summary = controller.run(max_commands=1)  # the same controller keeps going
    assert engine.commands[1].primitive is Primitive.RECOVER
    assert summary.primitives["recover"] == 1
    assert summary.commands == 2  # the summary accumulates over the controller's life
    assert len(engine.reports) == 2


def test_run_stops_at_its_deadline_mid_primitive(tmp_path) -> None:
    controller, _clk, _engine, _arm, _hand = make_controller(tmp_path, script=[move_command()])
    summary = controller.run(seconds=2.0)

    assert summary.stopped_by["run_deadline"] == 1
    assert summary.elapsed_s == pytest.approx(2.0, abs=0.05)
    assert summary.policy_calls == pytest.approx(20, abs=1)


def test_run_ends_when_the_engine_is_exhausted(tmp_path) -> None:
    controller, _clk, engine, _arm, _hand = make_controller(tmp_path, script=[move_command()])
    summary = controller.run(seconds=60.0, max_commands=4)

    # MOVE, then RECOVER, then MOVE again, then the script is spent (docs/engine.md retry budget).
    assert summary.commands <= 4
    assert engine.inner.next_command() is None or summary.commands == 4


# --------------------------------------------------------------------------------------------------
# heartbeat and summary
# --------------------------------------------------------------------------------------------------


def test_heartbeat_is_written_once_a_second(tmp_path) -> None:
    controller, _clk, _engine, _arm, _hand = make_controller(tmp_path, script=[move_command()])
    controller.run(seconds=5.0)

    lines = controller.heartbeat_path.read_text(encoding="utf-8").splitlines()
    assert controller.heartbeat_path.parent == tmp_path
    assert len(lines) == pytest.approx(6, abs=1)  # t=0 plus one per second
    assert all(line.startswith("ts_ns=") and "session=test" in line for line in lines)
    assert "primitive=move" in lines[0]


def test_summary_lines_report_measured_numbers() -> None:
    summary = RunSummary(commands=2, succeeded=1, policy_calls=20, actions_sent=60, elapsed_s=2.0)
    summary.primitives["move"] += 2
    summary.failure_modes["missed_cell"] += 1
    text = "\n".join(summary.lines())

    assert "policy calls       20 = 10.00 Hz" in text
    assert "actions sent       60 = 30.00 Hz" in text
    assert "move=2" in text and "missed_cell=1" in text
    assert RunSummary().policy_hz == 0.0  # no division by a zero run length


# --------------------------------------------------------------------------------------------------
# policy_api: shapes, and the R2 promise of HoldPolicy
# --------------------------------------------------------------------------------------------------


def observation(state=None, size=(8, 6)) -> Observation:
    w, h = size
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    return Observation(
        ts_ns=1,
        top=frame,
        oblique=frame,
        palm=frame,
        state=np.zeros(ACTION_DIM) if state is None else state,
        goal=np.zeros((2, h, w), dtype=np.float32),
        task_id=np.array([1.0, 0.0, 0.0], dtype=np.float32),
    )


def test_hold_policy_satisfies_the_protocol_and_commands_no_motion() -> None:
    policy = HoldPolicy()
    assert isinstance(policy, Policy)
    state = np.arange(ACTION_DIM, dtype=np.float64) / 100.0
    policy.reset(move_command())
    chunk = policy.act(observation(state))

    assert len(chunk) == 16 and chunk.hz == 30.0
    for i in range(len(chunk)):
        assert np.array_equal(chunk[i], state)  # R2: every action is exactly the measured state
    assert policy.done(observation(state)) is False
    assert "never deployed" in HoldPolicy.__doc__ and "R2" in HoldPolicy.__doc__


def test_observation_and_chunk_validate_their_shapes() -> None:
    with pytest.raises(ValueError, match="must hold 9 values"):
        observation(state=np.zeros(8))
    with pytest.raises(ValueError, match="the `top` frame"):
        Observation(ts_ns=0, top=np.zeros((4, 4, 3), np.uint8), oblique=np.zeros((4, 4, 3), np.uint8),
                    palm=np.zeros((4, 4, 3), np.uint8), state=np.zeros(ACTION_DIM),
                    goal=np.zeros((2, 5, 5), np.float32), task_id=np.array([1.0, 0.0, 0.0]))
    with pytest.raises(ValueError, match="one-hot"):
        Observation(ts_ns=0, top=np.zeros((4, 4, 3), np.uint8), oblique=np.zeros((4, 4, 3), np.uint8),
                    palm=np.zeros((4, 4, 3), np.uint8), state=np.zeros(ACTION_DIM),
                    goal=np.zeros((2, 4, 4), np.float32), task_id=np.array([1.0, 1.0, 0.0]))
    with pytest.raises(ValueError, match=r"\(n, 9\)"):
        ActionChunk(actions=np.zeros((16, 8)))
    with pytest.raises(ValueError, match="hz must be positive"):
        ActionChunk(actions=np.zeros((16, ACTION_DIM)), hz=0.0)
    with pytest.raises(ValueError):  # frozen: a chunk handed to the loop cannot be edited in place
        ActionChunk(actions=np.zeros((16, ACTION_DIM))).actions[0, 0] = 1.0


# --------------------------------------------------------------------------------------------------
# goal heatmaps (5.3)
# --------------------------------------------------------------------------------------------------


def test_heatmap_peaks_on_the_calibrated_pixel_of_each_cell() -> None:
    renderer = GoalRenderer(64, 48, sigma_px=3.0)
    src = Cell(id="a", board_xy_mm=(0.0, 0.0), top_px=(10.0, 20.0))
    dst = Cell(id="b", board_xy_mm=(0.0, 0.0), top_px=(50.0, 30.0))
    goal = renderer.render(Command(Primitive.MOVE, src, dst, "R0"))

    assert goal.shape == (2, 48, 64) and goal.dtype == np.float32
    assert np.unravel_index(goal[0].argmax(), goal[0].shape) == (20, 10)
    assert np.unravel_index(goal[1].argmax(), goal[1].shape) == (30, 50)
    assert goal[0].max() == pytest.approx(1.0)
    assert renderer.placeholder_uses == 0


def test_uncalibrated_cells_fall_back_to_the_documented_placeholder() -> None:
    renderer = GoalRenderer(640, 480, sigma_px=8.0)
    centre = Cell(id="c", board_xy_mm=(0.0, 0.0), top_px=None)
    corner = Cell(id="d", board_xy_mm=(300.0, 300.0), top_px=None)  # the board is 600 x 600 mm

    assert renderer.placeholder_px(centre) == pytest.approx((319.5, 239.5))
    assert renderer.placeholder_px(corner) == pytest.approx((639.0, 0.0))  # +y is up on the board
    renderer.channel(centre)
    assert renderer.placeholder_uses == 1


def test_a_channel_with_no_cell_is_all_zeros() -> None:
    renderer = GoalRenderer(32, 24)
    goal = renderer.render(Command(Primitive.ROLL, None, None, None))

    assert not goal.any()
    assert goal.shape == (2, 24, 32)


def test_task_one_hot_matches_the_configured_task_ids() -> None:
    renderer = GoalRenderer(8, 8)
    assert renderer.task_ids == ("move", "roll", "recover")
    assert np.array_equal(renderer.task_one_hot(Primitive.MOVE), [1, 0, 0])
    assert np.array_equal(renderer.task_one_hot(Primitive.ROLL), [0, 1, 0])
    assert np.array_equal(renderer.task_one_hot(Primitive.RECOVER), [0, 0, 1])


def test_goal_renderer_defaults_come_from_config() -> None:
    renderer = GoalRenderer()
    observation_cfg = config.load("training")["observation"]
    assert [renderer.width, renderer.height] == list(observation_cfg["images"]["top"])
    assert renderer.sigma_px == observation_cfg["goal_sigma_px"]


# --------------------------------------------------------------------------------------------------
# perception (placeholder)
# --------------------------------------------------------------------------------------------------


def test_mock_perception_satisfies_the_protocol_and_its_four_rules() -> None:
    perception = MockPerception()
    assert isinstance(perception, Perception)
    cells = load_cells()
    move = Command(Primitive.MOVE, cells["track-0"], cells["track-5"], "R0")
    before = {"horses": {"R0": "track-0"}, "die": 3}

    unchanged = perception.verify(move, before, before)
    assert unchanged.success is False and unchanged.failure_mode == NO_PROGRESS

    arrived = perception.verify(move, before, {"horses": {"R0": "track-5"}, "die": 3})
    assert arrived.success is True and arrived.observed_state_delta == {"R0": ["track-0", "track-5"]}

    elsewhere = perception.verify(move, before, {"horses": {"R0": "track-4"}, "die": 3})
    assert elsewhere.success is False and elsewhere.failure_mode == "missed_cell"

    roll = Command(Primitive.ROLL, None, None, None)
    rolled = perception.verify(roll, before, {"horses": {"R0": "track-0"}, "die": 5})
    assert rolled.success is True and rolled.observed_state_delta == {"die": [3, 5]}
    nudged = perception.verify(roll, before, {"horses": {"R0": "track-1"}, "die": 3})
    assert nudged.success is False and nudged.failure_mode == "die_out_of_bowl"


def test_state_delta_ignores_bookkeeping() -> None:
    before = {"horses": {"R0": "track-0"}, "die": 1, "turn": 4, "failures": []}
    after = {"horses": {"R0": "track-0"}, "die": 1, "turn": 5, "failures": [{"turn": 4}]}
    assert state_delta(before, after) == {}


# --------------------------------------------------------------------------------------------------
# the CLI and the R2 audit
# --------------------------------------------------------------------------------------------------


def test_real_backend_is_refused_until_the_drivers_exist() -> None:
    with pytest.raises(NotImplementedError, match="R2"):
        build("real")
    assert main(["--backend", "real", "--seconds", "0.1"]) == 2


def test_cli_runs_on_mocks_and_writes_a_heartbeat(tmp_path, capsys, monkeypatch) -> None:
    """The acceptance run, shortened: the real clock, the real CLI, a printed summary."""
    monkeypatch.setattr("runtime.controller.LOG_DIR", tmp_path)
    assert main(["--backend", "mock", "--seconds", "0.4", "--session", "cli"]) == 0

    printed = capsys.readouterr().out
    assert "controller run summary" in printed
    assert "commands executed  1" in printed and "policy calls" in printed
    assert (tmp_path / "controller_cli.heartbeat").read_text(encoding="utf-8").count("\n") >= 1


def test_build_wires_mocks_and_refuses_a_placeholder_policy_on_hardware() -> None:
    controller = build("mock", seed=3, session="wired")
    assert isinstance(controller.summary, RunSummary)
    assert controller.execute == config.load("training")["diffusion"]["execute"] == 8
    assert controller.timeout_ns == 20 * SECOND_NS
    with pytest.raises(NotImplementedError):
        build("real", policy=None)


def test_controller_needs_both_fixed_cameras(tmp_path) -> None:
    clk = FakeClock()
    with pytest.raises(ValueError, match="oblique"):
        Controller(arm=MockArm(now_ns=clk), hand=MockHand(now_ns=clk),
                   cameras={"top": MockCamera("top", now_ns=clk)}, engine=StubEngine(0),
                   policy=HoldPolicy(), perception=MockPerception(), now_ns=clk, log_dir=tmp_path)


def test_runtime_and_board_import_nothing_from_tools_hardware_checks() -> None:
    """R2: scripted motion lives only in tools/hardware_checks/ and never reaches the control path."""
    for package in ("runtime", "board", "policy"):
        for path in sorted((REPO_ROOT / package).rglob("*.py")):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")):
                    assert "hardware_checks" not in stripped, f"{path}: {stripped}"
    for path in ("runtime/controller.py", "runtime/goal.py", "runtime/policy_api.py", "board/perception.py"):
        assert "hardware_checks" not in (REPO_ROOT / path).read_text(encoding="utf-8"), path
