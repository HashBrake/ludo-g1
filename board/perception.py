"""Did the command actually happen? The state check of CLAUDE.md 5.5 (placeholder implementation).

``runtime/controller.py`` runs ``next_command`` -> execute -> **verify** -> ``report``. This module
is that verify step: it compares the board before the primitive with the board after it and turns the
difference into an :class:`~engine.interface.Outcome` with one of the labelled failure modes of
CLAUDE.md 6.5. Verifying the world instead of trusting the policy is required by R5 and is what makes
the engine's recovery loop possible at all.

That vocabulary is :class:`FailureMode`, and it lives here -- in the module that *produces* the
strings -- rather than in ``eval/``, which imports it. One definition, so that a failure the robot
had and a failure the eval JSON counts can never become two spellings of the same thing.

Two implementations live here. :class:`TopCameraPerception` (T-038) is the real shape of the thing:
it looks at a ``top`` frame, finds the horses and the die, and judges the primitive from what the
*table* shows. Its detection rules are placeholders -- colour bands and blob thresholds guessed
against synthetic images, since no Brio still of the real board exists yet (H-001) -- and the engine
team's own perception is expected to replace this module behind the same :class:`Perception`
Protocol (CLAUDE.md 5.1). What is not a placeholder is the verdict logic: which before/after
difference means which of the labelled failure modes of 6.5.

:class:`MockPerception` is the other one, and it stays: every mock run in the repo uses it. It is
deliberately blind: it reads *the engine's own view* of the board, not the
table. It therefore cannot see a horse that fell over, a horse that missed the magnet, or a die that
bounced out of the bowl -- it only sees whether the engine's state changed at all. On the stub engine
the state changes only when a command is reported successful, so every primitive executed with
:class:`~runtime.policy_api.HoldPolicy` shows no progress at all. That is the correct answer for a
robot that did not move, and it is the expected result of every mock run: the controller's watchdog
halts such a primitive as ``policy_stalled`` once ``runtime.watchdog_stall_s`` has passed with the
board unchanged, and :meth:`MockPerception.verify` answers ``timeout_no_progress`` whenever a
primitive instead runs to some other end with nothing having changed.

Using the camera one::

    from board import calibration, perception
    calib = calibration.load("config/board_calib.yaml")
    eye = perception.TopCameraPerception(calib)
    before = eye.detect(frame).to_dict()      # ... the robot executes the primitive ...
    outcome = eye.verify(command, before, eye.detect(frame).to_dict())

``verify`` and ``progress`` take the dict form of a :class:`BoardView` rather than a frame, because
that is the :class:`Perception` signature the controller already calls and because the watchdog
samples ``progress`` ten to twenty times per primitive: whoever owns the camera decides how often a
frame is actually looked at. Wiring that into ``runtime/controller.py``, which reads the engine's
board today, is a later task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import cv2
import numpy as np

from board.calibration import Calibration
from engine.cells import load_cells
from engine.interface import Command, Outcome, Primitive
from runtime import config

__all__ = [
    "NO_PROGRESS",
    "BoardView",
    "Bowl",
    "DieDetection",
    "FailureMode",
    "HorseDetection",
    "MockPerception",
    "Perception",
    "PerceptionError",
    "Placement",
    "Pose",
    "Rules",
    "TopCameraPerception",
    "load_rules",
    "state_delta",
    "view_delta",
]


class FailureMode(Enum):
    """The labelled failure vocabulary of CLAUDE.md 6.5, defined once for the whole project.

    Every failure the system must recover from has exactly one string here, perception is what
    produces them, and ``eval/protocol.py`` imports this enum so that a counted eval result and a
    reported :class:`~engine.interface.Outcome` can never drift apart into two spellings of the same
    failure. The four strings named in 5.5 keep their spelling exactly.

    ============================  ==================================================================
    member                        the case of 6.5 it labels
    ============================  ==================================================================
    ``HORSE_FELL``                horse falls over at the source or the destination
    ``MISSED_CELL``               horse placed between cells, or missing the magnet
    ``GRASP_FAILED``              the grasp closed on nothing
    ``WRONG_HORSE``               the grasp took the wrong horse (an adjacent cell)
    ``DIE_OUT_OF_BOWL``           the die landed outside the bowl (a human replaces it)
    ``DIE_GRASP_FAILED``          the die grasp failed
    ``POLICY_STALLED``            the watchdog halted the primitive: no progress for 20 s (6.5)
    ``TIMEOUT_NO_PROGRESS``       the primitive ran its course and perception saw nothing change
    ============================  ==================================================================

    The last two are deliberately two modes and not one. ``POLICY_STALLED`` is the watchdog's
    verdict, made *during* the primitive by ``runtime/controller.py`` from :meth:`Perception.progress`
    -- the policy went nowhere and was stopped. ``TIMEOUT_NO_PROGRESS`` is perception's verdict,
    made *after* it, when the board is the same as it was. A stall is one way to get a blank board,
    never the only one, and an eval that could not tell them apart could not tell a policy that
    froze from one that worked the whole 20 s and achieved nothing.
    """

    HORSE_FELL = "horse_fell"
    MISSED_CELL = "missed_cell"
    GRASP_FAILED = "grasp_failed"
    WRONG_HORSE = "wrong_horse"
    DIE_OUT_OF_BOWL = "die_out_of_bowl"
    DIE_GRASP_FAILED = "die_grasp_failed"
    POLICY_STALLED = "policy_stalled"
    TIMEOUT_NO_PROGRESS = "timeout_no_progress"

    @classmethod
    def parse(cls, value: str | None) -> FailureMode | None:
        """The member spelled ``value``, or ``None`` if it is not one of 6.5's strings."""
        try:
            return cls(value)
        except ValueError:
            return None


