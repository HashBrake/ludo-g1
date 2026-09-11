"""Eval protocol and runner tests (T-028; CLAUDE.md section 6, 6.5, R2, R5).

Nothing here touches hardware and nothing here is marked ``motion``: every run is mock drivers, the
stub engine and :class:`~runtime.policy_api.HoldPolicy`, which commands no motion at all. The headline
test is the one R5 exists for -- 20 mock MOVE trials produce a JSON with 20 rows, a **0/20** success
rate, and every one of those failures counted under a labelled CLAUDE.md 6.5 mode.

Runs use a copy of ``config/`` whose ``runtime.primitive_timeout_s`` is 1 s instead of 20 s
(:func:`fast_config`). That shortens the *fake* clock the loop runs on, not the loop: the same policy
calls, the same sends through the guard, the same timeout path, twenty times less of it. The full
20 s-timeout CLI run is the acceptance command in agents/BUILD_LOG.md, not a unit test.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from board.perception import NO_PROGRESS, FailureMode, MockPerception
from engine.cells import load_cells
from engine.interface import Command, Outcome, Primitive
from eval import protocol
from eval.protocol import CRITERIA, PERTURBATIONS, Trial, failure_key, judge, make_trials
from eval.run_eval import cross_check_log, main, make_policy, run_trials
from runtime import config
from runtime.run_report import read_trials
from runtime.safety import REPO_ROOT

#: What the controller's watchdog reports for a primitive it halted (CLAUDE.md 6.5, Phase 5).
STALLED = FailureMode.POLICY_STALLED.value

#: Ten held-out cell pairs, all ids from ``config/board.yaml``. Stands in for the held-out half of
#: the train/eval split that ``policy/dataset.py`` (T-027) produces from the recorded sessions.
HELD_OUT: list[tuple[str, str]] = [
    ("R-base-0", "track-12"), ("track-12", "track-17"), ("track-17", "track-22"),
    ("track-22", "track-24"), ("track-0", "track-5"), ("track-5", "track-9"),
    ("track-30", "track-35"), ("track-44", "track-47"), ("track-11", "R-home-0"),
    ("R-home-0", "R-home-3"),
]

#: The other half of that split: the pairs a policy would have been trained on. No trial may touch one.
TRAIN_PAIRS: list[tuple[str, str]] = [
    ("track-1", "track-6"), ("track-6", "track-10"), ("track-20", "track-25"),
    ("track-40", "track-43"), ("G-base-0", "track-0"),
]


@pytest.fixture(scope="module")
def fast_config(tmp_path_factory) -> Path:
    """``config/``, copied, with a 1 s primitive timeout. Same loop, one twentieth of the fake clock."""
    root = tmp_path_factory.mktemp("config")
    for src in (REPO_ROOT / "config").glob("*.yaml"):
        shutil.copy(src, root / src.name)
    training = yaml.safe_load((root / "training.yaml").read_text(encoding="utf-8"))
    training["runtime"]["primitive_timeout_s"] = 1.0
    (root / "training.yaml").write_text(yaml.safe_dump(training, sort_keys=False), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def move_result(fast_config, tmp_path_factory) -> dict:
    """The headline run: 20 mock MOVE trials on the held-out pairs, driven by HoldPolicy."""
    trials = make_trials("move", 20, HELD_OUT, seed=0)
    return run_trials(trials, kind="move", backend="mock", seed=0, config_root=fast_config,
                      log_dir=tmp_path_factory.mktemp("logs"), session="test-move")


# --------------------------------------------------------------------------------------------------
# trial sets: reproducible, held out, and shaped like CLAUDE.md Phase 3/4 asks
# --------------------------------------------------------------------------------------------------


def test_trial_sets_are_reproducible_by_seed() -> None:
    assert make_trials("move", 20, HELD_OUT, seed=7) == make_trials("move", 20, HELD_OUT, seed=7)
    assert make_trials("recover", 12, seed=3) == make_trials("recover", 12, seed=3)
    assert make_trials("move", 20, HELD_OUT, seed=7) != make_trials("move", 20, HELD_OUT, seed=8)


def test_move_trials_never_leave_the_held_out_pairs() -> None:
    """The eval may only score the policy on pairs it was held out on (CLAUDE.md Phase 3)."""
    pairs = {trial.pair for trial in make_trials("move", 20, HELD_OUT, seed=0)}
    assert pairs <= set(HELD_OUT)
    assert pairs.isdisjoint(TRAIN_PAIRS)


def test_move_trials_spread_evenly_over_the_pairs() -> None:
    """20 trials over 10 pairs is each pair twice, and never the same pair twice in a row."""
    trials = make_trials("move", 20, HELD_OUT, seed=0)
    counts = {pair: sum(t.pair == pair for t in trials) for pair in HELD_OUT}
    assert set(counts.values()) == {2}
    assert all(a.pair != b.pair for a, b in zip(trials, trials[1:], strict=False))
    assert [t.primitive for t in trials] == [Primitive.MOVE] * 20


def test_move_trials_reject_a_cell_the_board_does_not_have() -> None:
    with pytest.raises(ValueError, match="track-999"):
        make_trials("move", 4, [("track-0", "track-999")], seed=0)


def test_move_trials_require_held_out_pairs() -> None:
    with pytest.raises(ValueError, match="held_out_pairs"):
        make_trials("move", 4, None, seed=0)


def test_sequence_trials_are_the_eval_script() -> None:
    """kind='sequence' is engine/scripts/eval_20_moves.yaml, command for command."""
    trials = make_trials("sequence", 20, seed=0)
    script = yaml.safe_load((REPO_ROOT / "engine" / "scripts" / "eval_20_moves.yaml").read_text(encoding="utf-8"))
    assert [t.pair for t in trials] == [(c["src"], c["dst"]) for c in script["commands"]]
    assert len({t.pair for t in trials}) == 10
    assert protocol.script_pairs() == list(dict.fromkeys(t.pair for t in trials))


def test_sequence_refuses_a_different_trial_count() -> None:
    with pytest.raises(ValueError, match="20 commands"):
        make_trials("sequence", 19, seed=0)


def test_recover_trials_carry_a_65_perturbation() -> None:
    trials = make_trials("recover", 8, seed=1)
    cells = load_cells()
    assert all(t.primitive is Primitive.RECOVER and t.perturbed for t in trials)
    assert all(t.perturbation in PERTURBATIONS for t in trials)
    assert {t.perturbation for t in trials} == set(PERTURBATIONS)  # every perturbation is covered
    assert all(t.src is t.dst and t.src.id in cells for t in trials)


def test_roll_trials_address_no_cell() -> None:
    """A ROLL works over the bowl, which config/board.yaml does not describe (engine/stub.py)."""
    trials = make_trials("roll", 5, seed=0)
    assert all(t.primitive is Primitive.ROLL and t.src is None and t.dst is None for t in trials)


def test_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown trial kind"):
        make_trials("juggle", 5, seed=0)


# --------------------------------------------------------------------------------------------------
# success criteria and the 6.5 vocabulary
# --------------------------------------------------------------------------------------------------


def _move_trial() -> Trial:
    cells = load_cells()
    return Trial(0, Primitive.MOVE, cells["track-0"], cells["track-5"], "R0", seed=0)


def test_a_move_needs_the_horse_seen_on_dst() -> None:
    """R5: a success is evidence perception saw, not a claim the Outcome made."""
    trial = _move_trial()
    assert judge(trial, Outcome(True, {"R0": ["track-0", "track-5"]}, None))
    assert not judge(trial, Outcome(True, {}, None))                                # nothing observed
    assert not judge(trial, Outcome(True, {"R0": ["track-0", "track-6"]}, None))    # wrong cell
    assert not judge(trial, Outcome(True, {"R1": ["track-0", "track-5"]}, None))    # wrong horse
    assert not judge(trial, Outcome(False, {"R0": ["track-0", "track-5"]}, "horse_fell"))


def test_a_roll_needs_the_die_to_change_and_a_recover_needs_only_a_clean_outcome() -> None:
    roll = Trial(0, Primitive.ROLL, None, None, None, seed=0)
    assert judge(roll, Outcome(True, {"die": [None, 4]}, None))
    assert not judge(roll, Outcome(True, {"R0": ["track-0", "track-1"]}, None))
    cells = load_cells()
    recover = Trial(0, Primitive.RECOVER, cells["track-3"], cells["track-3"], "R2", seed=0, perturbed=True)
    assert judge(recover, Outcome(True, {}, None))
    assert not judge(recover, Outcome(True, {}, NO_PROGRESS))
    assert set(CRITERIA) == set(Primitive)


def test_every_65_failure_mode_has_one_spelling() -> None:
    """The enum is the vocabulary; perception produces it and eval counts it (one definition)."""
    assert {m.value for m in FailureMode} == {
        "horse_fell", "missed_cell", "grasp_failed", "wrong_horse", "die_out_of_bowl",
        # The watchdog's verdict and perception's are two modes, never one (T-037): the first says
        # the policy was stopped going nowhere, the second that nothing changed by the end.
        "die_grasp_failed", "policy_stalled", "timeout_no_progress",
    }
    for mode in FailureMode:
        assert failure_key(Outcome(False, {}, mode.value)) == mode.value
    assert failure_key(Outcome(False, {}, None)) == protocol.UNLABELLED
    assert failure_key(Outcome(False, {}, "wat")) == "wat"  # an unknown label is evidence, not noise
    assert FailureMode.parse("nope") is None


def test_perception_labels_come_from_the_enum() -> None:
    """board/perception.py behaviour is unchanged, and every string it emits is a 6.5 mode."""
    cells = load_cells()
    command = Command(Primitive.MOVE, cells["track-0"], cells["track-5"], "R0")
    before = {"horses": {"R0": "track-0"}, "die": None}
    verify = MockPerception().verify
    assert verify(command, before, before).failure_mode == FailureMode.TIMEOUT_NO_PROGRESS.value
    missed = verify(command, before, {"horses": {"R0": "track-4"}, "die": None})
    assert missed.failure_mode == FailureMode.MISSED_CELL.value
    roll = Command(Primitive.ROLL, None, None, None)
    bounced = verify(roll, before, {"horses": {"R0": "track-4"}, "die": None})
    assert bounced.failure_mode == FailureMode.DIE_OUT_OF_BOWL.value
    assert verify(command, before, {"horses": {"R0": "track-5"}, "die": None}).success


# --------------------------------------------------------------------------------------------------
# the runner: 20 mock MOVE trials, 0/20, every failure labelled
# --------------------------------------------------------------------------------------------------


def test_twenty_mock_move_trials_score_zero_out_of_twenty(move_result) -> None:
    """The R5 headline: HoldPolicy moves nothing, so the JSON says 0/20 and says why."""
    summary = move_result["summary"]
    assert len(move_result["trials"]) == 20
    assert (summary["success"], summary["n"], summary["rate"]) == (0, 20, 0.0)
    assert summary["by_failure_mode"] == {NO_PROGRESS: 20}
    assert sum(summary["by_failure_mode"].values()) == 20
    labelled = {m.value for m in FailureMode}
    assert all(row["failure_mode"] in labelled for row in move_result["trials"])
    assert all(row["success"] is False and row["reported_success"] is False for row in move_result["trials"])


def test_each_move_trial_is_one_attempt_that_went_through_the_guard(move_result) -> None:
    """Per-primitive eval counts one attempt per trial; engine recovery is for kind='sequence'."""
    assert move_result["engine_recovery"] is False
    assert move_result["summary"]["engine_retries"] == 0
    assert move_result["summary"]["safety_refusals"] == 0
    for row, trial in zip(move_result["trials"], make_trials("move", 20, HELD_OUT, seed=0), strict=True):
        assert (row["src"], row["dst"]) == trial.pair
        assert row["stopped_by"] == "timeout"
        assert row["duration_s"] == pytest.approx(1.0, abs=0.05)   # fast_config's timeout
        assert row["policy_calls"] > 0 and row["actions_sent"] > 0


def test_the_result_records_what_produced_it(move_result, fast_config) -> None:
    """5.6/R5: a number is never separable from the code and config it was measured under."""
    assert move_result["git_commit"] is None or len(move_result["git_commit"]) == 40
    assert move_result["config_hashes"] == {
        name: config.config_hash(name, fast_config) for name in ("safety", "robot", "board", "training")
    }
    assert move_result["policy"] == {"tag": "hold", "checkpoint": None, "checkpoint_sha256": None}
    assert (move_result["backend"], move_result["kind"], move_result["n"]) == ("mock", "move", 20)
    assert move_result["pairs"] == sorted(HELD_OUT)


def test_engine_retries_are_attributed_to_the_trial_that_caused_them(fast_config, tmp_path) -> None:
    """kind='sequence' runs with the engine's recovery on; the retries are counted, not trials."""
    trials = make_trials("sequence", 20, seed=0)
    result = run_trials(trials, kind="sequence", backend="mock", seed=0, config_root=fast_config,
                        log_dir=tmp_path, session="test-seq")
    assert result["engine_recovery"] is True
    assert len(result["trials"]) == 20
    # Each failed MOVE buys two RECOVERs it cannot pass (D-013), and they belong to their trial.
    assert all(row["engine_retries"] == 2 for row in result["trials"])
    assert result["summary"]["engine_retries"] == 40
    assert result["summary"]["by_failure_mode"] == {NO_PROGRESS: 20}


