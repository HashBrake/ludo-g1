"""Blob detection and the classification rules the top camera judges a frame with (T-042, D-013).

Split unchanged out of :mod:`board.perception`: the ``perception`` block of ``config/board.yaml``
parsed into :class:`Rules` (with :class:`Bowl`, :class:`Pose` and :class:`Placement`), and the blob
area measurement :func:`_area_px` that the classification bands are applied to.

Nothing here reads a camera or commands anything: it takes a config file and a contour (R1, R2).
:mod:`board.perception` imports every name back, so ``board.perception.load_rules`` and the rest
still resolve. Every threshold is still a placeholder under ``perception_status: UNMEASURED``; the
first real still is H-001.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from runtime import config

__all__ = ["Bowl", "Placement", "PerceptionError", "Pose", "Rules", "load_rules"]

# ==============================================================================================
# The top-camera implementation (T-038): detection rules
# ==============================================================================================


class PerceptionError(ValueError):
    """The frame cannot be read, or ``config/board.yaml``'s perception block is unusable."""


class Pose(Enum):
    """How a horse is sitting, from above."""

    STANDING = "standing"
    FALLEN = "fallen"


class Placement(Enum):
    """Whether a horse is on a cell or off it (CLAUDE.md 6.5, "placed between cells")."""

    AT_CELL = "at_cell"
    BETWEEN_CELLS = "between_cells"


@dataclass(frozen=True)
class Bowl:
    """Where the die bowl is, and how far outside it a die is still looked for."""

    centre_mm: tuple[float, float]
    diameter_mm: float
    search_radius_mm: float
    #: True when ``die.bowl_centre_mm`` held real numbers, False while the placeholder is in force.
    measured: bool


@dataclass(frozen=True)
class Rules:
    """The detection rules of ``config/board.yaml``'s ``perception`` block, parsed and checked.

    Every threshold here is a placeholder (``perception_status: UNMEASURED``). They are ratios and
    millimetres, never pixels, so the same numbers hold at any camera distance: an area is compared
    against the piece's expected footprint area at the local pixel scale of the calibration, and a
    distance is compared in the board frame.
    """

    hsv_ranges: dict[str, tuple[tuple[np.ndarray, np.ndarray], ...]]
    die_hsv_range: tuple[np.ndarray, np.ndarray]
    min_area_ratio: float
    standing_area_ratio: tuple[float, float]
    standing_max_aspect: float
    die_area_ratio: tuple[float, float]
    morph_open_px: int
    cell_snap_radius_mm: float
    die_moved_min_mm: float
    footprint_mm: float
    die_size_mm: float
    bowl: Bowl
    #: dotted paths of ``config/board.yaml`` that are still UNMEASURED and feed these rules
    unmeasured: tuple[str, ...] = ()

    @property
    def colors(self) -> tuple[str, ...]:
        return tuple(self.hsv_ranges)


def _hsv_pair(name: str, value: Any) -> tuple[np.ndarray, np.ndarray]:
    """One ``[[h,s,v], [h,s,v]]`` low/high pair, checked and turned into two uint8 arrays."""
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(isinstance(v, (list, tuple)) and len(v) == 3 for v in value)
    ):
        raise PerceptionError(f"config/board.yaml: {name} must be [[h,s,v], [h,s,v]], got {value!r}")
    lo, hi = (np.array([float(c) for c in side], dtype=np.float64) for side in value)
    if np.any(lo < 0) or np.any(hi > [179, 255, 255]) or np.any(lo > hi):
        raise PerceptionError(
            f"config/board.yaml: {name} = {value!r} is not an ordered HSV range inside "
            f"H 0..179, S 0..255, V 0..255"
        )
    return lo.astype(np.uint8), hi.astype(np.uint8)


def _positive(block: dict, key: str, where: str) -> float:
    value = block.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise PerceptionError(f"config/board.yaml: {where}.{key} must be a positive number, got {value!r}")
    return float(value)


def _band(block: dict, key: str, where: str) -> tuple[float, float]:
    value = block.get(key)
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(isinstance(v, (int, float)) for v in value)
        or not 0 <= value[0] < value[1]
    ):
        raise PerceptionError(f"config/board.yaml: {where}.{key} must be an ordered [low, high], got {value!r}")
    return float(value[0]), float(value[1])


