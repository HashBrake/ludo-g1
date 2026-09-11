"""What a trial *is*: the trial sets of CLAUDE.md Phase 3/4, and what counts as a success (R5).

``eval/run_eval.py`` runs trials; this module defines them and the shape of what a run records, so
that a number in an eval result is always traceable to a definition that was written down before the
run. Nothing here touches a robot, a driver, a policy or a dataset: a trial is a
:class:`~engine.interface.Command` plus the bookkeeping (seed, perturbation) that makes the run
reproducible and the result countable.

The four trial kinds are exactly the evaluations CLAUDE.md section 6 asks for:

=============  ====================================================================================
kind           what it is
=============  ====================================================================================
``move``       MOVE over **held-out** cell pairs (Phase 3: "20 trials on held-out cell pairs")
``roll``       ROLL over the bowl (Phase 4: "evaluate each primitive separately, 20 trials each")
``recover``    RECOVER at a cell, one perturbation of 6.5 per trial (Phase 4)
``sequence``   the 20-move scripted sequence of ``engine/scripts/eval_20_moves.yaml`` (Phase 4)
=============  ====================================================================================

The held-out pairs are an **argument**, never a thing this module decides: the train/held-out split
of the recorded sessions belongs to ``policy/dataset.py`` (T-027), and importing it here would let an
eval quietly score itself on pairs the policy trained on. :func:`script_pairs` offers the ten pairs of
the eval script as the default held-out set, because that file was written to be exactly that.

Success is not "the policy said so" and not "nothing crashed": :class:`SuccessCriterion` states, per
primitive, which fields of the reported :class:`~engine.interface.Outcome` must hold, and
:func:`judge` is the only thing that turns an Outcome into a counted success. See docs/eval.md.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from board.perception import FailureMode
from engine.cells import Layout, load_cells, load_layout
from engine.interface import Cell, Command, EngineClient, Outcome, Primitive
from engine.stub import load_script

__all__ = [
    "CRITERIA",
    "KINDS",
    "PERTURBATIONS",
    "RESULTS_DIR",
    "SEQUENCE_SCRIPT",
    "UNLABELLED",
    "AttributedEngine",
    "FailureMode",
    "SuccessCriterion",
    "Trial",
    "blank_row",
    "failure_key",
    "judge",
    "make_trials",
    "print_result",
    "record_execution",
    "script_pairs",
    "summarise",
    "write_result",
]

#: Where results go. A git-tracked directory with untracked contents: a result is committed
#: deliberately, when it is a number the project stands behind, never as a side effect of a run.
RESULTS_DIR: Path = Path(__file__).resolve().parent / "results"

#: The trial kinds :func:`make_trials` knows, in the order CLAUDE.md section 6 asks for them.
KINDS: tuple[str, ...] = ("move", "roll", "recover", "sequence")

#: The scripted sequence of Phase 4, and the source of the default held-out pair list.
SEQUENCE_SCRIPT = "eval_20_moves"

#: The states a RECOVER trial starts from (CLAUDE.md Phase 4, drawn from the 6.5 failure cases).
#: A human sets one of these up before the trial; the label goes in the result so that a recovery
#: success rate can be broken down by what it had to recover from.
PERTURBATIONS: tuple[str, ...] = ("horse_on_side", "horse_on_back", "between_cells", "missed_magnet")

#: The key a failure with no labelled mode is counted under. Not a 6.5 mode: seeing it in a result
#: means perception failed a command without saying why, which is a finding, not a category.
UNLABELLED = "unlabelled"


@dataclass(frozen=True)
class Trial:
    """One evaluation trial: one primitive execution, and everything needed to repeat it.

    ``index`` is the trial's position in its set (it is also the order the engine hands the commands
    out in). ``seed`` is the seed of the *set* the trial was drawn from, carried per trial so that a
    single result row is enough to reconstruct the set with :func:`make_trials`.

    ``perturbed`` says a human disturbed the scene before the trial started -- always true for a
    RECOVER trial, whose ``perturbation`` names which of :data:`PERTURBATIONS` was set up.
    """

    index: int
    primitive: Primitive
    src: Cell | None
    dst: Cell | None
    horse_id: str | None
    seed: int
    perturbed: bool = False
    perturbation: str | None = None

    def command(self) -> Command:
        """The :class:`~engine.interface.Command` the engine hands to the controller."""
        return Command(self.primitive, self.src, self.dst, self.horse_id)

    @property
    def pair(self) -> tuple[str, str] | None:
        """``(src_id, dst_id)`` when the trial addresses two cells, else ``None``."""
        if self.src is None or self.dst is None:
            return None
        return (self.src.id, self.dst.id)

    def to_dict(self) -> dict:
        """The trial as it appears in an eval result JSON (cells by id; docs/eval.md)."""
        return {
            "index": self.index,
            "primitive": self.primitive.value,
            "src": None if self.src is None else self.src.id,
            "dst": None if self.dst is None else self.dst.id,
            "horse_id": self.horse_id,
            "seed": self.seed,
            "perturbed": self.perturbed,
            "perturbation": self.perturbation,
        }


@dataclass(frozen=True)
class SuccessCriterion:
    """What the reported :class:`~engine.interface.Outcome` must say for a trial to count.

    Two requirements are shared by every primitive -- ``success`` is True and ``failure_mode`` is
    ``None`` -- and each primitive adds what perception must actually have *seen*, because R5 asks
    for a measurement and "the engine was told it worked" is not one. A criterion is data, so the
    definition a number was scored under is printable and lands in docs/eval.md unchanged.
    """

    primitive: Primitive
    requires_horse_at_dst: bool
    requires_die_change: bool
    description: str

    def met(self, trial: Trial, outcome: Outcome) -> bool:
        """Does ``outcome`` satisfy this criterion for ``trial``?"""
        if not outcome.success or outcome.failure_mode is not None:
            return False
        delta = outcome.observed_state_delta or {}
        if self.requires_die_change and "die" not in delta:
            return False
        if self.requires_horse_at_dst:
            if trial.horse_id is None or trial.dst is None:
                return False
            moved = delta.get(trial.horse_id)
            if not isinstance(moved, (list, tuple)) or len(moved) != 2 or moved[1] != trial.dst.id:
                return False
        return True


#: The success criterion of every primitive (CLAUDE.md 6.5, 5.5). RECOVER asks only for a clean
#: Outcome: a recovery puts a horse back where the engine already believes it is, so it changes no
#: board state by construction and no state delta can be demanded of it (agents/DECISIONS.md D-013).
CRITERIA: dict[Primitive, SuccessCriterion] = {
    Primitive.MOVE: SuccessCriterion(
        Primitive.MOVE, requires_horse_at_dst=True, requires_die_change=False,
        description="Outcome.success, no failure_mode, and observed_state_delta puts horse_id on dst",
    ),
    Primitive.ROLL: SuccessCriterion(
        Primitive.ROLL, requires_horse_at_dst=False, requires_die_change=True,
        description="Outcome.success, no failure_mode, and observed_state_delta contains a new die value",
    ),
    Primitive.RECOVER: SuccessCriterion(
        Primitive.RECOVER, requires_horse_at_dst=False, requires_die_change=False,
        description="Outcome.success and no failure_mode; a recovery changes no board state by design",
    ),
}


def judge(trial: Trial, outcome: Outcome) -> bool:
    """The one place an :class:`~engine.interface.Outcome` becomes a counted success (R5)."""
    return CRITERIA[trial.primitive].met(trial, outcome)


def failure_key(outcome: Outcome) -> str:
    """The key a failed trial is counted under: its 6.5 mode, or :data:`UNLABELLED`.

    A mode that is not one of :class:`~board.perception.FailureMode`'s strings is passed through
    unchanged rather than silently folded into ``unlabelled``: an unknown label is evidence of a bug
    in whoever produced it, and hiding it would lose that evidence.
    """
    mode = outcome.failure_mode
    if mode is None:
        return UNLABELLED
    known = FailureMode.parse(mode)
    return known.value if known is not None else str(mode)


def record_execution(row: dict, trial: Trial, outcome: Outcome, *, own: bool, first: bool,
                     cost: Mapping[str, Any]) -> None:
    """Fold one execution of ``trial`` into its result row, in place.

    ``cost`` is what the controller charged for this one command (duration, actions, policy calls,
    safety refusals, and why it stopped); it is *added*, because a trial with engine-level recovery
    is executed more than once and its row must show what the whole trial cost.

    Only an execution of the trial's **own** command decides the trial (``own=True``): a RECOVER the
    engine injected is bookkeeping about the failure, not a fresh attempt at the thing being
    measured, and a later attempt supersedes an earlier one. Anything but a first attempt counts one
    ``engine_retries``.
    """
    row["duration_s"] = round(row["duration_s"] + float(cost["duration_s"]), 3)
    for key in ("actions_sent", "policy_calls", "safety_refusals"):
        row[key] += int(cost[key])
    row["engine_retries"] += 0 if first else 1
    if own:
        row["success"] = judge(trial, outcome)
        row["reported_success"] = bool(outcome.success)
        row["failure_mode"] = None if row["success"] else failure_key(outcome)
        row["stopped_by"] = cost["stopped_by"]
        row["observed_state_delta"] = outcome.observed_state_delta


def write_result(result: dict, tag: str, out_dir: Path | str = RESULTS_DIR) -> Path:
    """Write ``<out_dir>/<timestamp>_<tag>.json`` and return the path (docs/eval.md)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(result["created_at"]).strftime("%Y%m%dT%H%M%S")
    path = out / f"{stamp}_{tag}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return path