def test_a_run_can_be_repeated_from_its_own_json(fast_config, tmp_path) -> None:
    """Every trial row carries its seed, so the set it came from is reconstructible."""
    trials = make_trials("roll", 4, seed=5)
    result = run_trials(trials, kind="roll", backend="mock", seed=5, config_root=fast_config,
                        log_dir=tmp_path, session="test-roll")
    rows = result["trials"]
    assert [row["seed"] for row in rows] == [5] * 4
    repeated = make_trials("roll", result["n"], None, rows[0]["seed"])
    assert [t.to_dict() for t in repeated] == [
        {key: row[key] for key in t.to_dict()} for t, row in zip(repeated, rows, strict=True)
    ]


# --------------------------------------------------------------------------------------------------
# the watchdog's failures, and the controller log the result is checked against (T-037)
# --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stalling_config(tmp_path_factory) -> Path:
    """``config/`` with the watchdog (0.5 s) well inside the primitive timeout (1 s).

    The two coincide in the real config; here the stall is unambiguously what ends each primitive,
    which is what a run that must count ``policy_stalled`` needs.
    """
    root = tmp_path_factory.mktemp("stall-config")
    for src in (REPO_ROOT / "config").glob("*.yaml"):
        shutil.copy(src, root / src.name)
    training = yaml.safe_load((root / "training.yaml").read_text(encoding="utf-8"))
    training["runtime"]["primitive_timeout_s"] = 1.0
    training["runtime"]["watchdog_stall_s"] = 0.5
    training["runtime"]["watchdog_interval_s"] = 0.1
    (root / "training.yaml").write_text(yaml.safe_dump(training, sort_keys=False), encoding="utf-8")
    return root