#: What ``failure_mode`` a mock run reports when the board did not change at all (6.5, "policy stalls").
NO_PROGRESS = FailureMode.TIMEOUT_NO_PROGRESS.value


def state_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """``{key: [before, after]}`` for every horse that moved and for the die, if it changed.

    The two arguments are :meth:`engine.interface.EngineClient.board_state` snapshots taken either
    side of one primitive. Keys are cell-bearing ones only: counters (``turn``, ``game``, the
    failure log) are bookkeeping, not board state, and a change in them is not evidence that the
    robot did anything.
    """
    out: dict[str, Any] = {}
    horses_before = dict(before.get("horses") or {})
    horses_after = dict(after.get("horses") or {})
    for horse in sorted(set(horses_before) | set(horses_after)):
        was, now = horses_before.get(horse), horses_after.get(horse)
        if was != now:
            out[horse] = [was, now]
    if before.get("die") != after.get("die"):
        out["die"] = [before.get("die"), after.get("die")]
    return out


@runtime_checkable
class Perception(Protocol):
    """The contract ``runtime/controller.py`` verifies through. Two methods, no state."""

    def verify(self, command: Command, before: dict[str, Any], after: dict[str, Any]) -> Outcome:
        """Judge one primitive execution from the board before and after it.

        ``before``/``after`` are whatever the caller uses as its view of the board: the engine's
        ``board_state()`` today, the Brio detections of the real implementation later. The returned
        :class:`~engine.interface.Outcome` is reported to the engine unchanged.
        """
        ...

    def progress(self, command: Command, before: dict[str, Any], now: dict[str, Any]) -> float:
        """How far ``command`` has got, in ``[0, 1]``, from the board when it started to the board now.

        The controller's watchdog samples this once per ``runtime.watchdog_interval_s`` while the
        primitive runs and halts it when the value has not *increased* for ``runtime.watchdog_stall_s``
        (CLAUDE.md 6.5, "policy stalls (watchdog)"). Only the direction is read, never the magnitude:
        the watchdog asks "is anything happening", not "how well is it going", so a signal that is
        merely monotone in the right way is enough and no scale has to be calibrated.

        It is called ten to twenty times per primitive and must therefore be **cheap** -- a board
        difference or a coarse image statistic, not a detection pass.
        """
        ...


class MockPerception:
    """The stand-in of the module docstring: judges only the engine's own board state.

    Rules, in order, and that is the whole of it:

    1. nothing changed -> failure, ``timeout_no_progress``;
    2. a MOVE whose ``horse_id`` did not end up on ``dst`` -> failure, ``missed_cell``;
    3. a ROLL that did not change the die -> failure, ``die_out_of_bowl``;
    4. otherwise success, with the delta as ``observed_state_delta``.

    A RECOVER changes no board state by design -- it puts a horse back where the engine already
    thinks it is -- so rule 1 always fails it here. The real perception, which looks at the table,
    is what will tell a righted horse from a fallen one.
    """

    def __repr__(self) -> str:
        return "MockPerception()"

    def progress(self, command: Command, before: dict[str, Any], now: dict[str, Any]) -> float:
        """The share of the board that has changed since the primitive began, in ``[0, 1]``.

        Blind in the same way :meth:`verify` is: it counts entries of :func:`state_delta`, so it is
        **0 unless the engine's own board changed**, and on the stub engine, which changes it only
        when a command is reported successful, it is 0 for the whole of every primitive. That is the
        correct reading for a robot that did not move, and it is why every mock run with
        :class:`~runtime.policy_api.HoldPolicy` ends in the watchdog's ``policy_stalled``.

        The denominator is every horse plus the die, so the value rises as more of the board differs
        from where it started and cannot leave ``[0, 1]``.
        """
        changed = len(state_delta(before, now))
        total = len(before.get("horses") or {}) + 1   # every horse, plus the die: never zero
        return min(1.0, changed / total)

    def verify(self, command: Command, before: dict[str, Any], after: dict[str, Any]) -> Outcome:
        delta = state_delta(before, after)
        if not delta:
            return Outcome(success=False, observed_state_delta={}, failure_mode=NO_PROGRESS)
        if command.primitive is Primitive.MOVE and command.dst is not None and command.horse_id is not None:
            if (after.get("horses") or {}).get(command.horse_id) != command.dst.id:
                return Outcome(success=False, observed_state_delta=delta,
                               failure_mode=FailureMode.MISSED_CELL.value)
        if command.primitive is Primitive.ROLL and "die" not in delta:
            return Outcome(success=False, observed_state_delta=delta,
                           failure_mode=FailureMode.DIE_OUT_OF_BOWL.value)
        return Outcome(success=True, observed_state_delta=delta, failure_mode=None)


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