def print_result(result: dict) -> None:
    """The success rate with its trial count, then the per-failure-mode table. R5 in two lines."""
    summary = result["summary"]
    print(f"success {summary['success']}/{summary['n']} ({100 * summary['rate']:.1f}%)")
    print("failure modes")
    if not summary["by_failure_mode"]:
        print("  none")
    for mode, count in sorted(summary["by_failure_mode"].items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {mode:<22} {count}")


def blank_row(trial: Trial) -> dict:
    """One trial's result row before it has run. This is the per-trial schema of docs/eval.md.

    ``success`` is :func:`judge`'s verdict and ``reported_success`` is what perception actually said;
    they differ exactly when an Outcome claims a success the criterion did not see evidence for, and
    a result where they differ is a finding about perception, not a better success rate.
    """
    return dict(
        trial.to_dict(), success=False, reported_success=False, failure_mode=None, duration_s=0.0,
        actions_sent=0, policy_calls=0, safety_refusals=0, engine_retries=0, stopped_by=None,
        observed_state_delta={},
    )


def summarise(rows: Sequence[dict]) -> dict:
    """The ``summary`` block of a result: the counted rate and the per-failure-mode table (R5).

    Only a *failed* trial contributes to the table, under :func:`failure_key`, so the modes always
    sum to ``n - success``.
    """
    modes = Counter(row["failure_mode"] for row in rows if not row["success"] and row["failure_mode"])
    successes = sum(1 for row in rows if row["success"])
    return {
        "success": successes,
        "n": len(rows),
        "rate": successes / len(rows) if rows else 0.0,
        "by_failure_mode": dict(sorted(modes.items())),
        "engine_retries": sum(row["engine_retries"] for row in rows),
        "safety_refusals": sum(row["safety_refusals"] for row in rows),
        "duration_s": round(sum(row["duration_s"] for row in rows), 3),
    }


class AttributedEngine(EngineClient):
    """The engine, wrapped so that every execution is attributed to the trial that caused it.

    The stub hands out the trial commands in order -- the very objects it was given, so identity is
    the test -- and, when engine-level recovery is on, injects RECOVER commands and re-issues of its
    own between them (``docs/engine.md``). Those extra executions belong to the trial in progress:
    they are counted as its ``engine_retries`` and never become trials of their own, or a 20-trial
    eval would silently report a different trial count than the one it was asked for.

    :attr:`executions` gets one entry per command handed out, ``(trial index, own, first)``: ``own``
    is False for a RECOVER the engine invented, and ``first`` is False for every execution after a
    trial's first attempt.
    """

    def __init__(self, inner: EngineClient, commands: Sequence[Command]) -> None:
        self.inner = inner
        self._commands = list(commands)
        self.index = -1
        self.executions: list[tuple[int, bool, bool]] = []
        self.outcomes: list[Outcome] = []

    def next_command(self) -> Command | None:
        command = self.inner.next_command()
        if command is None:
            return None
        following = self.index + 1
        if following < len(self._commands) and command is self._commands[following]:
            self.index = following
            self.executions.append((self.index, True, True))
        else:
            own = self.index >= 0 and command is self._commands[self.index]
            self.executions.append((self.index, own, False))
        return command

    def report(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)
        self.inner.report(outcome)

    def board_state(self) -> dict[str, Any]:
        return self.inner.board_state()


def script_pairs(name: str | Path = SEQUENCE_SCRIPT, cells: Mapping[str, Cell] | None = None) -> list[tuple[str, str]]:
    """The distinct ``(src, dst)`` cell pairs of a command script, in first-appearance order.

    ``engine/scripts/eval_20_moves.yaml`` was written as ten pairs run twice, so this is the default
    held-out pair list until ``policy/dataset.py`` (T-027) splits the recorded sessions instead.
    """
    pairs: list[tuple[str, str]] = []
    for command in load_script(name, None if cells is None else dict(cells)):
        if command.src is None or command.dst is None:
            continue
        pair = (command.src.id, command.dst.id)
        if pair not in pairs:
            pairs.append(pair)
    return pairs


def _robot_horses(layout: Layout, color: str) -> list[str]:
    """The robot's own horse ids, e.g. ``["R0", "R1", "R2", "R3"]`` (``engine/stub.py`` names them)."""
    return [f"{color}{i}" for i in range(layout.base_size)]


def _pair_order(pairs: Sequence[tuple[str, str]], n: int, rng: random.Random) -> list[tuple[str, str]]:
    """``n`` pairs, one seeded shuffle of the whole list per lap.

    Reshuffling each lap is what keeps a pair from being run twice in a row when ``n`` is a multiple
    of the pair count (``eval_20_moves.yaml`` reorders its second pass by hand for the same reason).
    """
    order: list[tuple[str, str]] = []
    while len(order) < n:
        lap = list(pairs)
        rng.shuffle(lap)
        order.extend(lap)
    return order[:n]


def make_trials(
    kind: str,
    n: int,
    held_out_pairs: Sequence[tuple[str, str]] | None = None,
    seed: int = 0,
    *,
    cells: Mapping[str, Cell] | None = None,
    layout: Layout | None = None,
    color: str = "R",
    bowl: Cell | None = None,
) -> list[Trial]:
    """The ``n``-trial set of ``kind``, reproducible from ``seed`` alone.

    ``held_out_pairs`` is required for ``kind="move"`` and ignored by every other kind: only a MOVE
    set is defined over cell pairs. Pass :func:`script_pairs` for the default set. Every id is
    resolved against ``config/board.yaml``, so a pair naming a cell that does not exist is an error
    and never a silently skipped trial.

    ``kind="sequence"`` reads :data:`SEQUENCE_SCRIPT` and therefore has a fixed length; ``n`` must
    equal it, so that a caller asking for 20 and getting 19 hears about it.

    ``bowl`` is the cell a ROLL addresses. It defaults to ``None`` for the same reason
    ``engine/stub.py`` does: ``config/board.yaml`` describes no bowl and inventing one would put a
    goal heatmap somewhere real on the board.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown trial kind {kind!r}; known kinds: {', '.join(KINDS)}")
    if n < 1:
        raise ValueError(f"a trial set needs n >= 1, got {n}")
    table = dict(load_cells() if cells is None else cells)
    layout = load_layout() if layout is None else layout
    horses = _robot_horses(layout, color)
    rng = random.Random(seed)

    if kind == "sequence":
        commands = load_script(SEQUENCE_SCRIPT, table)
        if n != len(commands):
            raise ValueError(
                f"kind='sequence' is {SEQUENCE_SCRIPT}, which has {len(commands)} commands; n={n} would be a "
                f"different evaluation. Pass n={len(commands)}."
            )
        return [
            Trial(i, c.primitive, c.src, c.dst, c.horse_id, seed)
            for i, c in enumerate(commands)
        ]

    if kind == "roll":
        return [Trial(i, Primitive.ROLL, bowl, bowl, None, seed) for i in range(n)]

    if kind == "recover":
        ids = list(table)
        return [
            Trial(i, Primitive.RECOVER, table[cell], table[cell], horses[i % len(horses)], seed,
                  perturbed=True, perturbation=PERTURBATIONS[i % len(PERTURBATIONS)])
            for i, cell in enumerate(rng.choice(ids) for _ in range(n))
        ]

    if not held_out_pairs:
        raise ValueError(
            "kind='move' needs held_out_pairs: a MOVE eval is defined by the cell pairs it is held out "
            "on (CLAUDE.md Phase 3). Pass eval.protocol.script_pairs() or the held-out half of the "
            "train/eval split."
        )
    unknown = sorted({c for pair in held_out_pairs for c in pair} - set(table))
    if unknown:
        raise ValueError("held_out_pairs names cell(s) config/board.yaml does not define: " + ", ".join(unknown))
    return [
        Trial(i, Primitive.MOVE, table[src], table[dst], horses[i % len(horses)], seed)
        for i, (src, dst) in enumerate(_pair_order(list(held_out_pairs), n, rng))
    ]