def test_a_stalled_run_counts_policy_stalled_once_per_trial(stalling_config, tmp_path) -> None:
    """The watchdog's halt is a labelled 6.5 failure in the JSON, one per trial, and nothing else."""
    trials = make_trials("move", 5, HELD_OUT, seed=0)
    result = run_trials(trials, kind="move", backend="mock", seed=0, config_root=stalling_config,
                        log_dir=tmp_path, session="test-stall")

    assert result["summary"]["by_failure_mode"] == {STALLED: 5}
    assert [row["stopped_by"] for row in result["trials"]] == ["watchdog"] * 5
    # The 0.5 s stall, plus the one action slot the halting hold occupies before the next command.
    assert all(row["duration_s"] == pytest.approx(0.55, abs=0.05) for row in result["trials"])
    assert result["summary"]["safety_refusals"] == 0


def test_the_result_names_the_controller_log_and_agrees_with_it(stalling_config, tmp_path) -> None:
    """R5: the eval JSON and the loop's own per-trial log are the same run, and it is checked."""
    trials = make_trials("move", 5, HELD_OUT, seed=0)
    result = run_trials(trials, kind="move", backend="mock", seed=0, config_root=stalling_config,
                        log_dir=tmp_path, session="test-agree")

    log = result["controller_log"]
    assert log["agrees"] is True and log["records"] == 5
    assert log["by_failure_mode"] == result["summary"]["by_failure_mode"] == {STALLED: 5}
    assert Path(log["path"]) == tmp_path / "controller_test-agree.trials.jsonl"

    records = read_trials(log["path"])
    assert [r["failure_mode"] for r in records] == [STALLED] * 5
    assert all(r["watchdog"]["stalled"] is True for r in records)
    for row, record in zip(result["trials"], records, strict=True):
        assert (record["command"]["src"], record["command"]["dst"]) == (row["src"], row["dst"])
        assert record["actions_sent"] == row["actions_sent"]
        assert record["policy_calls"] == row["policy_calls"]


