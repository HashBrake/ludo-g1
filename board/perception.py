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
:class:`~runtime.policy_api.HoldPolicy` shows no progress at all. That is the correct answer for a
robot that did not move, and it is the expected result of every mock run: the controller's watchdog
halts such a primitive as ``policy_stalled`` once ``runtime.watchdog_stall_s`` has passed with the
board unchanged, and :meth:`MockPerception.verify` answers ``timeout_no_progress`` whenever a
primitive instead runs to some other end with nothing having changed.
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
