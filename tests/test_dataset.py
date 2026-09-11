"""Training dataset tests (T-027; CLAUDE.md 5.3, 5.6, 5.7).

The session is recorded by the recorder itself onto the mock drivers, through the `Rig` of
`tests/test_recorder.py`, under `tmp_path`: what is loaded back is a real lerobot v3.0 session with a
real `episodes_meta.jsonl`, not a hand-built directory. Nothing here touches hardware and nothing
sends a command (R1, R2).
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import torch
import yaml
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from torch.utils.data import DataLoader, RandomSampler

from engine.interface import Cell, Command, Primitive
from policy.dataset import CAMERAS, LudoDataset, episode_metadata, split_cell_pairs
from runtime import config
from tests.test_recorder import SMALL, Rig

#: Samples the benchmark draws (task acceptance: a 1000-sample iteration benchmark).
BENCH_SAMPLES = 1000
#: Batch size the benchmark loads with. Small enough that the 1000 samples are 125 collations of a
#: shape a trainer actually sees, not one enormous one.
BENCH_BATCH = 8


def second_move() -> Command:
    """A second cell pair, so a split has something to hold out."""
    return Command(
        primitive=Primitive.MOVE,
        src=Cell("track-04", (-40.0, 20.0), None),
        dst=Cell("R-home-1", (100.0, -60.0), None),
        horse_id="R1",
    )


@pytest.fixture(scope="module")
def session(tmp_path_factory) -> tuple[Path, Path]:
    """Three episodes: two MOVEs on different cell pairs and a ROLL. Returns (session, config)."""
    rig = Rig(tmp_path_factory.mktemp("dataset"))
    rig.run(2.0)
    rig.rec.mark_success()
    rig.rec.stop_episode()
    rig.run(1.0, second_move())
    rig.rec.mark_success()
    rig.rec.stop_episode()
    rig.run(1.0, Command(Primitive.ROLL, None, None, None))
    rig.rec.stop_episode()
    rig.rec.close()
    return rig.root, rig.cfg


@pytest.fixture(scope="module")
def no_jitter_config(tmp_path_factory, session) -> Path:
    """The session's config with the colour jitter turned off, so only geometry can change a pixel."""
    _root, cfg = session
    out = tmp_path_factory.mktemp("nojitter") / "config"
    out.mkdir()
    for name in config.NAMES:
        data = config.load(name, root=cfg)
        if name == "training":
            data["augmentation"]["color_jitter"] = dict.fromkeys(data["augmentation"]["color_jitter"], 0.0)
        (out / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


def load(session: tuple[Path, Path], **kwargs) -> LudoDataset:
    root, cfg = session
    kwargs.setdefault("config_root", cfg)
    return LudoDataset([root], **kwargs)


# --------------------------------------------------------------------------------------------------
# shapes, dtypes and the observation of CLAUDE.md 5.3
# --------------------------------------------------------------------------------------------------


def test_sample_shapes_and_dtypes(session) -> None:
    data = load(session)
    assert len(data) == 60 + 30 + 30  # every frame of every episode is a sample
    sample = data[0]
    top_h, top_w = SMALL["top"][1], SMALL["top"][0]
    assert tuple(sample["top"].shape) == (5, top_h, top_w)  # 3 RGB + 2 goal channels
    assert tuple(sample["oblique"].shape) == (3, top_h, top_w)
    assert tuple(sample["palm"].shape) == (3, SMALL["palm"][1], SMALL["palm"][0])
    assert tuple(sample["state"].shape) == (9,)
    assert tuple(sample["task_id"].shape) == (3,)
    assert tuple(sample["action"].shape) == (16, 9)
    assert tuple(sample["action_mask"].shape) == (16,)
    for key, value in sample.items():
        assert value.dtype == torch.float32, key
    for name in CAMERAS:
        assert 0.0 <= float(sample[name].min()) and float(sample[name].max()) <= 1.0, name


def test_chunk_size_follows_the_caller_and_the_config(session) -> None:
    assert load(session).chunk == 16  # config/training.yaml diffusion.chunk
    assert tuple(load(session, chunk=32)[0]["action"].shape) == (32, 9)  # act.chunk
    with pytest.raises(ValueError, match="chunk >= 1"):
        load(session, chunk=0)


def test_task_one_hot_matches_the_recorded_primitive(session) -> None:
    data = load(session)
    task_ids = data.task_ids
    seen = {}
    for position, episode in enumerate(data.episodes):
        index = next(i for i, (pos, _frame) in enumerate(data._index) if pos == position)
        one_hot = data[index]["task_id"]
        assert float(one_hot.sum()) == 1.0
        seen[episode.index] = task_ids[int(one_hot.argmax())]
    metas = {int(m["episode_index"]): m["task_id"] for m in episode_metadata(session[0])}
    assert seen == metas
    assert set(seen.values()) == {"move", "roll"}


# --------------------------------------------------------------------------------------------------
# chunk alignment: action[k] is the action recorded k frames later, padded at the episode tail
# --------------------------------------------------------------------------------------------------


def test_action_chunk_is_the_next_sixteen_recorded_actions(session) -> None:
    root, _cfg = session
    data = load(session)
    back = LeRobotDataset(f"ludo-g1/{root.name}", root=root)
    recorded = np.asarray(back.hf_dataset["action"], dtype=np.float32)
    for index in (0, 17, 43):  # each at least a chunk from the end of its episode
        _position, frame = data._index[index]
        chunk = data[index]["action"].numpy()
        assert np.array_equal(chunk, recorded[frame : frame + 16])


def test_the_episode_tail_is_padded_with_the_last_action_and_masked(session) -> None:
    data = load(session)
    episode = data.episodes[0]
    frames = episode.stop - episode.start
    # The last frame of the episode: one real action, fifteen copies of it, and a mask that says so.
    last = next(i for i, (pos, frame) in enumerate(data._index) if pos == 0 and frame == episode.stop - 1)
    sample = data[last]
    assert sample["action_mask"].tolist() == [1.0] + [0.0] * 15
    assert torch.equal(sample["action"][1:], sample["action"][0].expand(15, -1))
    # ... and every sample at least a chunk from the end is entirely real.
    early = next(i for i, (pos, frame) in enumerate(data._index) if pos == 0 and frame == episode.start)
    assert data[early]["action_mask"].tolist() == [1.0] * 16
    assert frames == 60


def test_episodes_from_two_sessions_are_never_chunked_across(session, tmp_path_factory) -> None:
    """Two sessions, and no sample of one carries an action from the other."""
    root, cfg = session
    second = Rig(tmp_path_factory.mktemp("second"))
    second.run(1.0)
    second.rec.stop_episode()
    second.rec.close()
    data = LudoDataset([root, second.root], config_root=cfg)
    assert len(data) == 120 + 30
    assert {e.session for e in data.episodes} == {0, 1}
    for episode in data.episodes:
        for offset in (0, episode.stop - episode.start - 1):
            index = next(
                i for i, (pos, frame) in enumerate(data._index)
                if data.episodes[pos] is episode and frame == episode.start + offset
            )
            reach = int(data[index]["action_mask"].sum())
            assert reach == min(16, episode.stop - episode.start - offset)


# --------------------------------------------------------------------------------------------------
# the goal channels (5.3): rendered from the sidecar, on the recorded cell pixels
# --------------------------------------------------------------------------------------------------


def test_goal_channels_peak_on_the_recorded_cell_pixels(session) -> None:
    root, _cfg = session
    data = load(session)
    meta = episode_metadata(root)[0]
    goal = meta["goal"]
    sample = data[0]
    height, width = sample["top"].shape[-2:]
    for channel, key in enumerate(("src_px", "dst_px")):
        heat = sample["top"][3 + channel]
        assert float(heat.max()) == pytest.approx(1.0, abs=1e-3)
        peak = np.unravel_index(int(heat.argmax()), (height, width))
        want_u = goal[key][0] * (width - 1) / (goal["frame"][0] - 1)
        want_v = goal[key][1] * (height - 1) / (goal["frame"][1] - 1)
        assert abs(peak[1] - want_u) <= 1.0 and abs(peak[0] - want_v) <= 1.0


def test_a_primitive_with_no_cells_has_empty_goal_channels(session) -> None:
    """A ROLL addresses no cell: the channels are zero (no goal), not a gaussian at the origin."""
    data = load(session)
    roll = next(pos for pos, e in enumerate(data.episodes) if e.pair == (None, None))
    index = next(i for i, (pos, _frame) in enumerate(data._index) if pos == roll)
    assert float(data[index]["top"][3:].abs().max()) == 0.0


def test_goal_channels_are_rendered_once_per_episode(session) -> None:
    data = load(session)
    _ = data[0], data[1], data[61]
    assert sorted(data._goal_cache) == [0, 1]


# --------------------------------------------------------------------------------------------------
# augmentation (5.7): never geometric on `top`, reproducible from the seed
# --------------------------------------------------------------------------------------------------


def test_top_rgb_is_never_geometrically_altered(session, no_jitter_config) -> None:
    """With the jitter at zero strength, anything left that changes a `top` pixel is geometry."""
    root, _cfg = session
    plain = LudoDataset([root], config_root=no_jitter_config)
    augmented = LudoDataset([root], augment=True, seed=7, config_root=no_jitter_config)
    changed_oblique = changed_palm = changed_goal = 0
    for index in range(0, 60, 7):
        a, b = plain[index], augmented[index]
        assert torch.equal(a["top"][:3], b["top"][:3]), f"the `top` RGB changed at sample {index}"
        assert torch.equal(a["state"], b["state"]) and torch.equal(a["action"], b["action"])
        changed_oblique += int(not torch.equal(a["oblique"], b["oblique"]))
        changed_palm += int(not torch.equal(a["palm"], b["palm"]))
        changed_goal += int(not torch.equal(a["top"][3:], b["top"][3:]))
    # The crop and the blur did happen: an unchanged `top` RGB is not an augmentation that no-opped.
    assert changed_oblique >= 8 and changed_palm >= 8 and changed_goal >= 8


def test_colour_jitter_changes_every_camera_including_top(session) -> None:
    root, cfg = session
    plain = LudoDataset([root], config_root=cfg)
    augmented = LudoDataset([root], augment=True, seed=3, config_root=cfg)
    for name in CAMERAS:
        changed = sum(not torch.equal(plain[i][name][:3], augmented[i][name][:3]) for i in range(0, 60, 7))
        assert changed >= 8, f"the colour jitter never touched {name}"


def test_augmentation_is_reproducible_from_the_seed(session) -> None:
    root, cfg = session
    a = LudoDataset([root], augment=True, seed=11, config_root=cfg)
    b = LudoDataset([root], augment=True, seed=11, config_root=cfg)
    c = LudoDataset([root], augment=True, seed=12, config_root=cfg)
    for index in (0, 23, 88):
        for key in ("top", "oblique", "palm"):
            assert torch.equal(a[index][key], b[index][key]), key
    assert not torch.equal(a[0]["top"], c[0]["top"])
    # Two samples of the same episode draw different augmentations (the seed is per item, not per run).
    assert not torch.equal(a[0]["oblique"], a[1]["oblique"])
    # A seed that was not given is still recorded, so a run can be reproduced after the fact.
    assert LudoDataset([root], augment=True, config_root=cfg).seed >= 0


def test_geometric_on_top_is_refused_rather_than_assumed(session, tmp_path) -> None:
    root, cfg = session
    out = tmp_path / "config"
    out.mkdir()
    for name in config.NAMES:
        data = config.load(name, root=cfg)
        if name == "training":
            data["augmentation"]["geometric_on_top"] = True
        (out / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(config.ConfigError, match="geometric_on_top"):
        LudoDataset([root], config_root=out)
    for name in config.NAMES:
        data = config.load(name, root=cfg)
        if name == "training":
            data["augmentation"]["geometric_on_top"] = False
            data["augmentation"]["random_crop_cameras"] = ["top", "oblique"]
        (out / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(config.ConfigError, match="random_crop_cameras"):
        LudoDataset([root], config_root=out)


# --------------------------------------------------------------------------------------------------
# the cell-pair split (eval/protocol.py holds these out)
# --------------------------------------------------------------------------------------------------


def test_split_is_reproducible_and_disjoint(session) -> None:
    root, _cfg = session
    pairs = [("R-base-2", "track-17"), ("track-04", "R-home-1")]
    train, held = split_cell_pairs([root], 0.5, seed=0)
    again, held_again = split_cell_pairs([root], 0.5, seed=0)
    assert (train, held) == (again, held_again)
    assert sorted(train + held) == pairs  # the ROLL episode contributes no pair
    assert not set(train) & set(held)
    assert train == sorted(train) and held == sorted(held)
    # A different seed can choose differently; over these two pairs both splits are 1 + 1.
    assert len(split_cell_pairs([root], 0.5, seed=5)[1]) == 1


def test_split_fraction_bounds(session) -> None:
    root, _cfg = session
    assert split_cell_pairs([root], 0.0, seed=0)[1] == []
    assert len(split_cell_pairs([root], 1.0, seed=0)[1]) == 2
    # Rounding to zero would leave an eval set silently empty, so a positive fraction holds out one.
    assert len(split_cell_pairs([root], 0.01, seed=0)[1]) == 1
    with pytest.raises(ValueError, match="held_out_fraction"):
        split_cell_pairs([root], 1.5, seed=0)


def test_split_selects_the_episodes_of_those_pairs(session) -> None:
    root, cfg = session
    train, held = split_cell_pairs([root], 0.5, seed=0)
    train_data = LudoDataset([root], split=train, config_root=cfg)
    held_data = LudoDataset([root], split=held, config_root=cfg)
    assert {e.pair for e in train_data.episodes} == set(train)
    assert {e.pair for e in held_data.episodes} == set(held)
    assert len(train_data) + len(held_data) == 90  # the ROLL episode is in neither
    # ROLL episodes are kept by naming their (None, None) pair explicitly.
    with_roll = LudoDataset([root], split=[*train, (None, None)], config_root=cfg)
    assert len(with_roll) == len(train_data) + 30


def test_a_session_without_a_sidecar_is_refused(tmp_path) -> None:
    (tmp_path / "meta").mkdir()
    with pytest.raises(FileNotFoundError, match="episodes_meta.jsonl"):
        episode_metadata(tmp_path)
    with pytest.raises(ValueError, match="at least one session"):
        LudoDataset([])


# --------------------------------------------------------------------------------------------------
# acceptance: the 1000-sample iteration benchmark
# --------------------------------------------------------------------------------------------------


def test_iteration_benchmark(session) -> None:
    """Samples per second through a DataLoader, with no worker and with two.

    The mock session is 120 frames, so the 1000 samples are drawn with replacement: the measurement
    is of 1000 sample *fetches* (three PNG decodes, one goal lookup, one augmentation each), which is
    what a training epoch costs per sample. The frames are `tests.test_recorder.SMALL`, so these
    numbers are an upper bound on the rate at the real 640x480 resolution.

    Both legs run with one torch thread: a DataLoader worker sets `torch.set_num_threads(1)` itself
    (`torch/utils/data/_utils/worker.py`), and on 3x48x64 tensors the default thread pool costs about
    three times more than it saves, so measuring the `num_workers=0` leg with the pool on would be
    measuring thread contention rather than the loader.
    """
    root, cfg = session
    data = LudoDataset([root], augment=True, seed=0, config_root=cfg)
    rates = {}
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        for workers in (0, 2):
            sampler = RandomSampler(data, replacement=True, num_samples=BENCH_SAMPLES, generator=torch.Generator())
            loader = DataLoader(data, batch_size=BENCH_BATCH, sampler=sampler, num_workers=workers)
            seen, started = 0, time.perf_counter()
            for batch in loader:
                seen += int(batch["top"].shape[0])
            rates[workers] = seen / (time.perf_counter() - started)
            assert seen == BENCH_SAMPLES
    finally:
        torch.set_num_threads(threads)
    print(f"\nbenchmark ({BENCH_SAMPLES} samples, batch {BENCH_BATCH}, {SMALL['top'][0]}x{SMALL['top'][1]} frames, "
          f"1 torch thread): " + ", ".join(f"num_workers={w}: {r:.0f} samples/s" for w, r in rates.items()))
    assert min(rates.values()) > 0.0