def test_the_cross_check_refuses_a_log_that_disagrees(tmp_path) -> None:
    """Two records of one run that differ is a bug in one of them, never a number to report."""
    path = tmp_path / "controller_x.trials.jsonl"
    record = {"index": 0, "success": False, "failure_mode": STALLED, "stopped_by": "watchdog",
              "observed_state_delta": {}}
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    controller = SimpleNamespace(trials=SimpleNamespace(path=path, records=[record]))
    row = {"success": False, "reported_success": False, "failure_mode": STALLED, "stopped_by": "watchdog"}

    assert cross_check_log([row], [(0, True, True)], controller) == {
        "path": str(path), "records": 1, "by_failure_mode": {STALLED: 1}, "agrees": True,
    }
    with pytest.raises(ValueError, match="disagree"):
        cross_check_log([{**row, "stopped_by": "timeout"}], [(0, True, True)], controller)
    with pytest.raises(ValueError, match="controller log says"):
        cross_check_log([{**row, "failure_mode": NO_PROGRESS}], [(0, True, True)], controller)
    with pytest.raises(ValueError, match="lines for"):
        cross_check_log([row], [(0, True, True), (0, False, False)], controller)


# --------------------------------------------------------------------------------------------------
# the CLI, and what it refuses (R1, R2)
# --------------------------------------------------------------------------------------------------