def load_rules(root: Path | str | None = None) -> Rules:
    """Read the ``perception`` block of ``config/board.yaml`` (plus the piece sizes it needs).

    The bowl follows one precedence rule: a *measured* ``die.bowl_centre_mm`` /
    ``die.bowl_diameter_mm`` wins, and the ``perception.bowl`` placeholder stands in only while
    those are still the literal ``UNMEASURED``. ``Bowl.measured`` says which happened, so nothing
    downstream has to guess whether the bowl it was given is real.
    """
    board = config.load("board", root)
    block = board.get("perception")
    if not isinstance(block, dict):
        raise PerceptionError("config/board.yaml: no `perception` block; board/perception.py cannot run without it")

    ranges = block.get("hsv_ranges")
    if not isinstance(ranges, dict) or not ranges:
        raise PerceptionError("config/board.yaml: perception.hsv_ranges must be a non-empty mapping of colour -> bands")
    colors = tuple(str(c) for c in board["layout"]["colors"])
    missing = [c for c in colors if c not in ranges]
    if missing:
        raise PerceptionError(
            f"config/board.yaml: perception.hsv_ranges has no band for colour(s) {', '.join(missing)}; "
            f"layout.colors are {', '.join(colors)}"
        )
    hsv: dict[str, tuple[tuple[np.ndarray, np.ndarray], ...]] = {}
    for color in colors:
        bands = ranges[color]
        if not isinstance(bands, (list, tuple)) or not bands:
            raise PerceptionError(f"config/board.yaml: perception.hsv_ranges.{color} must be a non-empty list of bands")
        hsv[color] = tuple(_hsv_pair(f"perception.hsv_ranges.{color}[{i}]", b) for i, b in enumerate(bands))

    die_block = board["die"]
    bowl_block = block.get("bowl")
    if not isinstance(bowl_block, dict):
        raise PerceptionError("config/board.yaml: perception.bowl must be a mapping")
    centre = die_block.get("bowl_centre_mm")
    diameter = die_block.get("bowl_diameter_mm")
    measured = isinstance(centre, (list, tuple)) and len(centre) == 2 and isinstance(diameter, (int, float))
    if not measured:
        centre = bowl_block.get("centre_mm")
        diameter = bowl_block.get("diameter_mm")
        if not isinstance(centre, (list, tuple)) or len(centre) != 2:
            raise PerceptionError(
                "config/board.yaml: neither die.bowl_centre_mm nor perception.bowl.centre_mm is a pair of numbers"
            )
    bowl = Bowl(
        centre_mm=(float(centre[0]), float(centre[1])),
        diameter_mm=_positive({"d": diameter}, "d", "bowl diameter"),
        search_radius_mm=_positive(bowl_block, "search_radius_mm", "perception.bowl"),
        measured=bool(measured),
    )

    morph = block.get("morph_open_px", 0)
    if not isinstance(morph, int) or isinstance(morph, bool) or morph < 0:
        raise PerceptionError(f"config/board.yaml: perception.morph_open_px must be 0 or a positive int, got {morph!r}")
    snap = _positive(block, "cell_snap_radius_mm", "perception")
    pitch = float(board["layout"]["cell_pitch_mm"])
    if snap >= pitch / 2.0:
        raise PerceptionError(
            f"config/board.yaml: perception.cell_snap_radius_mm ({snap}) must stay below half the cell "
            f"pitch ({pitch / 2.0}); a wider radius lets two cells claim the same horse"
        )
    return Rules(
        hsv_ranges=hsv,
        die_hsv_range=_hsv_pair("perception.die_hsv_range", block.get("die_hsv_range")),
        min_area_ratio=_positive(block, "min_area_ratio", "perception"),
        standing_area_ratio=_band(block, "standing_area_ratio", "perception"),
        standing_max_aspect=_positive(block, "standing_max_aspect", "perception"),
        die_area_ratio=_band(block, "die_area_ratio", "perception"),
        morph_open_px=int(morph),
        cell_snap_radius_mm=snap,
        die_moved_min_mm=_positive(block, "die_moved_min_mm", "perception"),
        footprint_mm=_positive(board["horse"], "footprint_mm", "horse"),
        die_size_mm=_positive(die_block, "size_mm", "die"),
        bowl=bowl,
        unmeasured=tuple(
            p for p in config.unmeasured("board", root) if p.split(".")[0] in ("perception", "horse", "die")
        ),
    )


def _area_px(contour: np.ndarray) -> float:
    """Pixel area of a blob from its external contour.

    ``cv2.contourArea`` is the area of the polygon through the boundary *pixel centres*, which
    undercounts a blob by half its perimeter plus one -- 121 instead of 144 for a 12 px square, an
    error of 16% that matters when the whole classification is an area band. Adding that back makes
    the number the pixel count it should have been, and it deliberately counts the hole the dark
    arrow punches in the top face: what is being measured is the horse's footprint, not its paint.
    """
    return float(cv2.contourArea(contour) + 0.5 * cv2.arcLength(contour, True) + 1.0)
