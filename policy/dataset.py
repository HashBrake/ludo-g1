"""Training samples from recorded sessions: the observation of CLAUDE.md 5.3 plus an action chunk.

One :class:`LudoDataset` wraps one or more sessions written by ``teleop/recorder.py`` (a LeRobot
v3.0 dataset plus the ``episodes_meta.jsonl`` sidecar, D-011/D-015) and yields exactly what the
policies of 5.7 are trained on::

    from policy.dataset import LudoDataset, split_cell_pairs

    train_pairs, held_out = split_cell_pairs(sessions, 0.2, seed=0)   # eval/protocol.py holds these out
    data = LudoDataset(sessions, augment=True, seed=0, split=train_pairs)
    sample = data[0]

=============== ========================= ====================================================
key             shape / dtype             meaning
=============== ========================= ====================================================
``top``         ``(5, h, w)`` float32     Brio RGB in [0, 1], then the two goal heatmap channels
``oblique``     ``(3, h, w)`` float32     Orbbec Ego RGB in [0, 1]
``palm``        ``(3, h, w)`` float32     DexH15 palm RGB in [0, 1]
``state``       ``(9,)`` float32          7 arm joints, waist yaw, pinch (``robot.action_order``)
``task_id``     ``(3,)`` float32          one-hot over ``training.observation.task_ids``
``action``      ``(chunk, 9)`` float32    absolute joint targets at ``rates.dataset_hz``
``action_mask`` ``(chunk,)`` float32      1 for a recorded action, 0 for tail padding
=============== ========================= ====================================================

**The goal channels are rendered, not stored.** The recorder keeps 2.4 MB of gaussian per frame out
of the dataset and stores the two cell pixels once per episode instead (``docs/teleop.md``); this
module re-renders them with :class:`runtime.goal.GoalRenderer` from that sidecar, once per episode,
cached. The render is deterministic, so the same session always produces the same channels.

**Every frame is a sample, and the tail is padded.** A sample at frame ``f`` carries actions
``f .. f + chunk - 1``; past the end of its episode the chunk repeats the episode's last action and
``action_mask`` is 0 there (lerobot's own ``delta_timestamps`` clamping does both). Dropping the last
``chunk - 1`` frames instead would throw away precisely the end of every primitive -- the release,
the retreat -- which is the part the policy has the least of and needs the most.

**Augmentation (5.7) never touches the `top` geometry.** The goal heatmaps live in the ``top`` frame,
so a crop or a flip there would leave the policy conditioned on a cell that is no longer under the
gaussian. Colour jitter (all three cameras), crop-and-resize (``oblique`` and ``palm`` only) and a
gaussian blur of the goal channels only are what ``config/training.yaml`` ``augmentation`` allows;
``geometric_on_top: false`` is enforced here, not assumed.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torchvision.transforms.functional as TF
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from torch.utils.data import Dataset

from engine.interface import Cell
from runtime import config
from runtime.goal import GoalRenderer
from teleop.recorder import SIDECAR

__all__ = ["CAMERAS", "CellPair", "LudoDataset", "episode_metadata", "split_cell_pairs"]

#: The three camera streams of CLAUDE.md 5.3, in the order the sample dict lists them.
CAMERAS: tuple[str, ...] = ("top", "oblique", "palm")
#: One episode's ``(src_cell, dst_cell)``. ``None`` where the primitive addresses no such cell (a
#: ROLL has neither; a RECOVER may have only one), so a pair is not always a pair of cells.
CellPair = tuple[str | None, str | None]
#: Bound on the per-item augmentation seed, so ``seed + index`` stays a valid torch seed.
_SEED_MOD = 2**63


@dataclass(frozen=True)
class _Episode:
    """Where one kept episode's frames are, and what the goal channels over them look like."""

    session: int
    index: int
    start: int
    stop: int
    pair: CellPair
    goal: dict


def episode_metadata(session: Path | str) -> list[dict]:
    """The ``episodes_meta.jsonl`` sidecar of one session, in file order (CLAUDE.md 5.6)."""
    sidecar = Path(session) / SIDECAR
    if not sidecar.exists():
        raise FileNotFoundError(f"{sidecar} is missing: {session} is not a session written by teleop/recorder.py")
    return [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()]


