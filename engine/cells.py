"""Building :class:`~engine.interface.Cell` objects out of ``config/board.yaml``.

One place turns the cell table in the config into addressable objects, so that the stub engine, the
goal heatmaps and ``eval/protocol.py`` all address the same cells by the same ids. The table itself
is the engine team's to own; everything here reads it and nothing here invents a cell.

``top_px`` (the cell's centre in Brio pixels) is not in ``config/board.yaml`` and cannot be: it moves
whenever the camera moves. ``board/calibration.py`` (T-008) produces it from the AprilTag homography,
and :func:`load_cells` takes it as an optional mapping. Without one every cell carries ``top_px =
None``, which is the honest answer on an uncalibrated rig and is what the whole Phase 0 stack runs
on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from engine.interface import Cell
from runtime import config

__all__ = ["Layout", "base_id", "home_id", "load_cells", "load_layout", "track_id"]


@dataclass(frozen=True)
class Layout:
    """The topology of the cell table: what the ids mean and how a lap is walked.

    Mirrors the ``layout`` block of ``config/board.yaml``. Like the cell table it is a placeholder
    until the engine team delivers the real topology (``layout_status: UNMEASURED``).
    """

    colors: tuple[str, ...]
    starts: dict[str, str]        # colour -> id of its first track cell
    home_entries: dict[str, str]  # colour -> id of the last track cell before its home lane
    track_length: int             # cells in the single shared cycle
    home_length: int              # cells in one colour's home lane
    base_size: int                # horses (and base cells) per colour

    def start_index(self, color: str) -> int:
        """Index into the track cycle of ``color``'s start cell."""
        return int(self.starts[color].split("-")[1])


def track_id(index: int) -> str:
    """Id of track cell ``index``; the caller is responsible for the modulo."""
    return f"track-{index}"


def home_id(color: str, index: int) -> str:
    """Id of ``color``'s home cell ``index``, 0 at the lane mouth."""
    return f"{color}-home-{index}"


def base_id(color: str, index: int) -> str:
    """Id of ``color``'s base cell ``index``."""
    return f"{color}-base-{index}"


def load_layout(root: Path | str | None = None) -> Layout:
    """Read the ``layout`` block of ``config/board.yaml``."""
    layout = config.load("board", root)["layout"]
    return Layout(
        colors=tuple(layout["colors"]),
        starts=dict(layout["starts"]),
        home_entries=dict(layout["home_entries"]),
        track_length=int(layout["track_length"]),
        home_length=int(layout["home_length"]),
        base_size=int(layout["base_size"]),
    )


def load_cells(
    root: Path | str | None = None,
    top_px: Mapping[str, tuple] | None = None,
) -> dict[str, Cell]:
    """Every cell in ``config/board.yaml``, keyed by id, in file order.

    ``top_px`` maps cell id to its centre in Brio pixels, as produced by ``board/calibration.py``;
    ids it does not mention get ``top_px = None``, and ids it mentions that are not in the board
    config are an error (a calibration for a different board must not pass silently).

    Raises :class:`~runtime.config.ConfigError` for a duplicate id, a missing or malformed
    ``board_xy_mm``, or an unknown id in ``top_px``.
    """
    entries = config.load("board", root)["layout"]["cells"]
    if not isinstance(entries, list) or not entries:
        raise config.ConfigError("config/board.yaml: layout.cells must be a non-empty list")

    cells: dict[str, Cell] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or "id" not in entry or "board_xy_mm" not in entry:
            raise config.ConfigError(f"config/board.yaml: layout.cells[{i}] needs an 'id' and a 'board_xy_mm'")
        cell_id = str(entry["id"])
        if cell_id in cells:
            raise config.ConfigError(f"config/board.yaml: layout.cells[{i}]: duplicate cell id {cell_id!r}")
        xy = entry["board_xy_mm"]
        if not isinstance(xy, (list, tuple)) or len(xy) != 2 or not all(isinstance(v, (int, float)) for v in xy):
            raise config.ConfigError(
                f"config/board.yaml: layout.cells[{i}] ({cell_id!r}): board_xy_mm must be two numbers, got {xy!r}"
            )
        px = None if top_px is None else top_px.get(cell_id)
        cells[cell_id] = Cell(
            id=cell_id,
            board_xy_mm=(float(xy[0]), float(xy[1])),
            top_px=None if px is None else tuple(px),
        )

    if top_px is not None:
        unknown = sorted(set(top_px) - set(cells))
        if unknown:
            raise config.ConfigError(
                "top_px names cell(s) that config/board.yaml does not define: " + ", ".join(repr(u) for u in unknown)
            )
    return cells
