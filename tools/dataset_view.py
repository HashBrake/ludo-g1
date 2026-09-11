#!/usr/bin/env python3
"""Frame strips for the dataset audit of CLAUDE.md section 8 (T-026).

Section 8 asks Fable to view five random episodes of every session and to check that the goal
heatmaps land on the right cells. This renders one PNG per episode so that check is a glance rather
than a notebook: the three cameras at eight evenly spaced instants, the `top` row carrying the two
goal channels `runtime/goal.py` renders from the cells the episode was recorded for, and the nine
action and nine state dimensions over the whole episode underneath.

    .venv/bin/python -m tools.dataset_view data/raw/20260912T090000
    .venv/bin/python -m tools.dataset_view data/raw/20260912T090000 --episodes 0,3 --out /tmp/strips

Read-only: it opens a recorded session and writes PNGs, nothing else (R1 is not even in reach here).
Every number on a strip is read from the session, never recomputed -- the skew is the recorder's
measurement from `episodes_meta.jsonl`, not this tool's opinion (R5).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset

from engine.interface import Cell
from runtime.goal import GoalRenderer
from teleop.recorder import SIDECAR

__all__ = ["episode_strip", "load_session", "render_session"]

#: Frames sampled per episode, and the width every camera tile is scaled to (the palm camera is
#: smaller than the other two, so this is the "upscaled to the top width" of the task note).
COLUMNS, TILE_W = 8, 240
HEADER_H, LEGEND_H, PLOT_H = 30, 24, 220
#: A 1 px rule between tiles and under each camera row. Eight instants of a mostly static board look
#: alike, so without it an auditor cannot tell where one frame ends and the next begins.
SEP, RULE = 1, 225
#: Goal channels: source, then target (runtime/goal.py render order). BGR.
GOAL_BGR: tuple[tuple[int, int, int], ...] = ((80, 230, 80), (230, 80, 230))
GOAL_ALPHA = 0.65
FONT, SCALE = cv2.FONT_HERSHEY_SIMPLEX, 0.4
#: One colour per action/state dimension (9), reused by both panels and the shared legend.
DIM_BGR: tuple[tuple[int, int, int], ...] = (
    (200, 60, 60), (60, 160, 60), (60, 60, 210), (180, 140, 40), (170, 60, 180),
    (40, 160, 190), (110, 110, 110), (20, 90, 220), (10, 10, 10),
)
CAMERAS = ("top", "oblique", "palm")


def load_session(root: Path | str) -> tuple[LeRobotDataset, list[dict[str, Any]], str]:
    """Open a recorded session: the lerobot dataset, the sidecar metadata, the dataset card.

    The repo id is not stored in `meta/info.json`, so it is rebuilt the way `teleop/recorder.py`
    composes it (`ludo-g1/<session_id>`); with `root` present nothing is fetched from the hub.
    """
    root = Path(root)
    sidecar = root / SIDECAR
    if not sidecar.exists():
        raise FileNotFoundError(f"{sidecar} is missing: {root} is not a session written by teleop/recorder.py")
    metas = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()]
    card = (root / "README.md").read_text(encoding="utf-8") if (root / "README.md").exists() else ""
    return LeRobotDataset(f"ludo-g1/{root.name}", root=root), metas, card


def _span(dataset: LeRobotDataset, episode_index: int) -> tuple[int, int]:
    """The episode's half-open range of global frame indices."""
    episodes = dataset.meta.episodes
    rows = [i for i, index in enumerate(episodes["episode_index"]) if int(index) == episode_index]
    if not rows:
        raise KeyError(f"episode {episode_index} is in {SIDECAR} but not in the dataset")
    return int(episodes["dataset_from_index"][rows[0]]), int(episodes["dataset_to_index"][rows[0]])


def _tile(image: np.ndarray, width: int = TILE_W) -> np.ndarray:
    """One camera frame scaled to the common column width, aspect preserved."""
    height = max(round(image.shape[0] * width / image.shape[1]), 1)
    interp = cv2.INTER_AREA if width < image.shape[1] else cv2.INTER_NEAREST
    return cv2.resize(image, (width, height), interpolation=interp)


