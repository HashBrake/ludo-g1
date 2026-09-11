"""Recorder tests (T-017; CLAUDE.md 5.2, 5.3, 5.6).

Everything runs on the mock drivers and a :class:`FakeClock` the test advances by hand, so a 60 s
episode costs no wall-clock seconds of waiting and the sample grids are exactly the ones a real 60 s
run would have produced. Nothing here is marked ``motion``: the mocks are simulated robots (R1) and
the recorder itself never sends anything at all -- one test asserts exactly that.

Datasets are written under ``tmp_path``, never under ``data/raw/`` (task note).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml
from lerobot.datasets.lerobot_dataset import LeRobotDataset

from drivers.mock import MockArm, MockCamera, MockGlove, MockHand, frame_index
from engine.interface import Cell, Command, Primitive
from runtime import config
from runtime.types import ARM_DOF, MotionCommand
from teleop.recorder import SIDECAR, STREAMS, Recorder

SECOND_NS = 1_000_000_000
#: Small frames for the long tests: a 640x480 PNG costs ~8 ms to write and a 60 s episode writes
#: 5400 of them. The shapes are asserted at the real configured size by its own test below.
SMALL = {"top": [64, 48], "oblique": [64, 48], "palm": [48, 32]}
#: Device-poll rate of the teleop loop (CLAUDE.md 5.2: state at 100 Hz, subsampled to 30 Hz for the
#: dataset). Above the fastest device rate, so every sample of every stream is picked up.
POLL_HZ = 200


class FakeClock:
    """A monotonic nanosecond clock the test moves by hand."""

    def __init__(self, ns: int = 0) -> None:
        self.ns = int(ns)

    def __call__(self) -> int:
        return self.ns

    def advance(self, seconds: float) -> int:
        self.ns += round(seconds * SECOND_NS)
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


def move_command() -> Command:
    return Command(
        primitive=Primitive.MOVE,
        src=Cell("R-base-2", (-120.0, -80.0), None),
        dst=Cell("track-17", (60.0, 40.0), None),
        horse_id="R2",
    )


class Rig:
    """Mock drivers, a recorder, and the 30 Hz teleop loop that feeds it."""

    def __init__(self, tmp_path: Path, *, small: bool = True, operator: str = "alois") -> None:
        self.cfg = config_root(tmp_path, small=small)
        self.clk = FakeClock()
        kw = {"now_ns": self.clk, "config_root": self.cfg}
        self.arm, self.hand, self.glove = MockArm(**kw), MockHand(**kw), MockGlove(**kw)
        self.cameras = {name: MockCamera(name, **kw) for name in ("top", "oblique")}
        self.rec = Recorder(
            "20260912T090000", arm=self.arm, hand=self.hand, cameras=self.cameras, glove=self.glove,
            operator=operator, now_ns=self.clk, root=tmp_path / "sessions", config_root=self.cfg,
        )
        self.root = self.rec.root

    def target(self) -> MotionCommand:
        """A slow teleop target: 0.16 rad/s peak, and the wrist stays inside the workspace box.

        Shoulder roll, elbow and wrist pitch barely move the wrist in +x, which is the box face the
        rest pose sits 30 mm away from (``runtime.fk.left_arm_fk`` at qpos0 is [0.200, 0.149, 0.095] m
        against a box starting at x = 0.17 m). Nothing here is a hard-coded *robot* motion: it is a
        stand-in for the operator, in a test, and R2 is about the deployed system.
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
        measured state (D-018, T-033). So the first command of an episode *is* the measured state and
        the stand-in operator is blended in, which is what ``teleop/loop.py``'s clutch does on the
        real rig. Still no hard-coded robot motion: the shape is the operator stand-in above (R2).
        """
        want, measured = self.target(), self.arm.read_state().payload.joints
        alpha = min(1.0, max(0.0, (self.clk.ns - engaged_ns) / SECOND_NS))
        joints = measured + alpha * (want.joints - measured)
        return MotionCommand(arm=joints[:ARM_DOF], waist_yaw=joints[ARM_DOF], pinch=want.pinch)

    def run(self, seconds: float, command: Command | None = None, *, poll_hz: float = POLL_HZ) -> None:
        """Record one episode: poll at ``poll_hz``, command and tick on the dataset grid."""
        rec = self.rec
        # One dataset period of quiet before the first command of an episode, so the command that
        # opens episode n+1 is not inside the 60 Hz rate limit of the one that closed episode n.
        self.clk.ns += rec.period_ns
        rec.start_episode(command or move_command())
        poll_ns, want = round(SECOND_NS / poll_hz), round(seconds * rec.fps)
        next_tick, budget = self.clk.ns, want * 8 + 1000
        engaged_ns = self.clk.ns
        while rec.episode.frames < want:
            budget -= 1
            assert budget > 0, f"only {rec.episode.frames} of {want} frames after the tick budget"
            if self.clk.ns >= next_tick:
                admitted = self.arm.send_targets(self.engaged_target(engaged_ns))
                self.hand.send_pinch(admitted.pinch)
                rec.tick(admitted)
                # Commands go on the recorder's own frame grid, which is the board camera's.
                next_tick = rec.next_grid_ns(self.clk.ns) or self.clk.ns + rec.period_ns
            else:
                rec.poll()
            self.clk.ns += poll_ns


# --------------------------------------------------------------------------------------------------
# acceptance 1: a 60 s mock episode has all streams, skew p99 < 10 ms, zero dropped frames
# --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def long_session(tmp_path_factory) -> tuple[Rig, dict]:
    """One 60 s episode plus a short second one, recorded once and inspected by several tests."""
    rig = Rig(tmp_path_factory.mktemp("long"))
    rig.run(60.0)
    rig.rec.mark_success()
    first = rig.rec.stop_episode()
    rig.run(2.0, Command(Primitive.ROLL, None, None, None))
    rig.rec.mark_perturbed()
    rig.rec.mark_success(False)
    second = rig.rec.stop_episode()
    rig.rec.close()
    return rig, {"first": first, "second": second}


def test_sixty_second_episode_has_every_stream(long_session) -> None:
    rig, eps = long_session
    meta = eps["first"]
    assert meta.frames == 60 * rig.rec.fps == 1800
    # Every stream produced samples for the whole episode; none was silently absent from alignment.
    for name in STREAMS:
        assert len(rig.rec._seen[name]) > 0, name
    print(f"\nframes={meta.frames} skew p50={meta.skew_p50_ms:.3f} ms p99={meta.skew_p99_ms:.3f} ms "
          f"dropped={meta.dropped} skipped_ticks={meta.skipped_ticks} align_failures={meta.align_failures}")


def test_skew_p99_under_ten_milliseconds(long_session) -> None:
    _rig, eps = long_session
    assert eps["first"].skew_p99_ms < 10.0
    assert eps["second"].skew_p99_ms < 10.0


def test_no_dropped_frames_on_any_stream(long_session) -> None:
    _rig, eps = long_session
    meta = eps["first"]
    assert meta.dropped == dict.fromkeys(STREAMS, 0)
    assert meta.skipped_ticks == 0
    assert meta.align_failures == 0


# --------------------------------------------------------------------------------------------------
# acceptance 2: the dataset loads back with LeRobotDataset, with the expected keys and frame count
# --------------------------------------------------------------------------------------------------


def test_dataset_loads_back_with_lerobot_dataset(long_session) -> None:
    rig, eps = long_session
    back = LeRobotDataset(rig.rec.dataset.repo_id, root=rig.root)
    assert back.num_episodes == 2
    assert back.num_frames == eps["first"].frames + eps["second"].frames
    assert back.fps == rig.rec.fps
    for key in ("observation.images.top", "observation.images.oblique", "observation.images.palm",
                "observation.state", "observation.hand_joints", "observation.glove", "task_id", "action"):
        assert key in back.features, key
    item = back[5]
    assert tuple(item["observation.images.top"].shape) == (3, SMALL["top"][1], SMALL["top"][0])
    assert tuple(item["observation.images.palm"].shape) == (3, SMALL["palm"][1], SMALL["palm"][0])
    assert tuple(item["observation.state"].shape) == (9,)
    assert tuple(item["observation.hand_joints"].shape) == (15,)
    assert tuple(item["observation.glove"].shape) == (17,)
    assert tuple(item["action"].shape) == (9,)
    assert item["task"] == "move from R-base-2 to track-17"


def test_frames_at_the_configured_resolution(tmp_path) -> None:
    """The same round trip at the real config/cameras.yaml sizes, on a short episode."""
    rig = Rig(tmp_path, small=False)
    rig.run(1.0)
    meta = rig.rec.stop_episode()
    rig.rec.close()
    assert meta.frames == 30 and meta.dropped == dict.fromkeys(STREAMS, 0)
    back = LeRobotDataset(rig.rec.dataset.repo_id, root=rig.root)
    assert tuple(back[0]["observation.images.top"].shape) == (3, 480, 640)
    assert tuple(back[0]["observation.images.oblique"].shape) == (3, 480, 640)
    assert tuple(back[0]["observation.images.palm"].shape) == (3, 240, 320)


def test_recorded_frames_are_the_frames_the_cameras_produced(tmp_path) -> None:
    """PNG storage is lossless, so the mock frame counter survives the round trip exactly."""
    rig = Rig(tmp_path)
    rig.run(1.0)
    rig.rec.stop_episode()
    rig.rec.close()
    back = LeRobotDataset(rig.rec.dataset.repo_id, root=rig.root)
    counters = []
    for i in (0, 5, 20):
        frame = (back[i]["observation.images.top"].numpy() * 255.0).round().astype(np.uint8)
        counters.append(frame_index(np.transpose(frame, (1, 2, 0))))
    # One camera frame per dataset frame, in order, with the alignment lag already applied.
    assert counters == [counters[0], counters[0] + 5, counters[0] + 20]


# --------------------------------------------------------------------------------------------------
# acceptance 3: replaying the recorded actions through the mock arm reproduces the joint targets
# --------------------------------------------------------------------------------------------------


def test_replay_reproduces_recorded_joint_targets(tmp_path) -> None:
    rig = Rig(tmp_path)
    rig.run(5.0)
    meta = rig.rec.stop_episode()
    rig.rec.close()
    back = LeRobotDataset(rig.rec.dataset.repo_id, root=rig.root)
    recorded = np.asarray(back.hf_dataset["action"], dtype=np.float64)
    assert recorded.shape == (meta.frames, 9)

    clk = FakeClock()
    arm = MockArm(now_ns=clk, config_root=rig.cfg)
    worst = 0.0
    for row in recorded:
        admitted = arm.send_targets(MotionCommand.from_action(row))
        worst = max(worst, float(np.abs(admitted.to_action() - row).max()))
        clk.advance(1.0 / rig.rec.fps)
    print(f"\nreplay: {len(recorded)} actions, worst |admitted - recorded| = {worst:.3e}")
    assert worst < 1e-6


# --------------------------------------------------------------------------------------------------
# metadata, the dataset card, and the operator keys (5.6)
# --------------------------------------------------------------------------------------------------


def test_episode_metadata_sidecar_holds_the_five_six_fields(long_session) -> None:
    rig, eps = long_session
    lines = (rig.root / SIDECAR).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["task_id"] == "move" and first["src_cell"] == "R-base-2" and first["dst_cell"] == "track-17"
    assert first["success"] is True and first["perturbed"] is False and first["operator"] == "alois"
    assert second["task_id"] == "roll" and second["success"] is False and second["perturbed"] is True
    assert second["src_cell"] is None and second["dst_cell"] is None
    for key, name in (("latency_config_hash", "robot"), ("safety_config_hash", "safety"),
                      ("board_config_hash", "board")):
        assert first[key] == config.config_hash(name, root=rig.cfg)
    # The goal is stored once per episode, not per frame, and is enough to re-render the heatmaps.
    assert first["goal"]["src_px"] is not None and first["goal"]["dst_px"] is not None
    assert second["goal"]["src_px"] is None
    assert first["goal"]["sigma_px"] == pytest.approx(rig.rec._goal.sigma_px)
    assert eps["first"].episode_index == 0 and eps["second"].episode_index == 1


def test_dataset_card_reports_the_measurements(long_session) -> None:
    rig, eps = long_session
    card = (rig.root / "README.md").read_text(encoding="utf-8")
    assert "# LUDO-G1 session `20260912T090000`" in card
    assert f"| frames | {eps['first'].frames + eps['second'].frames} |" in card
    assert f"| dataset rate | {rig.rec.fps} Hz" in card
    assert f"`{config.config_hash('safety', root=rig.cfg)}`" in card
    assert f"{eps['first'].skew_p99_ms:.3f}" in card
    # The latency shifts are placeholders today; the card has to say so rather than imply a measurement.
    assert "latency.arm_ms" in card and "latency.camera_top_ms" in card


def test_goal_heatmaps_are_not_a_dataset_feature(long_session) -> None:
    """2x480x640 float32 per frame is 2.4 MB; the cells in the sidecar re-render it exactly."""
    rig, _eps = long_session
    assert not [k for k in rig.rec.dataset.features if "goal" in k]


# --------------------------------------------------------------------------------------------------
# behaviour: the recorder reads, and only reads (R1, R2)
# --------------------------------------------------------------------------------------------------


def test_recorder_never_commands_anything(tmp_path) -> None:
    rig = Rig(tmp_path)

    class Tripwire:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name in ("send_targets", "send_pinch"):
                raise AssertionError(f"the recorder called {name}()")
            return getattr(self._inner, name)

    rig.rec._arm, rig.rec._hand = Tripwire(rig.arm), Tripwire(rig.hand)
    rig.rec.start_episode(move_command())
    for _ in range(10):
        rig.rec.tick(MotionCommand(arm=np.zeros(ARM_DOF), waist_yaw=0.0, pinch=0.0))
        rig.clk.advance(1.0 / rig.rec.fps)
    assert rig.rec.episode.frames > 0
    rig.rec.stop_episode()
    rig.rec.close()


def test_episode_lifecycle_errors(tmp_path) -> None:
    rig = Rig(tmp_path)
    with pytest.raises(RuntimeError, match="no episode is open"):
        rig.rec.mark_success()
    with pytest.raises(RuntimeError, match="no episode is open"):
        rig.rec.tick(MotionCommand(arm=np.zeros(ARM_DOF), waist_yaw=0.0, pinch=0.0))
    rig.rec.start_episode(move_command())
    with pytest.raises(RuntimeError, match="still open"):
        rig.rec.start_episode(move_command())
    # An episode with no frames is discarded, not written: an empty parquet row group is not a record.
    assert rig.rec.stop_episode() is None
    assert rig.rec.dataset.meta.total_episodes == 0
    assert not (rig.root / SIDECAR).exists()
    rig.rec.close()


def test_recorder_refuses_a_camera_set_without_top_or_oblique(tmp_path) -> None:
    rig_cfg = config_root(tmp_path)
    clk = FakeClock()
    kw = {"now_ns": clk, "config_root": rig_cfg}
    with pytest.raises(ValueError, match=r"\['oblique'\]"):
        Recorder("s", arm=MockArm(**kw), hand=MockHand(**kw), glove=MockGlove(**kw),
                 cameras={"top": MockCamera("top", **kw)}, now_ns=clk, root=tmp_path / "x", config_root=rig_cfg)
