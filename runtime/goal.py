"""Goal conditioning: the two heatmap channels and the task one-hot of CLAUDE.md 5.3.

The engine says *which* cells a primitive addresses; the policy is told *where* they are by two
gaussian channels rendered in the same frame as the `top` image, plus a one-hot over the three
primitives. That is the whole of the goal conditioning, and CLAUDE.md R2 explicitly allows it: it is
conditioning, not a trajectory.

```python
from runtime.goal import GoalRenderer
renderer = GoalRenderer()                 # 640x480, sigma from config/training.yaml
goal = renderer.render(command)           # (2, 480, 640) float32: source, then target
task = renderer.task_one_hot(command.primitive)
```

**Where a cell is in pixels.** :attr:`engine.interface.Cell.top_px` is the truth and comes from the
AprilTag homography in ``board/calibration.py``. It is ``None`` until a calibration has been loaded
(``engine.cells.load_cells(top_px=...)``), which is the honest state of an uncalibrated rig. Rather
than refuse to render, this module then falls back to :meth:`GoalRenderer.placeholder_px`: a fixed
affine map from ``board_xy_mm`` onto the frame, assuming the board fills it squarely. **That is a
placeholder, not a calibration** -- it is right only by accident, it is never used to train or to
evaluate anything that matters, and a real calibration replaces it the moment one exists. The
renderer counts its uses in :attr:`GoalRenderer.placeholder_uses` and logs the first one.

**A channel with no cell** (a ROLL, whose ``src``/``dst`` are ``None`` while ``config/board.yaml``
describes no bowl cell) renders as all zeros: the absence of a goal, not a goal at the origin.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from engine.interface import Cell, Command, Primitive
from runtime import config
from runtime.log import get_logger

__all__ = ["GoalRenderer"]

_log = get_logger("runtime.goal")


class GoalRenderer:
    """Renders the goal channels for one frame size. Build one and reuse it: the grids are cached."""

    def __init__(
        self,
        width: int | None = None,
        height: int | None = None,
        *,
        sigma_px: float | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        training = config.load("training", root=config_root)["observation"]
        default_w, default_h = (int(v) for v in training["images"]["top"])
        self.width = default_w if width is None else int(width)
        self.height = default_h if height is None else int(height)
        if self.width < 1 or self.height < 1:
            raise ValueError(f"GoalRenderer needs a positive frame size, got {self.width}x{self.height}")
        self.sigma_px = float(training["goal_sigma_px"] if sigma_px is None else sigma_px)
        if not self.sigma_px > 0:
            raise config.ConfigError(
                f"config/training.yaml: observation.goal_sigma_px must be positive, got {self.sigma_px}"
            )
        self.channels = int(training["goal_channels"])
        self.task_ids: tuple[str, ...] = tuple(str(t) for t in training["task_ids"])
        board = config.load("board", root=config_root)
        self.board_size_mm = (float(board["size_mm"][0]), float(board["size_mm"][1]))
        self.placeholder_uses = 0
        self._xs = np.arange(self.width, dtype=np.float64)
        self._ys = np.arange(self.height, dtype=np.float64)
        self._zeros = np.zeros((self.height, self.width), dtype=np.float32)

    def __repr__(self) -> str:
        return (
            f"GoalRenderer({self.width}x{self.height}, sigma_px={self.sigma_px:g}, "
            f"placeholder_uses={self.placeholder_uses})"
        )

    # -- where a cell is ---------------------------------------------------------------------------

    def placeholder_px(self, cell: Cell) -> tuple[float, float]:
        """The stand-in pixel of a cell with no calibration: board millimetres mapped onto the frame.

        Board frame (``config/board.yaml`` ``frame: board_centre``): origin at the board centre, +x
        to the board's right, +y away from the camera. Image: +u right, +v **down**. So x maps to u
        linearly and y maps to v inverted, with the board assumed to fill the frame exactly. A cell
        outside the board maps outside the frame and its gaussian is simply (almost) all zeros.
        """
        x_mm, y_mm = (float(v) for v in cell.board_xy_mm)
        w_mm, h_mm = self.board_size_mm
        u = (x_mm / w_mm + 0.5) * (self.width - 1)
        v = (0.5 - y_mm / h_mm) * (self.height - 1)
        return u, v

    def pixel(self, cell: Cell | None) -> tuple[float, float] | None:
        """``cell.top_px`` when it is calibrated, the placeholder when it is not, ``None`` for no cell."""
        if cell is None:
            return None
        if cell.top_px is not None:
            return float(cell.top_px[0]), float(cell.top_px[1])
        self.placeholder_uses += 1
        if self.placeholder_uses == 1:
            _log.warning(
                "goal_placeholder_px",
                detail="no board calibration loaded; goal heatmaps use the placeholder board_xy_mm -> pixel map",
                cell=cell.id,
            )
        return self.placeholder_px(cell)

    # -- the channels ------------------------------------------------------------------------------

    def channel(self, cell: Cell | None) -> np.ndarray:
        """One ``(h, w)`` float32 channel: a unit-peak gaussian at the cell, or zeros for no cell."""
        centre = self.pixel(cell)
        if centre is None:
            return self._zeros.copy()
        u, v = centre
        two_sigma_sq = 2.0 * self.sigma_px * self.sigma_px
        gx = np.exp(-((self._xs - u) ** 2) / two_sigma_sq)
        gy = np.exp(-((self._ys - v) ** 2) / two_sigma_sq)
        return np.outer(gy, gx).astype(np.float32)

    def render(self, command: Command) -> np.ndarray:
        """``(2, h, w)`` float32: the source channel then the target channel (5.3)."""
        return np.stack([self.channel(command.src), self.channel(command.dst)]).astype(np.float32)

    def task_one_hot(self, primitive: Primitive) -> np.ndarray:
        """``(3,)`` float32 one-hot over ``config/training.yaml`` ``observation.task_ids``."""
        if primitive.value not in self.task_ids:
            raise config.ConfigError(
                f"config/training.yaml: observation.task_ids {self.task_ids} does not list {primitive.value!r}"
            )
        out = np.zeros(len(self.task_ids), dtype=np.float32)
        out[self.task_ids.index(primitive.value)] = 1.0
        return out