def _to_bgr(tensor: Any) -> np.ndarray:
    """A lerobot image item (CHW float32 in [0, 1], RGB) as an OpenCV BGR uint8 frame."""
    rgb = np.transpose(tensor.numpy(), (1, 2, 0))
    return cv2.cvtColor((rgb * 255.0).round().clip(0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)


def _goal_layers(goal: dict[str, Any], shape: tuple[int, int]) -> list[tuple[np.ndarray, tuple[int, int, int]]]:
    """The episode's goal channels, rendered once at the `top` frame size.

    The pixels and sigma come from the sidecar, which is what the recorder stored, so the overlay is
    the same gaussian the policy will be conditioned on and not a re-derivation of it. A channel with
    no cell (a ROLL) has no layer at all rather than a layer of zeros.
    """
    height, width = shape
    frame = goal.get("frame") or [width, height]
    renderer = GoalRenderer(int(frame[0]), int(frame[1]), sigma_px=float(goal["sigma_px"]))
    layers = []
    for key, colour in zip(("src_px", "dst_px"), GOAL_BGR, strict=True):
        pixel = goal.get(key)
        if pixel is None:
            continue
        heat = renderer.channel(Cell(key, (0.0, 0.0), (float(pixel[0]), float(pixel[1]))))
        if heat.shape != shape:
            heat = cv2.resize(heat, (width, height), interpolation=cv2.INTER_LINEAR)
        layers.append((heat, colour))
    return layers


def _overlay(frame: np.ndarray, layers: list[tuple[np.ndarray, tuple[int, int, int]]]) -> np.ndarray:
    """Alpha-blend the goal channels onto a `top` frame: green source, magenta target."""
    out = frame.astype(np.float32)
    for heat, colour in layers:
        alpha = (GOAL_ALPHA * heat)[:, :, None]
        out = out * (1.0 - alpha) + np.asarray(colour, dtype=np.float32) * alpha
    return out.round().clip(0, 255).astype(np.uint8)


def _band(width: int, height: int, value: int = 250) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


def _join(tiles: list[np.ndarray]) -> np.ndarray:
    """Tiles side by side with a rule between them; the tile pixels themselves are left untouched."""
    rule = _band(SEP, tiles[0].shape[0], RULE)
    return cv2.hconcat([band for tile in tiles for band in (rule, tile)][1:])


def _header(text: str, width: int) -> np.ndarray:
    band = _band(width, HEADER_H, 24)
    cv2.putText(band, text, (8, 20), FONT, 0.45, (240, 240, 240), 1, cv2.LINE_AA)
    return band


def _short(name: str) -> str:
    """`left_shoulder_pitch_joint` -> `shoulder_pitch`, so nine labels fit on one legend row."""
    return name.removeprefix("left_").removesuffix("_joint").removesuffix("_scalar")


def _legend(names: list[str], width: int) -> np.ndarray:
    band = _band(width, LEGEND_H)
    step = width // max(len(names), 1)
    for i, name in enumerate(names):
        x = i * step + 6
        cv2.rectangle(band, (x, 8), (x + 12, 16), DIM_BGR[i % len(DIM_BGR)], -1)
        cv2.putText(band, _short(name), (x + 16, 17), FONT, 0.35, (40, 40, 40), 1, cv2.LINE_AA)
    return band


def _panel(series: np.ndarray, title: str, width: int, height: int) -> np.ndarray:
    """One `(frames, dims)` block as nine coloured polylines on a shared, labelled y axis.

    All dimensions share one y range on purpose: the panel then shows the relative size of what the
    arm, the waist and the pinch were doing, and the two axis labels say what that range is.
    """
    panel = _band(width, height)
    x0, y0, x1, y1 = 56, 24, width - 12, height - 22
    cv2.rectangle(panel, (x0, y0), (x1, y1), (180, 180, 180), 1)
    lo, hi = float(series.min()), float(series.max())
    span = (hi - lo) or 1.0
    count = len(series)
    xs = np.linspace(x0, x1, count).round().astype(np.int32) if count > 1 else np.full(1, x0, np.int32)
    for dim in range(series.shape[1]):
        ys = (y1 - (series[:, dim] - lo) / span * (y1 - y0)).round().astype(np.int32)
        cv2.polylines(panel, [np.stack([xs, ys], axis=1)], False, DIM_BGR[dim % len(DIM_BGR)], 1, cv2.LINE_AA)
    cv2.putText(panel, title, (x0, 16), FONT, SCALE, (20, 20, 20), 1, cv2.LINE_AA)
    for text, org in ((f"{hi:+.3f}", (4, y0 + 6)), (f"{lo:+.3f}", (4, y1)), ("frame 0", (x0, height - 6)),
                      (f"frame {max(count - 1, 0)}", (x1 - 66, height - 6))):
        cv2.putText(panel, text, org, FONT, 0.35, (60, 60, 60), 1, cv2.LINE_AA)
    return panel


def _camera_rows(dataset: LeRobotDataset, picks: list[int], goal: dict[str, Any]) -> list[np.ndarray]:
    """The three camera rows, the `top` one carrying the goal overlay, all COLUMNS*TILE_W wide."""
    frames: dict[str, list[np.ndarray]] = {name: [] for name in CAMERAS}
    for index in picks:
        item = dataset[index]
        for name in CAMERAS:
            frames[name].append(_to_bgr(item[f"observation.images.{name}"]))
    layers = _goal_layers(goal, frames["top"][0].shape[:2])
    return [_join([_tile(_overlay(f, layers) if name == "top" else f) for f in frames[name]]) for name in CAMERAS]


def episode_strip(dataset: LeRobotDataset, meta: dict[str, Any]) -> np.ndarray:
    """Render one episode's strip: header, three camera rows, legend, action and state panels."""
    start, stop = _span(dataset, int(meta["episode_index"]))
    count = stop - start
    if count < 1:
        raise ValueError(f"episode {meta['episode_index']} has no frames")
    picks = [start + int(i) for i in np.linspace(0, count - 1, COLUMNS).round().astype(int)]
    rows = _camera_rows(dataset, picks, meta.get("goal") or {})
    width = rows[0].shape[1]
    names = [_short(n) for n in dataset.features["action"]["names"]]
    columns = dataset.hf_dataset.select_columns(["action", "observation.state"])[start:stop]
    action = np.asarray(columns["action"], dtype=np.float64)
    state = np.asarray(columns["observation.state"], dtype=np.float64)
    head = (
        f"ep {meta['episode_index']}  {meta['task_id']}  {meta.get('src_cell') or '-'} -> "
        f"{meta.get('dst_cell') or '-'}  success={meta.get('success')}  perturbed={meta.get('perturbed')}  "
        f"{count} frames  skew p99 {float(meta.get('skew_p99_ms', 0.0)):.3f} ms"
    )
    half = width // 2
    plots = cv2.hconcat([_panel(action, "action (9)", half, PLOT_H), _panel(state, "state (9)", width - half, PLOT_H)])
    bands = [band for row in rows for band in (row, _band(width, SEP, RULE))]
    return cv2.vconcat([_header(head, width), *bands, _legend(names, width), plots])


def render_session(
    root: Path | str, *, episodes: list[int] | None = None, out_dir: Path | str | None = None
) -> list[Path]:
    """Write one PNG per selected episode and return the paths, newest write last."""
    root = Path(root)
    dataset, metas, card = load_session(root)
    if card:
        print(card)
    out = Path(out_dir) if out_dir is not None else root / "strips"
    out.mkdir(parents=True, exist_ok=True)
    wanted = [m for m in metas if episodes is None or int(m["episode_index"]) in episodes]
    if episodes is not None:
        missing = sorted(set(episodes) - {int(m["episode_index"]) for m in wanted})
        if missing:
            raise KeyError(f"{root/SIDECAR} has no episode(s) {missing}")
    written = []
    for meta in wanted:
        path = out / f"episode_{int(meta['episode_index']):06d}.png"
        if not cv2.imwrite(str(path), episode_strip(dataset, meta)):
            raise OSError(f"could not write {path}")
        written.append(path)
        print(f"wrote {path}  ({path.stat().st_size / 1024:.0f} kB)")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session_root", type=Path, help="a session directory under data/raw/")
    parser.add_argument("--episodes", help="comma-separated episode indices; default every episode")
    parser.add_argument("--out", type=Path, default=None, help="output directory; default SESSION_ROOT/strips/")
    args = parser.parse_args(argv)
    episodes = [int(v) for v in args.episodes.split(",") if v.strip()] if args.episodes else None
    written = render_session(args.session_root, episodes=episodes, out_dir=args.out)
    print(f"{len(written)} strip(s)")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