def split_cell_pairs(
    sessions: Sequence[Path | str], held_out_fraction: float, seed: int
) -> tuple[list[CellPair], list[CellPair]]:
    """Split the sessions' cell pairs into ``(train, held_out)``, reproducibly from ``seed``.

    The split is over **cell pairs**, never over frames or episodes (``config/training.yaml``
    ``dataset.split_by: cell_pair``): a policy evaluated on a pair it was trained on has been asked
    nothing. ``eval/protocol.py`` holds out the second list; :class:`LudoDataset` takes either as its
    ``split``.

    Only episodes with **both** cells contribute a pair: a ROLL addresses no cell pair at all and a
    RECOVER may address one cell, and neither can be held out by pair. Both lists come back sorted,
    so the result is a stable, diffable record of what was held out. ``held_out_fraction`` above 0
    always holds out at least one pair when there is one, so an eval set is never silently empty.
    """
    if not 0.0 <= float(held_out_fraction) <= 1.0:
        raise ValueError(f"held_out_fraction must be in [0, 1], got {held_out_fraction!r}")
    pairs = sorted(
        {
            (str(meta["src_cell"]), str(meta["dst_cell"]))
            for session in sessions
            for meta in episode_metadata(session)
            if meta.get("src_cell") and meta.get("dst_cell")
        }
    )
    count = min(len(pairs), max(1, round(len(pairs) * float(held_out_fraction)))) if held_out_fraction else 0
    order = np.random.default_rng(int(seed)).permutation(len(pairs))
    held = {pairs[i] for i in order[:count]}
    return [p for p in pairs if p not in held], sorted(held)


# --------------------------------------------------------------------------------------------------
# augmentation (5.7): the only three things that may happen to a sample, and never to `top` geometry
# --------------------------------------------------------------------------------------------------


def _uniform(low: float, high: float, generator: torch.Generator) -> float:
    return float(low + (high - low) * torch.rand((), generator=generator).item())


def _color_jitter(image: torch.Tensor, generator: torch.Generator, strength: dict[str, float]) -> torch.Tensor:
    """Brightness, contrast, saturation and hue in a random order, photometric only.

    Written out rather than delegated to ``torchvision.transforms.ColorJitter`` because that draws
    from the global RNG: the per-item generator is what makes a sample reproducible from its index.
    """
    out = image
    for which in torch.randperm(4, generator=generator).tolist():
        if which == 0 and strength["brightness"] > 0:
            out = TF.adjust_brightness(out, _uniform(1 - strength["brightness"], 1 + strength["brightness"], generator))
        elif which == 1 and strength["contrast"] > 0:
            out = TF.adjust_contrast(out, _uniform(1 - strength["contrast"], 1 + strength["contrast"], generator))
        elif which == 2 and strength["saturation"] > 0:
            out = TF.adjust_saturation(out, _uniform(1 - strength["saturation"], 1 + strength["saturation"], generator))
        elif which == 3 and strength["hue"] > 0:
            out = TF.adjust_hue(out, _uniform(-strength["hue"], strength["hue"], generator))
    return out


def _crop_resize(image: torch.Tensor, generator: torch.Generator, ratio: float) -> torch.Tensor:
    """A random ``ratio`` sub-window scaled back to the frame size (``oblique`` and ``palm`` only)."""
    _, height, width = image.shape
    box_h, box_w = max(round(height * ratio), 1), max(round(width * ratio), 1)
    top = int(torch.randint(0, height - box_h + 1, (1,), generator=generator).item())
    left = int(torch.randint(0, width - box_w + 1, (1,), generator=generator).item())
    window = image[:, top : top + box_h, left : left + box_w]
    return torch.nn.functional.interpolate(
        window.unsqueeze(0), size=(height, width), mode="bilinear", align_corners=False
    ).squeeze(0)


def _blur_goal(goal: torch.Tensor, generator: torch.Generator, sigma_px: tuple[float, float]) -> torch.Tensor:
    """Blur the two goal channels by a random sigma: the cell pixel is not known to the millimetre."""
    sigma = _uniform(sigma_px[0], sigma_px[1], generator)
    if sigma <= 0:
        return goal
    radius = max(int(math.ceil(3.0 * sigma)), 1)
    return TF.gaussian_blur(goal, [2 * radius + 1, 2 * radius + 1], [sigma, sigma])


# --------------------------------------------------------------------------------------------------
# the dataset
# --------------------------------------------------------------------------------------------------