def test_cli_writes_the_json_and_prints_the_success_line(tmp_path, capsys) -> None:
    """The acceptance command's shape, at n=2 so the test pays for two primitives, not twenty."""
    code = main(["--backend", "mock", "--kind", "move", "--n", "2", "--policy", "hold",
                 "--out-dir", str(tmp_path), "--session", "test-cli"])
    out = capsys.readouterr().out
    assert code == 0
    assert "success 0/2 (0.0%)" in out
    # The real config: a HoldPolicy primitive reaches the 20 s stall and the 20 s timeout together,
    # and the watchdog's is the verdict reported (config/training.yaml runtime.watchdog_stall_s).
    assert "failure modes" in out and f"  {STALLED:<22} 2" in out
    written = list(tmp_path.glob("*_move-hold.json"))
    assert len(written) == 1
    result = json.loads(written[0].read_text(encoding="utf-8"))
    assert result["summary"]["n"] == len(result["trials"]) == 2
    assert result["pairs"] and set(map(tuple, result["pairs"])) <= set(protocol.script_pairs())


def test_cli_refuses_the_real_backend(tmp_path, capsys) -> None:
    """R1/R2: no actuated real driver exists and a placeholder policy is never deployed."""
    code = main(["--backend", "real", "--kind", "move", "--n", "2", "--out-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 2
    assert "cannot run this evaluation" in out and "backend='real'" in out and "R2" in out
    assert not list(tmp_path.glob("*.json"))
    with pytest.raises(NotImplementedError, match="R2"):
        make_policy(["hold"], "real")


def test_the_bundle_policy_takes_one_existing_bundle(tmp_path) -> None:
    """T-029 implemented `--policy bundle PATH`; loading a real one is tested in tests/test_diffusion.py."""
    with pytest.raises(FileNotFoundError, match="not an inference bundle"):
        make_policy(["bundle", str(tmp_path / "nothing")], "mock")
    with pytest.raises(ValueError, match="exactly one path"):
        make_policy(["bundle"], "mock")
    with pytest.raises(ValueError, match="unknown policy"):
        make_policy(["magic"], "mock")


def test_results_directory_is_tracked_and_its_contents_are_not() -> None:
    """eval/results/ exists in git via .gitkeep; a result is committed deliberately, never by a run."""
    assert (protocol.RESULTS_DIR / ".gitkeep").exists()
    assert protocol.RESULTS_DIR == REPO_ROOT / "eval" / "results"
