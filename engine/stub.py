"""A scripted / random stand-in for the real game engine (CLAUDE.md 5.5).

The real engine decides moves; this exists so that everything downstream of it -- the recorder, the
goal heatmaps, ``runtime/controller.py``, ``eval/`` -- has a deterministic, board-consistent stream of
commands long before that engine arrives. It is a move generator, not a rules engine, and it will be
deleted rather than extended when the engine team delivers.

``StubEngine(seed)`` plays random games over the cell table in ``config/board.yaml``;
``StubEngine(seed, script=load_script("eval_20_moves"))`` hands out exactly the commands in a script.
One ``random.Random(seed)`` drives everything, so a seed pins the whole stream. The robot plays one
colour and only *its* turns produce commands.

``docs/engine.md`` has the rules, the retry state machine, and what the stub deliberately does not do.
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engine.cells import Layout, base_id, home_id, load_cells, load_layout, track_id
from engine.interface import Cell, Command, EngineClient, Outcome, Primitive

__all__ = ["ENTER_ROLLS", "SCRIPTS_DIR", "StubEngine", "load_script"]

#: Rolls that let a horse out of base. Both, per the Vietnamese co ca ngua variant (docs/engine.md).
ENTER_ROLLS: tuple[int, ...] = (1, 6)

SCRIPTS_DIR: Path = Path(__file__).resolve().parent / "scripts"


@dataclass
class _Step:
    """One command the engine has queued, plus the state change it makes once it succeeds."""

    command: Command
    moves: tuple[tuple[str, int | None], ...] = ()   # horse -> new progress (None = back in base)
    places: tuple[tuple[str, str], ...] = ()         # horse -> cell id, used in script mode only
    die: int | None = None
    attempts: int = 0                                # failed executions of this step so far
    modes: list[str] = field(default_factory=list)   # failure_mode strings seen for this step


def load_script(name_or_path: str | Path, cells: dict[str, Cell] | None = None) -> list[Command]:
    """Load a command script from ``engine/scripts/<name>.yaml`` (or an explicit path).

    The file is a mapping with a ``commands`` list; each entry has ``primitive`` (a
    :class:`~engine.interface.Primitive` value) and optional ``src``, ``dst`` and ``horse_id``.
    ``src``/``dst`` are cell **ids**, resolved against ``config/board.yaml``, so a script can never
    address a cell that does not exist.
    """
    path = Path(name_or_path)
    if not path.suffix:
        path = SCRIPTS_DIR / f"{path.name}.yaml"
    cells = load_cells() if cells is None else cells
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("commands"), list):
        raise ValueError(f"{path}: expected a mapping with a 'commands' list")

    commands: list[Command] = []
    for i, entry in enumerate(doc["commands"]):
        if not isinstance(entry, dict) or "primitive" not in entry:
            raise ValueError(f"{path}: commands[{i}] needs a 'primitive'")
        try:
            primitive = Primitive(entry["primitive"])
        except ValueError as exc:
            raise ValueError(f"{path}: commands[{i}]: {exc}") from exc
        ends: list[Cell | None] = []
        for key in ("src", "dst"):
            cell_id = entry.get(key)
            if cell_id is not None and cell_id not in cells:
                raise ValueError(f"{path}: commands[{i}]: {key} {cell_id!r} is not a cell in config/board.yaml")
            ends.append(None if cell_id is None else cells[cell_id])
        horse = entry.get("horse_id")
        commands.append(Command(primitive, ends[0], ends[1], None if horse is None else str(horse)))
    return commands


class StubEngine(EngineClient):
    """A deterministic :class:`~engine.interface.EngineClient` for data collection and eval.

    ``seed`` pins every random choice. ``script``, when given, replaces the random games with exactly
    that list of commands, after which :meth:`next_command` returns ``None``.

    ``bowl_cell`` is the cell a ROLL addresses; it defaults to ``None`` because ``config/board.yaml``
    describes no bowl cell and ``die.bowl_centre_mm`` is UNMEASURED. The caller must :meth:`report`
    each command before asking for the next one (docs/engine.md).
    """

    def __init__(
        self,
        seed: int,
        script: Sequence[Command] | None = None,
        *,
        cells: dict[str, Cell] | None = None,
        layout: Layout | None = None,
        robot_color: str = "R",
        max_reissues: int = 2,
        bowl_cell: Cell | None = None,
    ) -> None:
        self.seed = seed
        self._rng = random.Random(seed)
        self._cells = load_cells() if cells is None else cells
        self._layout = load_layout() if layout is None else layout
        if robot_color not in self._layout.colors:
            raise ValueError(f"robot_color {robot_color!r} is not one of {self._layout.colors}")
        self.robot_color = robot_color
        self._max_reissues = max_reissues
        self._bowl = bowl_cell

        self._script = None if script is None else list(script)
        self._script_i = 0
        self._max_progress = self._layout.track_length + self._layout.home_length - 1
        self._horses = [f"{c}{i}" for c in self._layout.colors for i in range(self._layout.base_size)]
        self._progress: dict[str, int | None] = dict.fromkeys(self._horses)
        self._placed: dict[str, str] = {}     # script-mode overrides of a horse's cell
        self._queue: deque[_Step] = deque()
        self._pending: _Step | None = None
        self._turn_i = 0                      # index into layout.colors
        self.game = 0                         # random mode deals a fresh board when a colour is home
        self.turn = 0                         # robot turns played, cumulative over games
        self.die: int | None = None
        self.failures: list[dict[str, Any]] = []

    # -- EngineClient ------------------------------------------------------------------------------
    def next_command(self) -> Command | None:
        if self._pending is not None:
            raise RuntimeError(f"report() the outstanding {self._pending.command.primitive.value} first")
        if not self._queue:
            self._advance()
        if not self._queue:
            return None
        self._pending = self._queue[0]
        return self._pending.command

    def report(self, outcome: Outcome) -> None:
        step, self._pending = self._pending, None
        if step is None:
            raise RuntimeError("report() with no command outstanding")
        if outcome.success:
            self._apply(step)
            self._queue.popleft()
            return
        if outcome.failure_mode:
            step.modes.append(outcome.failure_mode)
        if step.command.primitive is Primitive.RECOVER:
            # A recovery that itself failed is never recovered in its own right, or the budget would
            # not terminate. It is charged to the command it was protecting -- the next step in the
            # queue -- which offers the cell a fresh recovery or gives the turn up.
            self._queue.popleft()
            if self._queue:
                self._queue[0].modes.extend(step.modes)
                self._charge(self._queue[0])
            return
        self._charge(step)

    def board_state(self) -> dict[str, Any]:
        """Where every horse is, plus the die, the counters and the turns given up so far."""
        horses = {h: self._placed.get(h, self._cell_of(h)) for h in self._horses}
        horses.update({h: c for h, c in self._placed.items() if h not in horses})
        return {"robot_color": self.robot_color, "game": self.game, "turn": self.turn,
                "winner": self.winner(), "die": self.die, "horses": horses,
                "progress": dict(self._progress), "failures": [dict(f) for f in self.failures]}

    # -- retries -----------------------------------------------------------------------------------
    def _charge(self, step: _Step) -> None:
        """Count one failed execution of ``step``: recover and re-issue, or give the turn up."""
        step.attempts += 1
        cmd = step.command
        if step.attempts > self._max_reissues:
            ends = {k: None if c is None else c.id for k, c in (("src", cmd.src), ("dst", cmd.dst))}
            self.failures.append({"turn": self.turn, "primitive": cmd.primitive.value, **ends,
                                  "horse_id": cmd.horse_id, "attempts": step.attempts,
                                  "failure_modes": list(step.modes)})
            # The rest of the turn depended on this step (a capture MOVE is followed by ours), so the
            # whole turn is abandoned and the game moves on.
            self._queue.clear()
            return
        where = cmd.dst if cmd.dst is not None else cmd.src
        self._queue.appendleft(_Step(Command(Primitive.RECOVER, where, where, cmd.horse_id)))

    def _apply(self, step: _Step) -> None:
        for horse, progress in step.moves:
            self._progress[horse] = progress
        for horse, cell_id in step.places:
            self._placed[horse] = cell_id
        if step.die is not None:
            self.die = step.die

    # -- board -------------------------------------------------------------------------------------
    def _cell_id(self, color: str, progress: int) -> str:
        if progress < self._layout.track_length:
            return track_id((self._layout.start_index(color) + progress) % self._layout.track_length)
        return home_id(color, progress - self._layout.track_length)

    def _cell_of(self, horse: str) -> str:
        color, index = horse[0], int(horse[1:])
        progress = self._progress[horse]
        return base_id(color, index) if progress is None else self._cell_id(color, progress)

    def _occupant(self, cell_id: str) -> str | None:
        for horse in self._horses:
            if self._progress[horse] is not None and self._cell_of(horse) == cell_id:
                return horse
        return None

    def _legal_moves(self, color: str, die: int) -> list[tuple[str, int, str | None]]:
        """``(horse, new_progress, captured_horse_or_None)`` for every legal move of ``color``."""
        moves = []
        for i in range(self._layout.base_size):
            horse = f"{color}{i}"
            progress = self._progress[horse]
            if progress is None and die not in ENTER_ROLLS:
                continue
            new_progress = 0 if progress is None else progress + die
            if new_progress > self._max_progress:
                continue
            occupant = self._occupant(self._cell_id(color, new_progress))
            if occupant is not None and occupant[0] == color:
                continue
            moves.append((horse, new_progress, occupant))
        return moves

    # -- turn generation ---------------------------------------------------------------------------
    def _advance(self) -> None:
        """Refill the queue: the next scripted command, or the robot's next turn."""
        if self._script is not None:
            if self._script_i >= len(self._script):
                return
            cmd = self._script[self._script_i]
            self._script_i += 1
            places = () if cmd.horse_id is None or cmd.dst is None else ((cmd.horse_id, cmd.dst.id),)
            self._queue.append(_Step(cmd, places=places))
            return

        if self.winner() is not None:
            self._new_game()
        while self._layout.colors[self._turn_i] != self.robot_color:
            self._simulate_turn(self._layout.colors[self._turn_i])
            if self.winner() is not None:
                self._new_game()
                break
            self._turn_i = (self._turn_i + 1) % len(self._layout.colors)
        self._turn_i = (self._turn_i + 1) % len(self._layout.colors)
        self.turn += 1
        self._queue.extend(self._robot_turn())

    def winner(self) -> str | None:
        """The colour that has every horse in its home lane, or ``None`` while the game is on."""
        for color in self._layout.colors:
            progress = [self._progress[f"{color}{i}"] for i in range(self._layout.base_size)]
            if all(p is not None and p >= self._layout.track_length for p in progress):
                return color
        return None

    def _new_game(self) -> None:
        """Deal a fresh board once a colour is home and keep playing (docs/engine.md).

        The RNG is *not* reset, so the stream stays pinned to the seed across game boundaries.
        """
        self.game += 1
        self._progress = dict.fromkeys(self._horses)
        self._turn_i = 0

    def _robot_turn(self) -> list[_Step]:
        color = self.robot_color
        die = self._rng.randint(1, 6)
        steps = [_Step(Command(Primitive.ROLL, self._bowl, self._bowl, None), die=die)]
        moves = self._legal_moves(color, die)
        if not moves:
            return steps
        horse, new_progress, captured = self._rng.choice(moves)
        src = self._cells[self._cell_of(horse)]
        dst = self._cells[self._cell_id(color, new_progress)]
        if captured is not None:
            # The robot clears the captured horse with its own arm before placing its own.
            home = base_id(captured[0], int(captured[1:]))
            steps.append(_Step(Command(Primitive.MOVE, dst, self._cells[home], captured), moves=((captured, None),)))
        steps.append(_Step(Command(Primitive.MOVE, src, dst, horse), moves=((horse, new_progress),)))
        return steps

    def _simulate_turn(self, color: str) -> None:
        """Play one non-robot colour's turn internally. No commands: the robot never touches these."""
        die = self._rng.randint(1, 6)
        moves = self._legal_moves(color, die)
        if not moves:
            return
        horse, new_progress, captured = self._rng.choice(moves)
        if captured is not None:
            self._progress[captured] = None
        self._progress[horse] = new_progress
