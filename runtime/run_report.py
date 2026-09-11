"""What a run of ``runtime/controller.py`` reports: the counted summary and the per-trial log.

Two things live here, both of them bookkeeping and neither of them able to move a robot:

:class:`RunSummary`
    the cumulative counters of one controller -- commands, policy calls, actions, refusals -- and
    the printed lines of a run. Every number in it was counted, never described (R5).
:class:`TrialLog`
    one JSON line per completed or halted primitive, appended to
    ``data/logs/controller_<session>.trials.jsonl``. ``eval/run_eval.py`` reads it back and
    cross-checks it against its own per-trial rows, so the eval JSON and what the loop actually did
    can never quietly disagree (R5). :func:`read_trials` is that reader.

They are a sibling module rather than part of ``runtime/controller.py`` so that the loop file stays
about the loop (agents/DECISIONS.md D-013). Nothing here imports the controller, so the dependency
runs one way only.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.interface import Command, Outcome

__all__ = ["Mark", "RunSummary", "TrialLog", "read_trials"]


def _counts(counter: Counter) -> str:
    """``a=1, b=2`` for one printed summary line, or ``none``."""
    return ", ".join(f"{k}={v}" for k, v in sorted(counter.items())) or "none"


@dataclass
class RunSummary:
    """What one run did. Every number here is counted, never described (R5)."""

    commands: int = 0
    succeeded: int = 0
    policy_calls: int = 0
    actions_sent: int = 0
    refused: int = 0
    align_failures: int = 0
    elapsed_s: float = 0.0
    primitives: Counter = field(default_factory=Counter)
    failure_modes: Counter = field(default_factory=Counter)
    stopped_by: Counter = field(default_factory=Counter)

    @property
    def policy_hz(self) -> float:
        """Measured rate of the 10 Hz loop: policy calls per second of run time."""
        return self.policy_calls / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def action_hz(self) -> float:
        """Measured rate of the 30 Hz action stream."""
        return self.actions_sent / self.elapsed_s if self.elapsed_s > 0 else 0.0

    def lines(self) -> list[str]:
        """The printed summary of a run, one fact per line."""
        return [
            f"elapsed            {self.elapsed_s:.2f} s",
            f"commands executed  {self.commands} ({_counts(self.primitives)})",
            f"outcomes           {self.succeeded} success, {self.commands - self.succeeded} failure",
            f"failure modes      {_counts(self.failure_modes)}",
            f"stopped by         {_counts(self.stopped_by)}",
            f"policy calls       {self.policy_calls} = {self.policy_hz:.2f} Hz",
            f"actions sent       {self.actions_sent} = {self.action_hz:.2f} Hz",
            f"safety refusals    {self.refused}",
            f"alignment failures {self.align_failures}",
        ]


@dataclass(frozen=True)
class Mark:
    """The cumulative counters of a :class:`RunSummary` at the instant one command started.

    The summary accumulates over the controller's whole life, so what a single command cost is a
    difference between two of these, never a counter of its own to keep in step.
    """

    ts_ns: int
    actions_sent: int
    policy_calls: int
    refused: int
    align_failures: int

    @classmethod
    def of(cls, summary: RunSummary, ts_ns: int) -> Mark:
        """Snapshot ``summary`` at ``ts_ns``."""
        return cls(int(ts_ns), summary.actions_sent, summary.policy_calls, summary.refused,
                   summary.align_failures)

    def cost(self, summary: RunSummary, now_ns: int) -> dict[str, Any]:
        """What happened between this mark and ``now_ns``: duration and the counter differences."""
        return {
            "duration_s": round((int(now_ns) - self.ts_ns) / 1e9, 3),
            "actions_sent": summary.actions_sent - self.actions_sent,
            "policy_calls": summary.policy_calls - self.policy_calls,
            "safety_refusals": summary.refused - self.refused,
            "align_failures": summary.align_failures - self.align_failures,
        }


class TrialLog:
    """One JSON line per primitive execution, appended to a ``.trials.jsonl`` file.

    The line is the whole record of one execution -- the command, what perception (or the watchdog)
    concluded, what the loop cost, and the watchdog's verdict -- so a run's failures can be counted
    from the log alone, by ``eval/run_eval.py`` today and by a phase report later.

    :attr:`records` keeps this process's own lines in memory as well: a reader can then compare what
    it was handed with what reached the disk without having to guess which lines of an appended file
    belong to which run. Failing to write a line never stops the loop -- the same rule as the
    heartbeat -- and :attr:`write_failures` counts the lines that did not land.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.records: list[dict[str, Any]] = []
        self.write_failures = 0

    def __repr__(self) -> str:
        return f"TrialLog(path={str(self.path)!r}, records={len(self.records)})"

    def record(self, *, session: str, index: int, ts_ns: int, command: Command, outcome: Outcome,
               stopped_by: str, cost: dict[str, Any], watchdog: dict[str, Any]) -> dict[str, Any]:
        """Write the line for one execution: the command, the verdict, the cost, the watchdog.

        This is the schema ``eval/run_eval.py`` reads back and ``docs/controller.md`` documents; it
        is built here, in one place, so the writer and the reader cannot drift apart.
        """
        return self.append({
            "session": session,
            "index": int(index),
            "ts_ns": int(ts_ns),
            "command": {"primitive": command.primitive.value,
                        "src": None if command.src is None else command.src.id,
                        "dst": None if command.dst is None else command.dst.id,
                        "horse_id": command.horse_id},
            "success": bool(outcome.success),
            "failure_mode": outcome.failure_mode,
            "stopped_by": stopped_by,
            "watchdog": dict(watchdog),
            "observed_state_delta": outcome.observed_state_delta,
            **cost,
        })

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Record one execution and return it, appending one JSON line to :attr:`path`."""
        self.records.append(record)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            self.write_failures += 1
        return record


def read_trials(path: Path | str) -> list[dict[str, Any]]:
    """Every record in a ``.trials.jsonl`` file, in the order it was written.

    A missing file is an empty list (a run that executed no command writes none); a malformed line
    is an error, because a log that cannot be parsed is not evidence of anything.
    """
    file = Path(path)
    if not file.exists():
        return []
    out: list[dict[str, Any]] = []
    for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{file}:{number}: not a JSON record: {exc}") from exc
    return out
