"""The game-engine contract (CLAUDE.md 5.5).

This module is the boundary between the game engine, which decides *what* move to make, and this
project, which executes it with a learned policy. It holds nothing else: no board knowledge (that is
``engine/cells.py``), no game rules (``engine/stub.py``), no robot (``runtime/controller.py``).

``engine/stub.py`` implements :class:`EngineClient` today and the real engine replaces it later; this
file is the part that does not change, so the stub's internals never reach into it.

The shapes below are CLAUDE.md 5.5 field for field. The only additions are type annotations,
docstrings, ``frozen=True`` on the three dataclasses (a command handed to the controller must not be
edited behind the engine's back), and the ABC that CLAUDE.md 5.1 asks for ("dataclasses and abstract
EngineClient"). 5.5's ``Optional[X]`` is spelled ``X | None`` here: the identical type, and the only
spelling ruff accepts under this project's ``UP`` lint rules (section 7).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = ["Cell", "Command", "EngineClient", "Outcome", "Primitive"]


class Primitive(Enum):
    """The three things the robot knows how to do. One policy, one ``task_id`` each (5.3)."""

    MOVE = "move"        # pick horse at src, place at dst (covers enter-from-base and capture)
    ROLL = "roll"        # pick die from bowl, release above bowl
    RECOVER = "recover"  # re-stand or re-seat the horse at cell


@dataclass(frozen=True)
class Cell:
    """One addressable board position.

    ``board_xy_mm`` comes from ``config/board.yaml``; ``top_px`` from ``board/calibration.py`` and is
    ``None`` whenever no calibration has been loaded, because the Brio pixel of a cell is a property
    of where the camera happens to be, not of the board (see ``engine/cells.py``).
    """

    id: str              # engine's cell identifier, e.g. "R-base-2", "track-17", "R-home-3"
    board_xy_mm: tuple   # center in board frame (AprilTag-defined), mm
    top_px: tuple | None  # center in Brio pixels, from board/calibration.py; None if uncalibrated


@dataclass(frozen=True)
class Command:
    """One primitive execution requested of the robot.

    ``src``/``dst`` are ``None`` for a primitive that does not address a board cell: a ROLL works over
    the bowl, which ``config/board.yaml`` does not (yet) describe as a cell.
    """

    primitive: Primitive
    src: Cell | None
    dst: Cell | None
    horse_id: str | None


@dataclass(frozen=True)
class Outcome:
    """What happened when the robot executed a :class:`Command`.

    ``observed_state_delta`` is whatever ``board/perception.py`` saw change; the engine is free to
    ignore it. ``failure_mode`` is one of the labelled strings of CLAUDE.md 6.5 whenever
    ``success`` is False, and is counted per failure mode in the eval results.
    """

    success: bool
    observed_state_delta: dict   # what perception saw change
    failure_mode: str | None  # "horse_fell", "missed_cell", "grasp_failed", "die_out_of_bowl", ...


class EngineClient(ABC):
    """The engine as seen by ``runtime/controller.py``.

    The controller runs one strict cycle and never deviates from it::

        cmd = engine.next_command()      # None means the game is over
        ...                              # execute cmd with the policy
        engine.report(outcome)           # exactly one report per command handed out

    Recovery and retries are the engine's business, not the controller's: a failed report is answered
    with a RECOVER command and then the original command again (5.5).
    """

    @abstractmethod
    def next_command(self) -> Command | None:
        """The next command to execute, or ``None`` when there is nothing left to play."""

    @abstractmethod
    def report(self, outcome: Outcome) -> None:
        """Tell the engine how the command it last handed out turned out."""

    @abstractmethod
    def board_state(self) -> dict[str, Any]:
        """The engine's current view of the board."""