# ==============================================================================================
# What one frame shows
# ==============================================================================================


@dataclass(frozen=True)
class HorseDetection:
    """One coloured blob that passed the size rules, placed on the board."""

    color: str
    pose: Pose
    placement: Placement
    board_xy_mm: tuple[float, float]
    #: the cell it occupies, or ``None`` when it is between cells
    cell_id: str | None
    #: the closest cell centre either way, and how far away it is, in millimetres
    nearest_cell: str
    nearest_mm: float
    area_ratio: float = 0.0
    aspect: float = 0.0
    centroid_px: tuple[float, float] | None = None

    @property
    def standing(self) -> bool:
        return self.pose is Pose.STANDING and self.placement is Placement.AT_CELL

    def to_dict(self) -> dict[str, Any]:
        return {
            "color": self.color,
            "pose": self.pose.value,
            "placement": self.placement.value,
            "board_xy_mm": [round(self.board_xy_mm[0], 2), round(self.board_xy_mm[1], 2)],
            "cell_id": self.cell_id,
            "nearest_cell": self.nearest_cell,
            "nearest_mm": round(self.nearest_mm, 2),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HorseDetection:
        xy = data.get("board_xy_mm") or (0.0, 0.0)
        return cls(
            color=str(data["color"]),
            pose=Pose(data.get("pose", Pose.STANDING.value)),
            placement=Placement(data.get("placement", Placement.AT_CELL.value)),
            board_xy_mm=(float(xy[0]), float(xy[1])),
            cell_id=data.get("cell_id"),
            nearest_cell=str(data.get("nearest_cell") or data.get("cell_id") or ""),
            nearest_mm=float(data.get("nearest_mm", 0.0)),
        )


@dataclass(frozen=True)
class DieDetection:
    """Where the die is, as far as the top camera can tell."""

    seen: bool
    in_bowl: bool
    board_xy_mm: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        xy = None if self.board_xy_mm is None else [round(self.board_xy_mm[0], 2), round(self.board_xy_mm[1], 2)]
        return {"seen": self.seen, "in_bowl": self.in_bowl, "board_xy_mm": xy}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DieDetection:
        if not isinstance(data, dict):
            return cls(seen=False, in_bowl=False)
        xy = data.get("board_xy_mm")
        return cls(
            seen=bool(data.get("seen", False)),
            in_bowl=bool(data.get("in_bowl", False)),
            board_xy_mm=None if xy is None else (float(xy[0]), float(xy[1])),
        )


@dataclass(frozen=True)
class BoardView:
    """Everything one frame said: the horses, where they are, and the die.

    :meth:`to_dict` is what travels: it is what ``verify``/``progress`` take (the
    :class:`Perception` signature is ``dict``), and it is plain JSON so that it can go straight into
    an :class:`~engine.interface.Outcome`'s ``observed_state_delta`` and on into an eval result.
    """

    horses: tuple[HorseDetection, ...] = ()
    die: DieDetection = field(default_factory=lambda: DieDetection(seen=False, in_bowl=False))

    @property
    def cells(self) -> dict[str, HorseDetection]:
        """Cell id -> the horse on it. Horses between cells are not in here; they are in :attr:`loose`."""
        return {h.cell_id: h for h in self.horses if h.cell_id is not None}

    @property
    def loose(self) -> tuple[HorseDetection, ...]:
        """The horses that are on no cell -- between cells, or off the magnet (6.5)."""
        return tuple(h for h in self.horses if h.cell_id is None)

    def occupancy(self) -> dict[str, str]:
        """Cell id -> colour, the plain board state most callers want."""
        return {cell: horse.color for cell, horse in self.cells.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": "top_camera",
            "cells": {cell: horse.to_dict() for cell, horse in sorted(self.cells.items())},
            "loose": [horse.to_dict() for horse in self.loose],
            "die": self.die.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> BoardView:
        """Rebuild a view from :meth:`to_dict`. Raises :class:`PerceptionError` on anything else."""
        if isinstance(data, BoardView):
            return data
        if not isinstance(data, dict) or data.get("source") != "top_camera":
            raise PerceptionError(
                "TopCameraPerception was handed a board state it did not produce "
                f"({type(data).__name__}); pass TopCameraPerception.detect(frame).to_dict()"
            )
        horses = [HorseDetection.from_dict(h) for h in (data.get("cells") or {}).values()]
        horses += [HorseDetection.from_dict(h) for h in (data.get("loose") or [])]
        return cls(horses=tuple(horses), die=DieDetection.from_dict(data.get("die")))


def view_delta(before: BoardView, after: BoardView) -> dict[str, Any]:
    """``{cell: [before, after]}`` for every cell whose occupant changed, plus loose horses and the die.

    The counterpart of :func:`state_delta` for what a camera sees rather than what the engine
    believes. It is what goes into ``Outcome.observed_state_delta``, so it stays plain JSON.
    """
    out: dict[str, Any] = {}
    cells_before, cells_after = before.cells, after.cells
    changed: dict[str, Any] = {}
    for cell in sorted(set(cells_before) | set(cells_after)):
        was, now = cells_before.get(cell), cells_after.get(cell)
        was_d = None if was is None else was.to_dict()
        now_d = None if now is None else now.to_dict()
        if was_d != now_d:
            changed[cell] = [was_d, now_d]
    if changed:
        out["cells"] = changed
    loose_before = [h.to_dict() for h in before.loose]
    loose_after = [h.to_dict() for h in after.loose]
    if loose_before != loose_after:
        out["loose"] = [loose_before, loose_after]
    if before.die.to_dict() != after.die.to_dict():
        out["die"] = [before.die.to_dict(), after.die.to_dict()]
    return out


# ==============================================================================================
# TopCameraPerception
# ==============================================================================================


def _area_px(contour: np.ndarray) -> float:
    """Pixel area of a blob from its external contour.

    ``cv2.contourArea`` is the area of the polygon through the boundary *pixel centres*, which
    undercounts a blob by half its perimeter plus one -- 121 instead of 144 for a 12 px square, an
    error of 16% that matters when the whole classification is an area band. Adding that back makes
    the number the pixel count it should have been, and it deliberately counts the hole the dark
    arrow punches in the top face: what is being measured is the horse's footprint, not its paint.
    """
    return float(cv2.contourArea(contour) + 0.5 * cv2.arcLength(contour, True) + 1.0)


class TopCameraPerception:
    """Board state from the top camera, and the CLAUDE.md 6.5 verdict on a primitive.

    Detection, per frame:

    1. one HSV mask per player colour (``cv2.inRange``, one band per entry, red needs two because
       it wraps the hue origin), opened to drop speckle;
    2. external contours of each mask, and for each blob its footprint area and its
       minimum-area rectangle;
    3. **pose**: ``standing`` when the area is inside the band around the expected footprint area
       *and* the rectangle is near square; anything else that is bigger than the noise floor is
       ``fallen`` -- a horse on its side shows an elongated footprint-by-height rectangle, one on
       its back shows only the smaller arrow face;
    4. **placement**: the centroid goes through the calibration into the board frame and takes the
       nearest cell centre within ``cell_snap_radius_mm``; further than that and it is
       ``between_cells``, which is 6.5's "placed between cells, or missing the magnet". Two horses
       never share a cell: the nearer one claims it and the other is loose;
    5. **die**: the one white blob of about die size within the bowl's search region, in the bowl
       or out of it.

    What it cannot do, stated rather than papered over: two horses of the same colour are
    interchangeable, so a MOVE that takes the wrong horse *of the same colour from another cell* is
    caught only because the cell it came from emptied; a horse lifted off the table entirely is
    "not seen", not "in the gripper"; and every threshold above is a guess until H-001 produces a
    real still.
    """

    def __init__(self, calibration: Calibration, rules: Rules | None = None,
                 root: Path | str | None = None) -> None:
        self.calibration = calibration
        self.rules = rules or load_rules(root)
        cells = load_cells(root)
        self._cell_ids: tuple[str, ...] = tuple(cells)
        self._cell_xy = np.array([c.board_xy_mm for c in cells.values()], dtype=np.float64)
        self._kernel = (
            None
            if self.rules.morph_open_px <= 0
            else cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.rules.morph_open_px,) * 2)
        )
        self._die_region: np.ndarray | None = None

    def __repr__(self) -> str:
        bowl = "measured" if self.rules.bowl.measured else "placeholder"
        return (
            f"TopCameraPerception(frame={self.calibration.image_size_px[0]}x"
            f"{self.calibration.image_size_px[1]}, colors={''.join(self.rules.colors)}, bowl={bowl})"
        )

    # -- geometry ------------------------------------------------------------------------------

    def _scale_at(self, board_xy: np.ndarray) -> float:
        """Local pixels per millimetre at a board point, from the homography's own Jacobian.

        The square root of the area scaling, so ``(size_mm * scale) ** 2`` is the expected blob area
        there. Under a tilt the near edge of the board is bigger in pixels than the far edge, and
        this is what keeps one area band true across the whole frame.
        """
        origin = self.calibration.board_to_px(board_xy)
        dx = self.calibration.board_to_px(board_xy + np.array([1.0, 0.0])) - origin
        dy = self.calibration.board_to_px(board_xy + np.array([0.0, 1.0])) - origin
        return float(np.sqrt(abs(dx[0] * dy[1] - dx[1] * dy[0])))

    def _nearest_cell(self, board_xy: np.ndarray) -> tuple[str, float]:
        distances = np.linalg.norm(self._cell_xy - board_xy, axis=1)
        index = int(np.argmin(distances))
        return self._cell_ids[index], float(distances[index])

    def _check_frame(self, frame: np.ndarray) -> np.ndarray:
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
            shape = None if not isinstance(frame, np.ndarray) else frame.shape
            raise PerceptionError(f"expected an HxWx3 BGR frame, got {shape!r}")
        width, height = self.calibration.image_size_px
        if (frame.shape[1], frame.shape[0]) != (width, height):
            raise PerceptionError(
                f"frame is {frame.shape[1]}x{frame.shape[0]} but the calibration is for "
                f"{width}x{height}; a frame of another size maps every blob to the wrong board point"
            )
        return cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # -- detection -----------------------------------------------------------------------------

    def _blobs(self, hsv: np.ndarray, bands: tuple[tuple[np.ndarray, np.ndarray], ...]) -> list[np.ndarray]:
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for low, high in bands:
            mask |= cv2.inRange(hsv, low, high)
        if self._kernel is not None:
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return list(contours)

    @staticmethod
    def _centroid_px(contour: np.ndarray) -> tuple[float, float] | None:
        moments = cv2.moments(contour)
        if moments["m00"] > 0:
            return float(moments["m10"] / moments["m00"]), float(moments["m01"] / moments["m00"])
        points = contour.reshape(-1, 2).astype(np.float64)
        return (float(points[:, 0].mean()), float(points[:, 1].mean())) if len(points) else None

    def detect(self, frame: np.ndarray) -> BoardView:
        """Everything the top camera can say about one BGR frame."""
        hsv = self._check_frame(frame)
        rules = self.rules
        found: list[HorseDetection] = []
        for color, bands in rules.hsv_ranges.items():
            for contour in self._blobs(hsv, bands):
                centroid = self._centroid_px(contour)
                if centroid is None:
                    continue
                board_xy = self.calibration.px_to_board(np.asarray(centroid, dtype=np.float64))
                scale = self._scale_at(board_xy)
                expected = (rules.footprint_mm * scale) ** 2
                if expected <= 0:
                    continue
                ratio = _area_px(contour) / expected
                if ratio < rules.min_area_ratio:
                    continue
                (_, _), (w, h), _ = cv2.minAreaRect(contour)
                short, long_ = sorted((float(w) + 1.0, float(h) + 1.0))
                aspect = long_ / short if short > 0 else float("inf")
                standing = rules.standing_area_ratio[0] <= ratio <= rules.standing_area_ratio[1] and (
                    aspect <= rules.standing_max_aspect
                )
                cell_id, distance = self._nearest_cell(board_xy)
                found.append(
                    HorseDetection(
                        color=color,
                        pose=Pose.STANDING if standing else Pose.FALLEN,
                        placement=Placement.AT_CELL if distance <= rules.cell_snap_radius_mm
                        else Placement.BETWEEN_CELLS,
                        board_xy_mm=(float(board_xy[0]), float(board_xy[1])),
                        cell_id=cell_id if distance <= rules.cell_snap_radius_mm else None,
                        nearest_cell=cell_id,
                        nearest_mm=distance,
                        area_ratio=ratio,
                        aspect=aspect,
                        centroid_px=centroid,
                    )
                )
        return BoardView(horses=tuple(self._resolve_cells(found)), die=self._detect_die(hsv))

    @staticmethod
    def _resolve_cells(found: list[HorseDetection]) -> list[HorseDetection]:
        """One horse per cell: the nearest claims it, the rest become "between cells".

        Two blobs snapping to the same cell means the detector saw something it should not have --
        a horse half on a neighbour, a reflection split in two. Reporting both as "on the cell"
        would make the occupancy a lie; reporting the further one as loose keeps it visible.
        """
        claimed: dict[str, HorseDetection] = {}
        out: list[HorseDetection] = []
        for horse in sorted(found, key=lambda h: h.nearest_mm):
            if horse.cell_id is None or horse.cell_id not in claimed:
                if horse.cell_id is not None:
                    claimed[horse.cell_id] = horse
                out.append(horse)
            else:
                out.append(
                    HorseDetection(
                        color=horse.color, pose=horse.pose, placement=Placement.BETWEEN_CELLS,
                        board_xy_mm=horse.board_xy_mm, cell_id=None, nearest_cell=horse.nearest_cell,
                        nearest_mm=horse.nearest_mm, area_ratio=horse.area_ratio, aspect=horse.aspect,
                        centroid_px=horse.centroid_px,
                    )
                )
        return out

    def _die_search_mask(self, shape: tuple[int, int]) -> np.ndarray:
        """Pixels close enough to the bowl to be worth looking at. Built once, then cached."""
        if self._die_region is None:
            angles = np.linspace(0.0, 2.0 * np.pi, 64, endpoint=False)
            centre = np.asarray(self.rules.bowl.centre_mm, dtype=np.float64)
            circle = centre + self.rules.bowl.search_radius_mm * np.stack([np.cos(angles), np.sin(angles)], axis=1)
            region = np.zeros(shape, dtype=np.uint8)
            cv2.fillPoly(region, [np.round(self.calibration.board_to_px(circle)).astype(np.int32)], 255)
            self._die_region = region
        return self._die_region

    def _detect_die(self, hsv: np.ndarray) -> DieDetection:
        rules = self.rules
        low, high = rules.die_hsv_range
        mask = cv2.bitwise_and(cv2.inRange(hsv, low, high), self._die_search_mask(hsv.shape[:2]))
        if self._kernel is not None:
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best: tuple[float, np.ndarray] | None = None
        for contour in contours:
            centroid = self._centroid_px(contour)
            if centroid is None:
                continue
            board_xy = self.calibration.px_to_board(np.asarray(centroid, dtype=np.float64))
            expected = (rules.die_size_mm * self._scale_at(board_xy)) ** 2
            if expected <= 0:
                continue
            ratio = _area_px(contour) / expected
            if not rules.die_area_ratio[0] <= ratio <= rules.die_area_ratio[1]:
                continue
            distance = float(np.linalg.norm(board_xy - np.asarray(rules.bowl.centre_mm)))
            if best is None or distance < best[0]:
                best = (distance, board_xy)
        if best is None:
            return DieDetection(seen=False, in_bowl=False)
        distance, board_xy = best
        return DieDetection(
            seen=True,
            in_bowl=distance <= rules.bowl.diameter_mm / 2.0,
            board_xy_mm=(float(board_xy[0]), float(board_xy[1])),
        )

    # -- the Perception contract ---------------------------------------------------------------

    def _color_of(self, command: Command, before: BoardView) -> str | None:
        """Which colour the command is about: the engine's horse id if it names one, else the source cell."""
        horse_id = command.horse_id
        if horse_id:
            head = str(horse_id).split("-")[0]
            for candidate in (head, head[:1].upper()):
                if candidate in self.rules.hsv_ranges:
                    return candidate
        if command.src is not None:
            horse = before.cells.get(command.src.id)
            if horse is not None:
                return horse.color
        return None

    def _expectations(self, command: Command, before: BoardView) -> list[Any]:
        """What this command should change, as predicates on the board now.

        One list, used twice: :meth:`progress` counts how many already hold, and :meth:`verify`
        reads the same facts for its verdict. A primitive whose expectations all hold succeeded.
        """
        color = self._color_of(command, before)
        if command.primitive is Primitive.MOVE:
            if command.src is None or command.dst is None:
                raise PerceptionError(f"a MOVE needs both src and dst, got src={command.src}, dst={command.dst}")
            src, dst = command.src.id, command.dst.id
            return [
                lambda view: (view.cells.get(src) is None or view.cells[src].color != color),
                lambda view: (view.cells.get(dst) is not None and view.cells[dst].color == color),
            ]
        if command.primitive is Primitive.ROLL:
            return [lambda view: self._die_moved(before.die, view.die)]
        target = command.dst or command.src
        if target is None:                      # a RECOVER of the die: get it back into the bowl
            return [lambda view: view.die.in_bowl]
        cell = target.id
        return [
            lambda view: view.cells.get(cell) is not None,
            lambda view: view.cells.get(cell) is not None and view.cells[cell].pose is Pose.STANDING,
        ]

    @staticmethod
    def _die_distance(before: DieDetection, after: DieDetection) -> float:
        """How far the die moved, or ``inf`` when it was not seen at both ends."""
        if not (before.seen and after.seen) or before.board_xy_mm is None or after.board_xy_mm is None:
            return float("inf")
        return float(np.linalg.norm(np.asarray(after.board_xy_mm) - np.asarray(before.board_xy_mm)))

    def _die_moved(self, before: DieDetection, after: DieDetection) -> bool:
        """Did the die actually get picked up and dropped again?

        A roll that worked leaves the die in the bowl, which is where it started, so presence alone
        proves nothing: what proves it is that the die is somewhere *else* in the bowl. A die that
        was not in the bowl before and is now (a human put it back) also counts.
        """
        if not after.seen or not after.in_bowl:
            return False
        return self._die_distance(before, after) >= self.rules.die_moved_min_mm

    @staticmethod
    def _cell_signature(view: BoardView) -> dict[str, tuple[str, str]]:
        """Cell -> (colour, pose): the part of a view that a millimetre of detector noise cannot move."""
        return {cell: (horse.color, horse.pose.value) for cell, horse in view.cells.items()}

    def _changed_cells(self, before: BoardView, after: BoardView) -> set[str]:
        """Cells whose occupant changed colour, changed pose, arrived or left."""
        was, now = self._cell_signature(before), self._cell_signature(after)
        return {cell for cell in set(was) | set(now) if was.get(cell) != now.get(cell)}

    def _loose_changed(self, before: BoardView, after: BoardView) -> bool:
        """Did the set of horses that are on no cell change, beyond detector jitter?"""
        tolerance = self.rules.cell_snap_radius_mm / 2.0
        if len(before.loose) != len(after.loose):
            return True
        remaining = list(after.loose)
        for horse in before.loose:
            match = next(
                (
                    other for other in remaining
                    if other.color == horse.color and other.pose is horse.pose
                    and float(np.linalg.norm(np.asarray(other.board_xy_mm) - np.asarray(horse.board_xy_mm)))
                    <= tolerance
                ),
                None,
            )
            if match is None:
                return True
            remaining.remove(match)
        return False

    def _material_change(self, before: BoardView, after: BoardView) -> bool:
        """Did anything happen on the table, ignoring what a noisy detector moves by itself?

        ``view_delta`` records *every* difference, down to the last tenth of a millimetre, because
        that is the evidence that goes into the outcome. "Nothing changed" -- which is what makes a
        MOVE a failed grasp and a RECOVER a no-progress timeout -- must not be defeated by a
        centroid wobbling half a pixel between two frames, so it is asked of this instead: a cell
        that changed hands or pose, a loose horse that appeared, vanished or moved further than half
        the snap radius, or a die that changed state or moved further than ``die_moved_min_mm``.
        """
        if self._changed_cells(before, after) or self._loose_changed(before, after):
            return True
        if (before.die.seen, before.die.in_bowl) != (after.die.seen, after.die.in_bowl):
            return True
        # An unknown distance (the die was not seen at both ends) is not evidence that it moved.
        moved = self._die_distance(before.die, after.die)
        return bool(np.isfinite(moved)) and moved >= self.rules.die_moved_min_mm

    def progress(self, command: Command, before: dict[str, Any], now: dict[str, Any]) -> float:
        """Share of this command's expected changes that have happened, in ``[0, 1]``.

        The watchdog reads only the direction (``runtime/controller.py``), so what matters is that
        it rises as the primitive gets closer to done: a MOVE is at 0.5 once the horse has left its
        source cell and at 1.0 once it stands on the target.
        """
        before_view, now_view = BoardView.from_dict(before), BoardView.from_dict(now)
        checks = self._expectations(command, before_view)
        if not checks:
            return 0.0
        return sum(1.0 for check in checks if check(now_view)) / len(checks)

    def verify(self, command: Command, before: dict[str, Any], after: dict[str, Any]) -> Outcome:
        """Judge one primitive from the board before and after it, in the vocabulary of 6.5."""
        before_view, after_view = BoardView.from_dict(before), BoardView.from_dict(after)
        delta = view_delta(before_view, after_view)
        if command.primitive is Primitive.MOVE:
            failure = self._judge_move(command, before_view, after_view)
        elif command.primitive is Primitive.ROLL:
            failure = self._judge_roll(before_view, after_view)
        else:
            failure = self._judge_recover(command, before_view, after_view)
        if failure is None:
            return Outcome(success=True, observed_state_delta=delta, failure_mode=None)
        return Outcome(success=False, observed_state_delta=delta, failure_mode=failure.value)

    def _judge_move(self, command: Command, before: BoardView, after: BoardView) -> FailureMode | None:
        """A MOVE, which is also an enter-from-base and also a capture (5.5).

        In order: did our colour arrive standing on the target (and leave the source, or it was a
        different horse of the same colour); did it fall; did it never leave, and did something else
        move instead; is it somewhere it should not be.
        """
        if command.src is None or command.dst is None:
            raise PerceptionError(f"a MOVE needs both src and dst, got src={command.src}, dst={command.dst}")
        color = self._color_of(command, before)
        src, dst = command.src.id, command.dst.id
        src_after, dst_after = after.cells.get(src), after.cells.get(dst)
        src_before = before.cells.get(src)
        still_at_src = (
            src_after is not None and src_before is not None
            and src_after.color == src_before.color == color and src_after.pose is Pose.STANDING
        )

        if dst_after is not None and dst_after.color == color:
            if still_at_src:
                # Our colour is on the target and still on the source: a second horse of the same
                # colour was moved. The cell the engine asked for is right, the horse is not.
                return FailureMode.WRONG_HORSE
            return None if dst_after.pose is Pose.STANDING else FailureMode.HORSE_FELL
        if not self._material_change(before, after):
            # The table is exactly as it was: the gripper closed on nothing (6.5).
            return FailureMode.GRASP_FAILED
        if still_at_src:
            # Our horse never left its cell, yet some other piece did move: the wrong horse was
            # taken (6.5, "grasp on the wrong horse (adjacent cell)"). Nothing but the die moving
            # is a grasp that closed on nothing.
            other_moved = bool(self._changed_cells(before, after) - {src}) or self._loose_changed(before, after)
            return FailureMode.WRONG_HORSE if other_moved else FailureMode.GRASP_FAILED
        fell = [
            horse for horse in after.horses
            if horse.color == color and horse.pose is Pose.FALLEN and not self._was_fallen(before, horse)
        ]
        if fell:
            return FailureMode.HORSE_FELL
        # It left the source and is standing somewhere that is not the target: between cells, off
        # the magnet, on the wrong cell, or out of the frame altogether. All of it is missed_cell.
        return FailureMode.MISSED_CELL

    @staticmethod
    def _was_fallen(before: BoardView, horse: HorseDetection) -> bool:
        """Was a horse of this colour already fallen at about this place before the primitive?"""
        for other in before.horses:
            if other.color != horse.color or other.pose is not Pose.FALLEN:
                continue
            if float(np.linalg.norm(np.asarray(other.board_xy_mm) - np.asarray(horse.board_xy_mm))) < 1.0:
                return True
        return False

    def _judge_roll(self, before: BoardView, after: BoardView) -> FailureMode | None:
        """A ROLL: pick the die out of the bowl, drop it back in (5.5)."""
        if not after.die.seen or not after.die.in_bowl:
            return FailureMode.DIE_OUT_OF_BOWL
        if not self._die_moved(before.die, after.die):
            # The die is exactly where it was: it was never picked up.
            return FailureMode.DIE_GRASP_FAILED
        return None

    def _judge_recover(self, command: Command, before: BoardView, after: BoardView) -> FailureMode | None:
        """A RECOVER: put one horse back upright on its cell -- or the die back in the bowl."""
        target = command.dst or command.src
        if target is None:
            return None if after.die.in_bowl else FailureMode.DIE_OUT_OF_BOWL
        cell = target.id
        color = self._color_of(command, before)
        at_cell = after.cells.get(cell)
        if at_cell is not None:
            if color is not None and at_cell.color != color:
                return FailureMode.WRONG_HORSE
            return None if at_cell.pose is Pose.STANDING else FailureMode.HORSE_FELL
        near = [horse for horse in after.loose if horse.nearest_cell == cell]
        if any(horse.pose is Pose.FALLEN for horse in near):
            return FailureMode.HORSE_FELL
        if near:
            return FailureMode.MISSED_CELL
        if not self._material_change(before, after):
            return FailureMode.TIMEOUT_NO_PROGRESS
        return FailureMode.MISSED_CELL