class LudoDataset(Dataset):
    """Samples of CLAUDE.md 5.3 over one or more recorded sessions (see the module docstring)."""

    def __init__(
        self,
        sessions: Sequence[Path | str],
        *,
        chunk: int | None = None,
        augment: bool = False,
        seed: int | None = None,
        split: Iterable[CellPair] | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        """``sessions`` are session directories under ``data/raw/``.

        ``chunk`` defaults to ``config/training.yaml`` ``diffusion.chunk`` (16); the ACT baseline
        passes its own (32). ``augment`` turns on the three augmentations of 5.7, each drawn from a
        generator seeded with ``seed + index``, so sample *i* is the same sample on every epoch, in
        every worker, and after a restart; ``seed=None`` draws one base seed and records it in
        :attr:`seed`. ``split`` keeps only the episodes whose ``(src_cell, dst_cell)`` is listed --
        pass what :func:`split_cell_pairs` returns, adding ``(None, None)`` to keep the ROLL episodes
        (which have no cell pair, so no split can hold them out).
        """
        paths = [Path(s) for s in sessions]
        if not paths:
            raise ValueError("LudoDataset needs at least one session directory")
        training = config.load("training", root=config_root)
        self.chunk = int(training["diffusion"]["chunk"]) if chunk is None else int(chunk)
        if self.chunk < 1:
            raise ValueError(f"LudoDataset needs chunk >= 1, got {self.chunk}")
        self.fps = int(training["rates"]["dataset_hz"])
        self.task_ids: tuple[str, ...] = tuple(str(t) for t in training["observation"]["task_ids"])
        self.augment = bool(augment)
        self.augmentation = self._augmentation(training["augmentation"])
        self.seed = int(torch.seed() % _SEED_MOD) if seed is None else int(seed)
        self._config_root = config_root
        keep = None if split is None else {(a, b) for a, b in split}

        delta = {"action": [i / self.fps for i in range(self.chunk)]}
        self.datasets: list[LeRobotDataset] = []
        self.episodes: list[_Episode] = []
        self._index: list[tuple[int, int]] = []
        for session_index, root in enumerate(paths):
            metas = episode_metadata(root)
            dataset = LeRobotDataset(f"ludo-g1/{root.name}", root=root, delta_timestamps=delta)
            if int(dataset.fps) != self.fps:
                raise ValueError(
                    f"{root} was recorded at {dataset.fps} Hz but config/training.yaml "
                    f"rates.dataset_hz is {self.fps}: the action chunk would span the wrong time"
                )
            self.datasets.append(dataset)
            spans = _spans(dataset)
            for meta in metas:
                pair: CellPair = (meta.get("src_cell"), meta.get("dst_cell"))
                if keep is not None and pair not in keep:
                    continue
                episode_index = int(meta["episode_index"])
                if episode_index not in spans:
                    raise KeyError(f"episode {episode_index} is in {root / SIDECAR} but not in the dataset")
                start, stop = spans[episode_index]
                self.episodes.append(_Episode(session_index, episode_index, start, stop, pair, meta.get("goal") or {}))
                self._index.extend((len(self.episodes) - 1, frame) for frame in range(start, stop))
        self._goal_cache: dict[int, torch.Tensor] = {}

    def __repr__(self) -> str:
        return (
            f"LudoDataset(sessions={len(self.datasets)}, episodes={len(self.episodes)}, frames={len(self)}, "
            f"chunk={self.chunk}, augment={self.augment}, seed={self.seed})"
        )

    def __len__(self) -> int:
        return len(self._index)

    @staticmethod
    def _augmentation(block: dict) -> dict:
        """Validate and flatten ``config/training.yaml`` ``augmentation`` (5.7)."""
        if bool(block["geometric_on_top"]):
            raise config.ConfigError(
                "config/training.yaml augmentation.geometric_on_top must stay false: the goal heatmaps are "
                "rendered in the `top` frame and any geometric change there desynchronises them (CLAUDE.md 5.7)"
            )
        crop_cameras = tuple(str(c) for c in block["random_crop_cameras"])
        if "top" in crop_cameras:
            raise config.ConfigError(
                f"config/training.yaml augmentation.random_crop_cameras {crop_cameras} includes `top`, which is "
                "a geometric augmentation of the goal frame (CLAUDE.md 5.7)"
            )
        unknown = [c for c in crop_cameras if c not in CAMERAS]
        if unknown:
            raise config.ConfigError(
                f"config/training.yaml augmentation.random_crop_cameras: unknown camera(s) {unknown}"
            )
        ratio = float(block["random_crop_ratio"])
        if not 0.0 < ratio <= 1.0:
            raise config.ConfigError(
                f"config/training.yaml augmentation.random_crop_ratio must be in (0, 1], got {ratio}"
            )
        blur = tuple(float(v) for v in block["goal_heatmap_blur_px"])
        if len(blur) != 2 or blur[0] < 0 or blur[1] < blur[0]:
            raise config.ConfigError(
                f"config/training.yaml augmentation.goal_heatmap_blur_px must be [low, high] with 0 <= low <= high, "
                f"got {list(blur)}"
            )
        jitter = {k: float(block["color_jitter"][k]) for k in ("brightness", "contrast", "saturation", "hue")}
        return {"color_jitter": jitter, "crop_cameras": crop_cameras, "crop_ratio": ratio, "blur_px": blur}

    # -- the goal channels, once per episode ---------------------------------------------------------

    def _goal(self, episode_position: int, shape: tuple[int, int]) -> torch.Tensor:
        """``(2, h, w)`` float32 for one episode: source channel, then target channel (5.3).

        Rendered from the pixels the recorder stored, so it is the gaussian the operator saw and the
        one ``runtime/controller.py`` will build live, not a re-derivation of it.
        """
        cached = self._goal_cache.get(episode_position)
        if cached is not None:
            return cached
        goal = self.episodes[episode_position].goal
        if not goal:
            raise KeyError(f"episode {self.episodes[episode_position].index} has no `goal` in {SIDECAR}")
        height, width = shape
        frame = goal.get("frame") or [width, height]
        renderer = GoalRenderer(
            int(frame[0]), int(frame[1]), sigma_px=float(goal["sigma_px"]), config_root=self._config_root
        )
        channels = []
        for key in ("src_px", "dst_px"):
            pixel = goal.get(key)
            cell = None if pixel is None else Cell(key, (0.0, 0.0), (float(pixel[0]), float(pixel[1])))
            channels.append(torch.from_numpy(renderer.channel(cell)))
        rendered = torch.stack(channels)
        if rendered.shape[-2:] != torch.Size(shape):
            rendered = torch.nn.functional.interpolate(
                rendered.unsqueeze(0), size=shape, mode="bilinear", align_corners=False
            ).squeeze(0)
        self._goal_cache[episode_position] = rendered
        return rendered

    def _one_hot(self, task_index: int) -> torch.Tensor:
        if not 0 <= task_index < len(self.task_ids):
            raise ValueError(
                f"recorded task_id {task_index} is outside config/training.yaml observation.task_ids {self.task_ids}"
            )
        out = torch.zeros(len(self.task_ids), dtype=torch.float32)
        out[task_index] = 1.0
        return out

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        episode_position, frame = self._index[index]
        episode = self.episodes[episode_position]
        item = self.datasets[episode.session][frame]
        images = {name: item[f"observation.images.{name}"].to(torch.float32) for name in CAMERAS}
        goal = self._goal(episode_position, tuple(images["top"].shape[-2:]))
        if self.augment:
            generator = torch.Generator().manual_seed((self.seed + index) % _SEED_MOD)
            for name in CAMERAS:
                images[name] = _color_jitter(images[name], generator, self.augmentation["color_jitter"])
            for name in self.augmentation["crop_cameras"]:
                images[name] = _crop_resize(images[name], generator, self.augmentation["crop_ratio"])
            goal = _blur_goal(goal, generator, self.augmentation["blur_px"])
        return {
            "top": torch.cat([images["top"], goal], dim=0),
            "oblique": images["oblique"],
            "palm": images["palm"],
            "state": item["observation.state"].to(torch.float32),
            "task_id": self._one_hot(int(item["task_id"].reshape(-1)[0])),
            "action": item["action"].to(torch.float32),
            "action_mask": (~item["action_is_pad"]).to(torch.float32),
        }


def _spans(dataset: LeRobotDataset) -> dict[int, tuple[int, int]]:
    """Every episode's half-open range of global frame indices, by episode index."""
    episodes = dataset.meta.episodes
    return {
        int(index): (int(start), int(stop))
        for index, start, stop in zip(
            episodes["episode_index"],
            episodes["dataset_from_index"],
            episodes["dataset_to_index"],
            strict=True,
        )
    }
