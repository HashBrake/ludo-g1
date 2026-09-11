"""Did the command actually happen? The state check of CLAUDE.md 5.5 (placeholder implementation).

``runtime/controller.py`` runs ``next_command`` -> execute -> **verify** -> ``report``. This module
is that verify step: it compares the board before the primitive with the board after it and turns the
difference into an :class:`~engine.interface.Outcome` with one of the labelled failure modes of
CLAUDE.md 6.5. Verifying the world instead of trusting the policy is required by R5 and is what makes
the engine's recovery loop possible at all.

That vocabulary is :class:`FailureMode`, and it lives here -- in the module that *produces* the
strings -- rather than in ``eval/``, which imports it. One definition, so that a failure the robot
had and a failure the eval JSON counts can never become two spellings of the same thing.

**The real implementation does not exist yet.** It detects horses and the die in the Brio frame
(``board/perception.py`` per CLAUDE.md 5.1, "placeholder until the engine team delivers") and needs
both the calibration of T-008 and the engine team's real cell ids. :class:`MockPerception` stands in
until then, and it is deliberately blind: it reads *the engine's own view* of the board, not the
table. It therefore cannot see a horse that fell over, a horse that missed the magnet, or a die that
bounced out of the bowl -- it only sees whether the engine's state changed at all. On the stub engine
the state changes only when a command is reported successful, so every primitive executed with
:class:`~runtime.policy_api.HoldPolicy` verifies as ``timeout_no_progress``. That is the correct
answer for a robot that did not move, and it is the expected result of every mock run.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from engine.interface import Command, Outcome, Primitive

__all__ = ["NO_PROGRESS", "FailureMode", "MockPerception", "Perception", "state_delta"]


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
    ``TIMEOUT_NO_PROGRESS``       the policy stalled and the watchdog ended the primitive
    ============================  ==================================================================
    """

    HORSE_FELL = "horse_fell"
    MISSED_CELL = "missed_cell"
    GRASP_FAILED = "grasp_failed"
    WRONG_HORSE = "wrong_horse"
    DIE_OUT_OF_BOWL = "die_out_of_bowl"
    DIE_GRASP_FAILED = "die_grasp_failed"
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
    """The contract ``runtime/controller.py`` verifies through. One method, no state."""

    def verify(self, command: Command, before: dict[str, Any], after: dict[str, Any]) -> Outcome:
        """Judge one primitive execution from the board before and after it.

        ``before``/``after`` are whatever the caller uses as its view of the board: the engine's
        ``board_state()`` today, the Brio detections of the real implementation later. The returned
        :class:`~engine.interface.Outcome` is reported to the engine unchanged.
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
