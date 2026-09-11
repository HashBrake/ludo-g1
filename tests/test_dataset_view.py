"""Dataset viewer tests (T-026; CLAUDE.md section 8).

The session comes from the recorder itself, on the mock drivers and the `FakeClock` of
`tests/test_recorder.py`, written under `tmp_path`: the viewer is then audited against a real
lerobot v3.0 session rather than a hand-built directory, which is the whole point of the tool.
Nothing here touches hardware and nothing sends a command (R1, R2).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from engine.interface import Command, Primitive
from tests.test_recorder import Rig
from tools import dataset_view
from tools.dataset_view import COLUMNS, HEADER_H, LEGEND_H, PLOT_H, SEP, TILE_W

#: `tests.test_recorder.SMALL`: top and oblique 64x48, palm 48x32, so with tiles scaled to TILE_W
#: the three camera rows are 180, 180 and 160 px tall, each followed by a 1 px rule.
TOP_H, OBLIQUE_H, PALM_H = 180, 180, 160
EXPECTED_SIZE = (
    COLUMNS * TILE_W + (COLUMNS - 1) * SEP,
    HEADER_H + TOP_H + OBLIQUE_H + PALM_H + 3 * SEP + LEGEND_H + PLOT_H,
)


@pytest.fixture(scope="module")
def session(tmp_path_factory) -> Path:
    """Two recorded episodes: a MOVE (both goal cells) and a ROLL (neither)."""
    rig = Rig(tmp_path_factory.mktemp("view"))
    rig.run(2.0)
    rig.rec.mark_success()
    rig.rec.stop_episode()
    rig.run(1.0, Command(Primitive.ROLL, None, None, None))
    rig.rec.mark_perturbed()
    rig.rec.stop_episode()
    rig.rec.close()
    return rig.root


# --------------------------------------------------------------------------------------------------
# acceptance: one PNG per episode, at the expected size
# --------------------------------------------------------------------------------------------------


def test_render_session_writes_one_png_per_episode(session, capsys) -> None:
    written = dataset_view.render_session(session)
    assert [p.name for p in written] == ["episode_000000.png", "episode_000001.png"]
    assert all(p.parent == session / "strips" for p in written)
    sizes = []
    for path in written:
        image = cv2.imread(str(path))
        assert image is not None, path
        assert (image.shape[1], image.shape[0]) == EXPECTED_SIZE
        sizes.append(path.stat().st_size)
    # The dataset card is printed for the auditor, not just written to disk.
    assert "# LUDO-G1 session `20260912T090000`" in capsys.readouterr().out
    print(f"\nstrips {EXPECTED_SIZE[0]}x{EXPECTED_SIZE[1]} px, "
          f"{', '.join(f'{s / 1024:.0f} kB' for s in sizes)}")


def test_strip_rows_are_the_three_cameras_and_two_plots(session) -> None:
    dataset, metas, _card = dataset_view.load_session(session)
    strip = dataset_view.episode_strip(dataset, metas[0])
    assert (strip.shape[1], strip.shape[0]) == EXPECTED_SIZE
    # Eight columns of camera: the tile boundaries are where the row is cut, and every tile is filled.
    top_row = strip[HEADER_H:HEADER_H + TOP_H]
    for column in range(COLUMNS):
        start = column * (TILE_W + SEP)
        tile = top_row[:, start:start + TILE_W]
        assert tile.shape == (TOP_H, TILE_W, 3)
        assert tile.std() > 0, f"camera tile {column} is flat"


# --------------------------------------------------------------------------------------------------
# acceptance: the goal overlay changes the `top` pixels, and lands on the goal cells
# --------------------------------------------------------------------------------------------------


def test_goal_overlay_pixels_differ_from_the_raw_frame(session) -> None:
    dataset, metas, _card = dataset_view.load_session(session)
    meta = metas[0]
    start, _stop = dataset_view._span(dataset, int(meta["episode_index"]))
    raw = dataset_view._to_bgr(dataset[start]["observation.images.top"])
    layers = dataset_view._goal_layers(meta["goal"], raw.shape[:2])
    assert len(layers) == 2, "a MOVE has a source and a target channel"
    overlaid = dataset_view._overlay(raw, layers)
    assert overlaid.shape == raw.shape
    diff = np.abs(overlaid.astype(np.int32) - raw.astype(np.int32)).sum(axis=2)
    assert diff.max() > 0, "the overlay changed nothing"
    # The channels land on the cells the episode was recorded for: each gaussian peaks within a
    # pixel of the centre the recorder stored. (The *difference* peaks wherever the frame happens to
    # be furthest from the overlay colour, so the check is on the channel, not on the blend.)
    centres = [meta["goal"]["src_px"], meta["goal"]["dst_px"]]
    for (heat, _colour), (u, v) in zip(layers, centres, strict=True):
        peak_v, peak_u = np.unravel_index(int(heat.argmax()), heat.shape)
        assert abs(peak_u - u) <= 1.0 and abs(peak_v - v) <= 1.0
        assert heat[int(round(v)), int(round(u))] == pytest.approx(1.0, abs=0.02)
        assert diff[int(round(v)), int(round(u))] > 0
    print(f"\ngoal overlay: peak |diff| = {int(diff.max())}/765, changed "
          f"{int((diff > 0).sum())}/{diff.size} px; src {tuple(round(c, 1) for c in centres[0])} "
          f"dst {tuple(round(c, 1) for c in centres[1])}")

    # And the same difference survives into the written PNG's first `top` tile.
    strip = dataset_view.episode_strip(dataset, meta)
    tile = strip[HEADER_H:HEADER_H + TOP_H, :TILE_W]
    assert not np.array_equal(tile, dataset_view._tile(raw))
    assert np.array_equal(tile, dataset_view._tile(overlaid))


def test_only_the_top_row_is_overlaid(session) -> None:
    """`oblique` and `palm` are not in the goal frame, so they are shown untouched."""
    dataset, metas, _card = dataset_view.load_session(session)
    strip = dataset_view.episode_strip(dataset, metas[0])
    start, _stop = dataset_view._span(dataset, 0)
    item = dataset[start]
    bands = (("oblique", HEADER_H + TOP_H + SEP, OBLIQUE_H),
             ("palm", HEADER_H + TOP_H + OBLIQUE_H + 2 * SEP, PALM_H))
    for name, offset, height in bands:
        raw = dataset_view._to_bgr(item[f"observation.images.{name}"])
        tile = strip[offset:offset + height, :TILE_W]
        assert np.array_equal(tile, dataset_view._tile(raw)), name


def test_roll_episode_has_no_goal_channels(session) -> None:
    """A ROLL addresses no cell: zero layers, and the `top` row is the raw frame (goal.py's rule)."""
    dataset, metas, _card = dataset_view.load_session(session)
    meta = metas[1]
    assert meta["task_id"] == "roll" and meta["goal"]["src_px"] is None
    assert dataset_view._goal_layers(meta["goal"], (48, 64)) == []
    start, _stop = dataset_view._span(dataset, 1)
    raw = dataset_view._to_bgr(dataset[start]["observation.images.top"])
    strip = dataset_view.episode_strip(dataset, meta)
    assert np.array_equal(strip[HEADER_H:HEADER_H + TOP_H, :TILE_W], dataset_view._tile(raw))


# --------------------------------------------------------------------------------------------------
# the CLI and its errors
# --------------------------------------------------------------------------------------------------


def test_cli_selects_episodes_and_an_output_directory(session, tmp_path) -> None:
    out = tmp_path / "picked"
    assert dataset_view.main([str(session), "--episodes", "1", "--out", str(out)]) == 0
    assert [p.name for p in sorted(out.iterdir())] == ["episode_000001.png"]


def test_unknown_episode_and_unrecorded_directory_are_errors(session, tmp_path) -> None:
    with pytest.raises(KeyError, match="episode"):
        dataset_view.render_session(session, episodes=[7])
    with pytest.raises(FileNotFoundError, match="not a session"):
        dataset_view.load_session(tmp_path)


def test_panels_hold_every_dimension_of_a_move_episode(session) -> None:
    """Nine action dims and nine state dims: the legend names them and the panel plots them all."""
    dataset, metas, _card = dataset_view.load_session(session)
    names = dataset.features["action"]["names"]
    assert len(names) == 9 and names == dataset.features["observation.state"]["names"]
    start, stop = dataset_view._span(dataset, 0)
    series = np.asarray(
        dataset.hf_dataset.select_columns(["action"])[start:stop]["action"], dtype=np.float64
    )
    assert series.shape == (stop - start, 9)
    panel = dataset_view._panel(series, "action (9)", 960, PLOT_H)
    assert panel.shape == (PLOT_H, 960, 3)
    assert len(np.unique(panel.reshape(-1, 3), axis=0)) > len(dataset_view.DIM_BGR), "the panel is blank"
    # The legend swatches are flat fills, so every dimension's colour is there exactly.
    legend = dataset_view._legend([dataset_view._short(n) for n in names], 960)
    for colour in dataset_view.DIM_BGR:
        assert (legend == np.asarray(colour, dtype=np.uint8)).all(axis=2).any(), colour
