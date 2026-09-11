"""Operator UI tests (T-025; CLAUDE.md 5.3, 5.5, 5.6).

Everything runs headless on the mock drivers, the stub engine and a :class:`FakeClock` the test moves
by hand, so a 30 s collection session costs no wall-clock seconds of waiting. Nothing here is marked
``motion``: the UI sends nothing at all (the actions it hands the recorder are built by the test, the
way the teleop loop will build them), and the mocks are simulated robots (R1).

Datasets are written under ``tmp_path``, never under ``data/raw/`` (task note).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from drivers.mock import MockArm, MockCamera, MockGlove, MockHand
from engine.cells import load_cells, load_layout
from engine.interface import Command, Primitive
from engine.stub import StubEngine
from runtime import config
from runtime.goal import GoalRenderer
from runtime.types import ARM_DOF, MotionCommand
from teleop.loop import ClutchState
from teleop.operator_ui import ABORTED, MARKED_FAILURE, OperatorUI, UIState
from teleop.recorder import SIDECAR, Recorder

SECOND_NS = 1_000_000_000
#: Small frames for the session tests, as in tests/test_recorder.py: a 640x480 PNG costs ~8 ms to
#: write. The pixel test uses the real configured size instead.
SMALL = {"top": [64, 48], "oblique": [64, 48], "palm": [48, 32]}
#: Device-poll rate of the teleop loop (5.2: state at 100 Hz). Above the fastest device's rate.
POLL_HZ = 200


class FakeClock:
    """A monotonic nanosecond clock the test moves by hand."""

    def __init__(self, ns: int = 0) -> None:
        self.ns = int(ns)

    def __call__(self) -> int:
        return self.ns


def config_root(tmp_path: Path, *, small: bool = True) -> Path:
    """A copy of ``config/``, optionally with the camera frames shrunk so the tests stay fast."""
    root = tmp_path / "config"
    root.mkdir(exist_ok=True)
    for name in config.NAMES:
        data = config.load(name)
        if small and name == "cameras":
            for cam, size in SMALL.items():
                data[cam]["policy_resolution"] = list(size)
        if small and name == "training":
            data["observation"]["images"] = {k: list(v) for k, v in SMALL.items()}
        (root / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


class Rig:
    """Mock drivers, a recorder, a stub engine and the UI over them, all on one fake clock."""

    def __init__(self, tmp_path: Path, *, small: bool = True, script: list[Command] | None = None,
                 seed: int = 0) -> None:
        self.cfg = config_root(tmp_path, small=small)
        self.clk = FakeClock()
        kw = {"now_ns": self.clk, "config_root": self.cfg}
        self.arm, self.hand, self.glove = MockArm(**kw), MockHand(**kw), MockGlove(**kw)
        self.cameras = {name: MockCamera(name, **kw) for name in ("top", "oblique")}
        self.rec = Recorder(
            "20260912T120000", arm=self.arm, hand=self.hand, cameras=self.cameras, glove=self.glove,
            operator="alois", now_ns=self.clk, root=tmp_path / "sessions", config_root=self.cfg,
        )
        self.engine = StubEngine(seed, script, cells=load_cells(self.cfg), layout=load_layout(self.cfg))
        self.ui = OperatorUI(engine=self.engine, recorder=self.rec, cameras=self.cameras,
                             now_ns=self.clk, config_root=self.cfg)
        self.root = self.rec.root

    def target(self) -> MotionCommand:
        """A slow stand-in for the operator: 0.16 rad/s peak, wrist inside the workspace box.

        Copied from tests/test_recorder.py. It is a test's stand-in for a human hand, not a robot
        trajectory: R2 is about the deployed system, and nothing in ``teleop/operator_ui.py``
        produces a target of its own.
        """
        phase = 2.0 * math.pi * (self.clk.ns / 1e9) / 8.0
        arm = np.zeros(ARM_DOF)
        arm[1] = 0.2 * math.sin(phase)
        arm[3] = 0.15 * math.sin(phase + 1.0)
        arm[5] = 0.1 * math.sin(phase + 2.0)
        return MotionCommand(arm=arm, waist_yaw=0.05 * math.sin(phase), pinch=0.5 + 0.4 * math.sin(phase))

    def engaged_target(self, engaged_ns: int) -> MotionCommand:
        """:meth:`target` blended in from the arm's measured state over one second.

        A stream may not step the arm when it starts: ``config/safety.yaml``
        ``first_command_max_step_rad`` refuses a first command further than 0.05 rad from the
        measured state (D-018, T-033), and every :meth:`record` call after an idle is a fresh stream.
        This is the test's stand-in for ``teleop/loop.py``'s clutch, which does exactly this.
        """
        want, measured = self.target(), self.arm.read_state().payload.joints
        alpha = min(1.0, max(0.0, (self.clk.ns - engaged_ns) / SECOND_NS))
        joints = measured + alpha * (want.joints - measured)
        return MotionCommand(arm=joints[:ARM_DOF], waist_yaw=joints[ARM_DOF], pinch=want.pinch)

    def idle(self, seconds: float) -> None:
        """Advance the clock with no episode open, polling the devices as the real loop would."""
        until = self.clk.ns + round(seconds * SECOND_NS)
        while self.clk.ns < until:
            self.rec.poll()
            self.clk.ns += round(SECOND_NS / POLL_HZ)

    def record(self, seconds: float) -> int:
        """Drive the teleop loop for ``seconds`` while the UI is recording. Returns frames written."""
        until = self.clk.ns + round(seconds * SECOND_NS)
        next_tick, frames, engaged_ns = self.clk.ns, 0, self.clk.ns
        while self.clk.ns < until:
            if self.clk.ns >= next_tick:
                admitted = self.arm.send_targets(self.engaged_target(engaged_ns))
                self.hand.send_pinch(admitted.pinch)
                frames += int(self.ui.tick(admitted))
                next_tick = self.rec.next_grid_ns(self.clk.ns) or self.clk.ns + self.rec.period_ns
            else:
                self.rec.poll()
            self.clk.ns += round(SECOND_NS / POLL_HZ)
        return frames


def watch_reports(rig: Rig) -> list:
    """Record every :class:`~engine.interface.Outcome` the UI reports, and still report it on."""
    reported, original = [], rig.engine.report

    def spy(outcome) -> None:
        reported.append(outcome)
        original(outcome)

    rig.engine.report = spy
    return reported


def move_script(cfg: Path) -> list[Command]:
    """One MOVE between two cells that are far apart and well inside the frame."""
    cells = load_cells(cfg)
    return [Command(Primitive.MOVE, cells["R-base-0"], cells["track-17"], "R0")]


# --------------------------------------------------------------------------------------------------
# the state machine: which key does what, and in which state
# --------------------------------------------------------------------------------------------------


def test_keys_walk_idle_armed_recording_stopped(tmp_path) -> None:
    rig = Rig(tmp_path)
    ui = rig.ui
    # The engine has a command, so the UI arms itself: the operator is shown what to do before moving.
    assert ui.state is UIState.ARMED and ui.command is not None
    assert ui.handle_key("x") is UIState.ARMED      # stop before start does nothing
    assert ui.handle_key("y") is UIState.ARMED      # and so does a verdict on nothing
    assert ui.handle_key("z") is UIState.ARMED      # an unbound key is not an action
    assert ui.handle_key("s") is UIState.RECORDING
    frames = rig.record(1.0)
    assert ui.elapsed_s == pytest.approx(1.0, abs=0.05) and frames >= 28
    assert ui.handle_key("p") is UIState.RECORDING and ui.perturbed is True
    assert ui.handle_key("p") is UIState.RECORDING and ui.perturbed is False
    ui.handle_key("p")
    assert ui.handle_key("x") is UIState.STOPPED
    # An episode that is stopped but not marked is still open: the verdict is the operator's.
    assert rig.rec.episode is not None and rig.rec.episodes == []
    assert ui.handle_key("y") is UIState.ARMED
    assert len(rig.rec.episodes) == 1
    meta = rig.rec.episodes[0]
    assert meta.success is True and meta.perturbed is True and meta.frames == frames
    rig.rec.close()


def test_mark_failure_stops_first_and_labels_the_outcome(tmp_path) -> None:
    rig = Rig(tmp_path)
    reported = watch_reports(rig)
    rig.ui.handle_key("s")
    rig.record(0.5)
    # `n` straight out of RECORDING: it stops, marks, writes the episode and re-arms in one press.
    assert rig.ui.handle_key("n") is UIState.ARMED
    assert rig.rec.episodes[0].success is False and rig.rec.episodes[0].frames > 0
    assert len(reported) == 1 and reported[0].success is False
    assert reported[0].failure_mode == MARKED_FAILURE
    rig.rec.close()


def test_abort_discards_the_episode_and_the_engine_asks_for_a_recovery(tmp_path) -> None:
    rig = Rig(tmp_path)
    reported = watch_reports(rig)
    rig.ui.handle_key("s")
    rig.record(0.5)
    assert rig.rec.episode.frames > 0
    assert rig.ui.handle_key("a") is UIState.ARMED
    # Nothing was written, and the engine -- not the UI -- decided what happens to the failed command.
    assert rig.rec.episodes == [] and rig.rec.dataset.meta.total_episodes == 0
    assert not (rig.root / SIDECAR).exists()
    assert rig.ui.aborted == 1
    assert len(reported) == 1 and reported[0].success is False and reported[0].failure_mode == ABORTED
    assert rig.ui.command.primitive is Primitive.RECOVER
    rig.rec.close()


def test_quit_aborts_whatever_is_open(tmp_path) -> None:
    rig = Rig(tmp_path)
    rig.ui.handle_key("s")
    rig.record(0.2)
    rig.ui.handle_key("q")
    assert rig.ui.quit_requested is True and rig.ui.aborted == 1 and rig.rec.episodes == []
    with pytest.raises(RuntimeError, match="headless"):
        rig.ui.run_window()
    rig.rec.close()


def test_an_exhausted_engine_leaves_the_ui_idle(tmp_path) -> None:
    rig = Rig(tmp_path, script=[])
    assert rig.ui.state is UIState.IDLE and rig.ui.command is None
    for key in ("s", "x", "y", "n", "p", "a"):
        assert rig.ui.handle_key(key) is UIState.IDLE
    assert rig.rec.episodes == [] and rig.rec.episode is None
    rig.rec.close()


def test_keys_come_from_the_config_and_must_be_complete(tmp_path) -> None:
    rig = Rig(tmp_path)
    data = config.load("training", root=rig.cfg)
    data["operator_ui"]["keys"]["stop"] = data["operator_ui"]["keys"]["start"]
    (rig.cfg / "training.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(config.ConfigError, match="operator_ui.keys"):
        OperatorUI(engine=rig.engine, recorder=rig.rec, cameras=rig.cameras,
                   now_ns=rig.clk, config_root=rig.cfg)
    rig.rec.close()


def test_the_ui_refuses_a_camera_set_without_top(tmp_path) -> None:
    rig = Rig(tmp_path)
    with pytest.raises(ValueError, match="'top' camera"):
        OperatorUI(engine=rig.engine, recorder=rig.rec, cameras={"oblique": rig.cameras["oblique"]},
                   now_ns=rig.clk, config_root=rig.cfg)
    rig.rec.close()


# --------------------------------------------------------------------------------------------------
# the display: the two goal markers, at the pixels runtime/goal.py reports, and nothing else
# --------------------------------------------------------------------------------------------------


def test_render_marks_the_two_goal_cells_and_leaves_the_rest_of_the_frame_alone(tmp_path) -> None:
    rig = Rig(tmp_path, small=False, script=move_script(config_root(tmp_path, small=False)))
    ui, cells = rig.ui, load_cells(rig.cfg)
    assert ui.command.primitive is Primitive.MOVE
    # No calibration exists, so the pixels are runtime/goal.py's documented placeholder map.
    assert cells["R-base-0"].top_px is None
    goal = GoalRenderer(config_root=rig.cfg)
    centres = [goal.placeholder_px(cells[name]) for name in ("R-base-0", "track-17")]

    raw = rig.cameras["top"].grab().payload
    rendered = ui.render()
    ui_cfg = config.load("training", root=rig.cfg)["operator_ui"]
    height, width = raw.shape[:2]
    assert rendered.shape == (height + ui_cfg["banner_height_px"], width, 3)

    image = rendered[:height]
    changed = np.any(image != raw, axis=2)
    ys, xs = np.nonzero(changed)
    assert changed.any(), "render() drew no marker at all"
    # Every changed pixel is on one of the two markers ...
    distance = np.min([np.hypot(xs - u, ys - v) for u, v in centres], axis=0)
    tolerance = ui_cfg["marker_radius_px"] + ui_cfg["marker_thickness_px"]
    assert distance.max() <= tolerance, f"a pixel {distance.max():.1f} px from any goal cell changed"
    # ... and each marker's centre dot is drawn, so both cells are actually visible.
    for u, v in centres:
        assert changed[int(round(v)), int(round(u))], f"no marker centre at {(u, v)}"
    print(f"\nmarkers at {[(round(u, 1), round(v, 1)) for u, v in centres]}: "
          f"{changed.sum()} px changed, max distance from a cell {distance.max():.1f} px "
          f"(radius {ui_cfg['marker_radius_px']} + thickness {ui_cfg['marker_thickness_px']})")
    rig.rec.close()


def test_the_banner_says_what_to_do_and_what_is_happening(tmp_path) -> None:
    rig = Rig(tmp_path, script=move_script(config_root(tmp_path)))
    lines = rig.ui.lines()
    assert "MOVE" in lines[0] and "R-base-0" in lines[0] and "track-17" in lines[0]
    assert "ARMED" in lines[1] and "0 frames" in lines[1] and "perturbed no" in lines[1]
    assert "s=start" in lines[2] and "y=success" in lines[2] and "p=perturbed" in lines[2]
    rig.ui.handle_key("s")
    rig.record(0.5)
    assert "RECORDING" in rig.ui.lines()[1] and f"{rig.rec.episode.frames} frames" in rig.ui.lines()[1]
    rig.rec.close()


def test_the_ui_never_sends_anything(tmp_path) -> None:
    """R2/R1: the UI reads the board camera and writes files; targets come from the teleop loop."""
    rig = Rig(tmp_path)

    class Tripwire:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name in ("send_targets", "send_pinch"):
                raise AssertionError(f"the operator UI called {name}()")
            return getattr(self._inner, name)

    rig.rec._arm, rig.rec._hand = Tripwire(rig.arm), Tripwire(rig.hand)
    rig.ui._top = Tripwire(rig.cameras["top"])
    rig.ui.handle_key("s")
    for _ in range(10):
        rig.ui.tick(MotionCommand(arm=np.zeros(ARM_DOF), waist_yaw=0.0, pinch=0.0))
        rig.ui.render()
        rig.clk.ns += rig.rec.period_ns
    rig.ui.handle_key("y")
    assert rig.rec.episodes[0].frames > 0
    rig.rec.close()


# --------------------------------------------------------------------------------------------------
# acceptance: a 30 s headless mock session records 2 episodes with the right metadata
# --------------------------------------------------------------------------------------------------


def test_thirty_second_headless_session_records_two_episodes(tmp_path) -> None:
    # Seed 2 is the seed whose first two commands are a ROLL and then a MOVE out of base, so the two
    # episodes cover a primitive with no cells and one with both (CLAUDE.md 5.5, 5.6).
    rig = Rig(tmp_path, seed=2)
    ui = rig.ui
    start_ns, commands, frames = rig.clk.ns, [], []

    rig.idle(2.0)                                   # the operator reads the goal display
    commands.append(ui.command)
    assert ui.handle_key("s") is UIState.RECORDING
    frames.append(rig.record(10.0))
    assert ui.handle_key("x") is UIState.STOPPED
    rig.idle(1.0)
    assert ui.handle_key("y") is UIState.ARMED      # marked a success

    rig.idle(3.0)                                   # the scene is reset between episodes
    commands.append(ui.command)
    assert ui.handle_key("s") is UIState.RECORDING
    frames.append(rig.record(10.0))
    ui.handle_key("p")                              # someone reached over the board
    assert ui.handle_key("x") is UIState.STOPPED
    rig.idle(1.0)
    assert ui.handle_key("n") is UIState.ARMED      # marked a failure
    rig.idle(3.0)
    rig.rec.close()

    elapsed_s = (rig.clk.ns - start_ns) / 1e9
    assert elapsed_s == pytest.approx(30.0, abs=0.05)
    assert len(rig.rec.episodes) == 2 and ui.aborted == 0

    lines = (rig.root / SIDECAR).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert [m["task_id"] for m in (first, second)] == ["roll", "move"]
    assert (second["src_cell"], second["dst_cell"], second["horse_id"]) == ("R-base-0", "track-12", "R0")
    for meta, command, success, perturbed in ((first, commands[0], True, False),
                                              (second, commands[1], False, True)):
        assert meta["task_id"] == command.primitive.value
        assert meta["src_cell"] == (None if command.src is None else command.src.id)
        assert meta["dst_cell"] == (None if command.dst is None else command.dst.id)
        assert meta["horse_id"] == command.horse_id
        assert meta["success"] is success and meta["perturbed"] is perturbed
        assert meta["operator"] == "alois" and meta["frames"] >= 295
        assert meta["skew_p99_ms"] < 10.0
        assert meta["dropped"] == dict.fromkeys(meta["dropped"], 0)
    print(f"\n30 s session: {elapsed_s:.1f} s, {len(rig.rec.episodes)} episodes, "
          f"{[m['task_id'] for m in (first, second)]}, {[m['frames'] for m in (first, second)]} frames, "
          f"success={[m['success'] for m in (first, second)]}, "
          f"perturbed={[m['perturbed'] for m in (first, second)]}, "
          f"skew p99 {[round(m['skew_p99_ms'], 3) for m in (first, second)]} ms, aborted={ui.aborted}")
    assert [m["frames"] for m in (first, second)] == frames


# --------------------------------------------------------------------------------------------------
# the clutch (D-018, T-033): one key out, one state and one number in
# --------------------------------------------------------------------------------------------------


class StubClutch:
    """What :class:`teleop.operator_ui.ClutchView` allows: ask to engage, read a state and a number.

    The real one is :class:`teleop.loop.Clutch` (tested in tests/test_teleop_loop.py). This stub is
    here to prove the UI does nothing else to it -- it never engages one by itself, and it cannot
    overrule a refusal.
    """

    def __init__(self, *, engages: bool = True, worst_distance_rad: float = 0.012) -> None:
        self.state, self.worst_distance_rad = ClutchState.DISENGAGED, worst_distance_rad
        self.engages, self.calls = engages, 0

    def request_engage(self) -> bool:
        self.calls += 1
        if self.engages:
            self.state = ClutchState.ENGAGED
        return self.engages


def test_the_engage_key_reaches_the_clutch_and_the_banner_shows_its_state(tmp_path) -> None:
    rig = Rig(tmp_path)
    clutch = StubClutch()
    ui = rig.ui
    ui.clutch = clutch  # the teleop loop attaches its own the same way (teleop/loop.py)
    assert "e=engage" in ui.lines()[2]
    assert "clutch disengaged 0.012" in ui.lines()[0]
    assert ui.handle_key("e") is ui.state and clutch.calls == 1
    assert "clutch engaged 0.012" in ui.lines()[0]
    rig.rec.close()


def test_the_ui_reports_a_refused_engage_and_never_engages_anything_itself(tmp_path) -> None:
    rig = Rig(tmp_path)
    clutch = StubClutch(engages=False, worst_distance_rad=0.44)
    ui = rig.ui
    ui.clutch = clutch
    assert ui.engage() is False and clutch.state is ClutchState.DISENGAGED
    # Every other key, in every state, leaves the clutch alone: only `e` may ask it for anything.
    for key in ("s", "p", "x", "y", "a", "q"):
        ui.handle_key(key)
    assert clutch.calls == 1
    rig.rec.close()


def test_a_ui_without_a_clutch_ignores_the_engage_key(tmp_path) -> None:
    rig = Rig(tmp_path)
    assert rig.ui.clutch is None
    assert rig.ui.engage() is False
    assert "clutch" not in rig.ui.lines()[0]
    assert rig.ui.handle_key("e") is UIState.ARMED  # inert, not an error
    rig.rec.close()
