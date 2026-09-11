"""Run a trial set through ``runtime/controller.py`` and write the JSON every success rate comes from.

```bash
.venv/bin/python -m eval.run_eval --backend mock --kind move --n 20 --policy hold
```

One :class:`~runtime.controller.Controller` plays the trial commands out of a scripted
:class:`~engine.stub.StubEngine`, one command at a time, and the difference in its
:class:`~runtime.controller.RunSummary` across each command is that command's measurement. The result
is written to ``eval/results/<timestamp>_<tag>.json`` with the git commit and the four config hashes
it was produced under, so no number this project reports is ever separable from what produced it (R5).

Engine-level recovery (``docs/engine.md``) is **on for ``kind="sequence"`` only**: a per-primitive
success rate must count one attempt per trial, while the scripted sequence of Phase 4 is explicitly
evaluated with the engine's retries enabled and counted. Recovery commands are attributed to the trial
that provoked them and counted in that trial's ``engine_retries``.

On ``--backend mock`` the clock is fake: the loop's ``sleep_until`` jumps instead of sleeping, so 20
trials of a 20 s primitive timeout take ~10 s of wall time instead of 400 s, at exactly the nominal
rates. Pass ``--realtime`` to run the mocks on the real clock. ``--backend real`` refuses, here as in
``runtime/controller.py``: no actuated real driver exists (Phase 1), and a placeholder policy is never
deployed (R2). See docs/eval.md.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from board.perception import MockPerception
from drivers import make
from engine.stub import StubEngine
from eval import protocol
from eval.protocol import RESULTS_DIR, AttributedEngine, Trial
from runtime import clock, config
from runtime.controller import Controller
from runtime.policy_api import HoldPolicy, Policy
from runtime.safety import REPO_ROOT

__all__ = ["RESULTS_DIR", "FakeClock", "main", "make_policy", "run_trials"]

#: Config files whose hash every result records (5.6).
HASHED_CONFIGS: tuple[str, ...] = ("safety", "robot", "board", "training")

#: Engine re-issues allowed per command: the stub's own budget for ``sequence``, none otherwise.
SEQUENCE_REISSUES = 2


class FakeClock:
    """A monotonic nanosecond clock that moves only when the loop waits for it.

    Injected as the controller's ``now_ns`` and ``sleep_until`` on mocks: the loop's rates and
    timeouts are then exact and a 20 s primitive costs its real work, not 20 s of sleeping.
    """

    def __init__(self, ns: int = 0) -> None:
        self.ns = int(ns)

    def __call__(self) -> int:
        return self.ns

    def jump_to(self, ts_ns: int) -> None:
        self.ns = max(self.ns, int(ts_ns))


def make_policy(spec: Sequence[str], backend: str) -> tuple[Policy, dict]:
    """Build the policy named by ``--policy`` and describe it for the result JSON.

    ``hold`` is :class:`~runtime.policy_api.HoldPolicy`, which commands no motion and scores 0 by
    construction; like :func:`runtime.controller.build` it is refused on any backend but mocks (R2).
    ``bundle PATH`` loads the inference bundle ``policy/export.py`` wrote -- either model of 5.7,
    whichever the bundle names (:func:`policy.export.open_bundle`); the result records the bundle's
    policy, its weights hash and the training run behind it, so a success rate names the checkpoint
    it belongs to (R5).
    """
    parts = list(spec) or ["hold"]
    tag = parts[0]
    if tag == "hold":
        if backend != "mock":
            raise NotImplementedError(
                f"no learned policy exists yet (Phase 3, CLAUDE.md section 6); backend={backend!r} needs one, and "
                "HoldPolicy is a test double that must never be deployed (R2)"
            )
        return HoldPolicy(), {"tag": "hold", "checkpoint": None, "checkpoint_sha256": None}
    if tag == "bundle":
        from policy.export import open_bundle

        if len(parts) != 2:
            raise ValueError("--policy bundle takes exactly one path: --policy bundle data/checkpoints/<run>/bundle")
        adapter = open_bundle(parts[1])
        run = adapter.manifest.get("train_run") or {}
        return adapter, {
            "tag": "bundle",
            "policy": adapter.manifest.get("policy", "diffusion"),
            "checkpoint": adapter.manifest.get("checkpoint"),
            "checkpoint_sha256": adapter.manifest.get("weights_sha256"),
            "bundle": str(adapter.path),
            # DDIM steps for the Diffusion Policy; the ACT baseline has no such knob (5.8, D-019).
            "inference_steps": getattr(adapter.spec, "inference_steps", None),
            "train_run": run.get("run"),
            "dataset_manifest_sha256": run.get("dataset_manifest_sha256"),
        }
    raise ValueError(f"unknown policy {tag!r}; known: 'hold', 'bundle PATH'")


def _git_commit() -> str | None:
    """``git rev-parse HEAD``, or None outside a checkout. Every result names the code that made it."""
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


def _snapshot(summary) -> dict:
    """The controller's cumulative counters, so the next command's cost is a difference."""
    return {"commands": summary.commands, "elapsed_s": summary.elapsed_s, "actions_sent": summary.actions_sent,
            "policy_calls": summary.policy_calls, "refused": summary.refused,
            "stopped_by": Counter(summary.stopped_by)}


def _cost(before: dict, after: dict) -> dict:
    """What one command cost: the counter differences, and the single reason it stopped."""
    return {"duration_s": after["elapsed_s"] - before["elapsed_s"],
            "actions_sent": after["actions_sent"] - before["actions_sent"],
            "policy_calls": after["policy_calls"] - before["policy_calls"],
            "safety_refusals": after["refused"] - before["refused"],
            "stopped_by": next(iter(after["stopped_by"] - before["stopped_by"]), None)}


def run_trials(
    trials: Sequence[Trial],
    *,
    kind: str,
    backend: str = "mock",
    policy_spec: Sequence[str] = ("hold",),
    seed: int = 0,
    realtime: bool = False,
    config_root: Path | str | None = None,
    log_dir: Path | str | None = None,
    session: str | None = None,
) -> dict:
    """Execute every trial and return the result document of docs/eval.md.

    Raises :class:`NotImplementedError` for a backend or a policy that does not exist -- the same
    refusal ``runtime/controller.py`` makes, for the same reasons (R1, R2).
    """
    policy, policy_info = make_policy(policy_spec, backend)
    commands = [trial.command() for trial in trials]
    reissues = SEQUENCE_REISSUES if kind == "sequence" else 0
    engine = AttributedEngine(StubEngine(seed, script=commands, max_reissues=reissues), commands)
    fake = None if (realtime or backend != "mock") else FakeClock()
    passthrough: dict[str, Any] = {} if config_root is None else {"config_root": config_root}
    if fake is not None:
        passthrough["now_ns"] = fake

    controller = Controller(
        arm=make("arm", backend, **passthrough),
        hand=make("hand", backend, **passthrough),
        cameras={name: make(name, backend, **passthrough) for name in ("top", "oblique")},
        engine=engine,
        policy=policy,
        perception=MockPerception(),
        now_ns=clock.now_ns if fake is None else fake,
        session=session,
        log_dir=log_dir,
        config_root=config_root,
        **({} if fake is None else {"sleep_until": fake.jump_to}),
    )

    per_trial: list[dict] = [protocol.blank_row(trial) for trial in trials]
    before = _snapshot(controller.summary)
    while True:
        summary = controller.run(max_commands=1)
        if summary.commands == before["commands"]:
            break
        after = _snapshot(summary)
        index, own, first = engine.executions[-1]
        outcome = engine.outcomes[-1]
        protocol.record_execution(per_trial[index], trials[index], outcome, own=own, first=first,
                                  cost=_cost(before, after))
        before = after

    return {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "config_hashes": {name: config.config_hash(name, config_root) for name in HASHED_CONFIGS},
        "policy": policy_info,
        "backend": backend,
        "realtime": realtime,
        "kind": kind,
        "n": len(trials),
        "seed": seed,
        "engine_recovery": reissues > 0,
        "pairs": sorted({trial.pair for trial in trials if trial.pair is not None}),
        "trials": per_trial,
        "summary": protocol.summarise(per_trial),
    }


def _pairs(values: Sequence[str] | None) -> list[tuple[str, str]]:
    """``--pairs track-0:track-5 ...``, or the ten pairs of the eval script."""
    if not values:
        return protocol.script_pairs()
    pairs = []
    for value in values:
        src, sep, dst = value.partition(":")
        if not sep or not src or not dst:
            raise ValueError(f"--pairs takes src:dst, got {value!r}")
        pairs.append((src, dst))
    return pairs


def main(argv: list[str] | None = None) -> int:
    """``python -m eval.run_eval --backend mock --kind move --n 20 --policy hold``."""
    parser = argparse.ArgumentParser(description="Run an eval trial set (CLAUDE.md section 6, R5).")
    parser.add_argument("--backend", default="mock", help="mock (default) or real; real refuses until Phase 1")
    parser.add_argument("--kind", default="move", choices=protocol.KINDS, help="trial set (default move)")
    parser.add_argument("--n", type=int, default=20, help="number of trials (default 20)")
    parser.add_argument("--policy", nargs="+", default=["hold"], metavar="SPEC",
                        help="hold (default), or 'bundle PATH' (an inference bundle from policy/export.py)")
    parser.add_argument("--seed", type=int, default=0, help="trial-set and engine seed (default 0)")
    parser.add_argument("--pairs", nargs="+", default=None, metavar="SRC:DST",
                        help="held-out cell pairs for --kind move (default: the pairs of eval_20_moves)")
    parser.add_argument("--tag", default=None, help="result file tag (default <kind>-<policy>)")
    parser.add_argument("--out-dir", default=None, help=f"result directory (default {RESULTS_DIR})")
    parser.add_argument("--realtime", action="store_true", help="run the mocks on the real clock, not a fake one")
    parser.add_argument("--session", default=None, help="session id, used for the heartbeat file name")
    parser.add_argument("--json-logs", action="store_true", help="JSON log lines instead of key=value")
    args = parser.parse_args(argv)

    from runtime.log import configure

    # WARNING, not INFO: one run line per command would bury the result the run exists to print.
    configure(level=logging.WARNING, json=args.json_logs)
    try:
        trials = protocol.make_trials(args.kind, args.n, _pairs(args.pairs), args.seed)
        result = run_trials(trials, kind=args.kind, backend=args.backend, policy_spec=args.policy,
                            seed=args.seed, realtime=args.realtime, session=args.session)
    except NotImplementedError as exc:
        print(f"cannot run this evaluation: {exc}")
        return 2
    except ValueError as exc:
        print(f"cannot build the trial set: {exc}")
        return 2
    tag = args.tag or f"{args.kind}-{result['policy']['tag']}"
    path = protocol.write_result(result, tag, RESULTS_DIR if args.out_dir is None else args.out_dir)
    protocol.print_result(result)
    print(f"written {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
