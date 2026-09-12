"""Session pre-flight: the read-only go/no-go table (T-041; CLAUDE.md 4.6, R1, R2).

Nothing here touches hardware or a session file. Every device is a fake injected through
``device_rows(make_fn=...)``, every config is a temporary directory, and the one test that runs the
real thing runs it with ``--no-devices``, so this file is green on a laptop with nothing plugged in
and green on the rig. The point of the tool is its verdict, so both the PASS and the FAIL path of
every row are exercised, and the exit-code rule is exercised on hand-built rows where the right
answer is obvious.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from runtime import config
from runtime.clock import Stamped, now_ns
from tools.hardware_checks import session_preflight as pf
from tools.hardware_checks.session_preflight import FAIL, PASS, SKIP, Row

REPO = Path(__file__).resolve().parents[1]

#: Short enough that a fake device row costs milliseconds, long enough to hold many samples.
BUDGET = 0.05


# --------------------------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeProbe:
    connected: bool = True
    serial_number: str = "SN-1"


class FakeStatus:
    def __init__(self, valid: bool, reason: str) -> None:
        self.valid, self.reason = valid, reason


class FakeGate:
    def __init__(self, status: Any = None, error: Exception | None = None) -> None:
        self._status, self._error = status, error

    def status(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._status


class FakePolled:
    """A device answered by reading it, like a camera, the hand or the controller."""

    def __init__(self, probe: Any = None, fail_after: int | None = None) -> None:
        self.reads, self.closed, self._probe, self._fail_after = 0, False, probe, fail_after

    def read(self) -> Stamped[None]:
        if self._fail_after is not None and self.reads >= self._fail_after:
            from drivers.cameras import CameraUnavailable

            raise CameraUnavailable("the node went silent mid-stream")
        self.reads += 1
        return Stamped(now_ns(), None)

    def probe(self) -> Any:
        return FakeProbe() if self._probe is None else self._probe

    def close(self) -> None:
        self.closed = True


class FakeQueued:
    """A device that stamps in a callback and is drained, like the arm and the real glove."""

    def __init__(self, silent: bool = False) -> None:
        self.closed, self._silent = False, silent

    def poll(self) -> list[Stamped[None]]:
        return [] if self._silent else [Stamped(now_ns(), None)]

    def probe(self, window_s: float = 1.0) -> Any:
        return FakeProbe()

    def close(self) -> None:
        self.closed = True


def make_fn_for(**devices: Any):
    """A ``drivers.make`` stand-in: the named devices exist, every other one is absent."""

    def make(name: str, backend: str = "real", **kwargs: Any) -> Any:
        assert backend == "real"
        if name not in devices:
            from drivers.g1_arm import ArmUnavailable

            raise ArmUnavailable(f"{name} is not plugged in (H-002)")
        return devices[name]

    return make


def _measured(node: Any) -> Any:
    """Every placeholder in a loaded config replaced by a value, every status flipped to MEASURED."""
    if isinstance(node, dict):
        return {k: ("MEASURED" if k.endswith(config.STATUS_SUFFIX) else _measured(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_measured(v) for v in node]
    return 0.0 if node == config.UNMEASURED else node


def measured_root(tmp_path: Path, checklist: list[str] | None = None, **drop: str) -> Path:
    """A config directory whose six files carry no placeholder at all.

    ``checklist`` fills ``safety.session.checklist``; ``drop={"board": "apriltags.centres_mm"}``
    deletes one key so that the missing-key path can be exercised.
    """
    root = tmp_path / "config"
    root.mkdir(parents=True, exist_ok=True)
    for name in config.NAMES:
        data = _measured(yaml.safe_load((REPO / "config" / f"{name}.yaml").read_text(encoding="utf-8")))
        if name == "safety" and checklist is not None:
            data["session"]["checklist"] = checklist
        if name in drop:
            *parents, leaf = drop[name].split(".")
            node = data
            for part in parents:
                node = node[part]
            del node[leaf]
        (root / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


def row_named(rows: list[Row], check: str) -> Row:
    return next(row for row in rows if row.check == check)


# --------------------------------------------------------------------------------------------
# config rows
# --------------------------------------------------------------------------------------------


def test_every_motion_key_names_a_real_config_file_and_a_reason() -> None:
    for name, key, why in pf.MOTION_KEYS:
        assert name in config.NAMES, key
        assert pf._lookup(config.load(name), key)[0], f"{name}.{key} does not exist"
        assert len(why) > 10, f"{name}.{key} has no stated reason"


def test_config_rows_fail_on_the_repo_today_and_are_all_motion_relevant() -> None:
    rows = pf.config_rows()
    assert len(rows) == len(pf.MOTION_KEYS)
    assert all(row.motion_relevant for row in rows)
    assert all(row.status == FAIL and row.detail.startswith("UNMEASURED:") for row in rows)


def test_config_rows_pass_when_the_placeholders_are_gone(tmp_path: Path) -> None:
    rows = pf.config_rows(measured_root(tmp_path))
    assert [row.status for row in rows] == [PASS] * len(pf.MOTION_KEYS)
    assert row_named(rows, "config robot.network.dds_interface").detail.startswith("measured:")


def test_config_row_fails_when_the_key_is_gone(tmp_path: Path) -> None:
    root = measured_root(tmp_path, board="apriltags.centres_mm")
    row = row_named(pf.config_rows(root), "config board.apriltags.centres_mm")
    assert row.status == FAIL and "has no key" in row.detail


def test_config_rows_fail_with_the_loader_error_when_the_file_is_unusable(tmp_path: Path) -> None:
    root = measured_root(tmp_path, safety="workspace_box_m.max")
    rows = [row for row in pf.config_rows(root) if row.check.startswith("config safety.")]
    assert all(row.status == FAIL for row in rows)
    assert "missing required key" in rows[0].detail


# --------------------------------------------------------------------------------------------
# the e-stop row (Q-004) and the session gate row
# --------------------------------------------------------------------------------------------


def test_estop_row_fails_on_the_checklist_we_ship_today() -> None:
    row = pf.estop_row()
    assert row.status == FAIL and row.motion_relevant
    assert "e-stop within reach" in row.detail and "Q-004" in row.detail


def test_estop_row_passes_when_the_checklist_names_the_device() -> None:
    row = pf.estop_row(checklist=("e-stop: the Unitree remote damp chord (L2+B) is in my hand",))
    assert row.status == PASS and "damp" in row.detail


def test_estop_row_fails_when_no_item_mentions_an_estop() -> None:
    row = pf.estop_row(checklist=("legs locked", "workspace clear"))
    assert row.status == FAIL and "no checklist item mentions an e-stop" in row.detail


def test_estop_row_reads_the_checklist_out_of_safety_yaml(tmp_path: Path) -> None:
    root = measured_root(tmp_path, checklist=["emergency stop: mains switch behind the rig"])
    assert pf.estop_row(root).status == PASS
    stale = measured_root(tmp_path / "b", checklist=["e-stop within reach"])
    assert pf.estop_row(stale).status == FAIL


def test_session_row_reports_the_gate_and_is_never_motion_relevant() -> None:
    open_row = pf.session_row(FakeGate(FakeStatus(True, "session enabled by Alois, 600 s left")))
    assert (open_row.status, open_row.motion_relevant) == (PASS, False)
    assert "Alois" in open_row.detail
    closed = pf.session_row(FakeGate(FakeStatus(False, "no such file")))
    assert closed.status == SKIP and "no such file" in closed.detail
    broken = pf.session_row(FakeGate(error=RuntimeError("gate exploded")))
    assert broken.status == FAIL and "gate exploded" in broken.detail


def test_session_row_on_the_real_gate_is_closed_here() -> None:
    assert pf.session_row().status in (SKIP, PASS)  # PASS only inside a human-enabled session


# --------------------------------------------------------------------------------------------
# device rows
# --------------------------------------------------------------------------------------------


def test_device_row_passes_with_the_achieved_rate_and_closes_the_device() -> None:
    camera = FakePolled()
    rows = pf.device_rows(BUDGET, make_fn_for(top=camera), devices=("top",))
    assert rows[0].status == PASS and "Hz," in rows[0].detail and "SN-1" in rows[0].detail
    assert camera.closed


def test_device_row_skips_with_the_drivers_own_message_when_absent() -> None:
    rows = pf.device_rows(BUDGET, make_fn_for(), devices=("arm", "glove"))
    assert [row.status for row in rows] == [SKIP, SKIP]
    assert "H-002" in rows[0].detail and rows[0].detail.startswith("absent:")


def test_device_row_fails_when_the_device_is_open_but_silent() -> None:
    row = pf.device_rows(BUDGET, make_fn_for(arm=FakeQueued(silent=True)), devices=("arm",))[0]
    assert row.status == FAIL and "delivered 0 sample(s)" in row.detail


def test_device_row_fails_when_the_stream_stops_mid_measurement() -> None:
    row = pf.device_rows(BUDGET, make_fn_for(top=FakePolled(fail_after=0)), devices=("top",))[0]
    assert row.status == FAIL and "opened, then stopped" in row.detail


def test_device_row_fails_when_the_probe_reports_not_connected() -> None:
    hand = FakePolled(probe=FakeProbe(connected=False))
    row = pf.device_rows(BUDGET, make_fn_for(hand=hand), devices=("hand",))[0]
    assert row.status == FAIL and "connected=False" in row.detail


def test_only_the_arm_and_the_hand_are_motion_relevant_devices() -> None:
    rows = pf.device_rows(BUDGET, make_fn_for(), devices=pf.DEVICES)
    assert {row.check for row in rows if row.motion_relevant} == {"device arm", "device hand"}
    assert len(rows) == len(pf.DEVICES)


# --------------------------------------------------------------------------------------------
# calibration, disk, git
# --------------------------------------------------------------------------------------------


def write_calib(path: Path, board_hash: str) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "created_at": "2026-09-12T00:00:00+07:00",
                "image": "data/calib/board_empty.png",
                "image_size_px": [3840, 2160],
                "apriltags": {"family": "tag36h11", "size_mm": 40.0, "ids": {"a": 0}},
                "homography_board_mm_to_top_px": [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]],
                "reprojection_rms_px": 0.5,
                "reprojection_max_px": 0.9,
                "board_config_hash": board_hash,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_calibration_row_fails_when_absent_and_passes_when_fresh(tmp_path: Path) -> None:
    missing = pf.calibration_row(tmp_path / "nope.yaml")
    assert missing.status == FAIL and "H-001" in missing.detail and not missing.motion_relevant
    fresh = write_calib(tmp_path / "board_calib.yaml", config.config_hash("board"))
    row = pf.calibration_row(fresh)
    assert row.status == PASS and "rms 0.500 px" in row.detail


def test_calibration_row_fails_when_stale_or_unreadable(tmp_path: Path) -> None:
    stale = pf.calibration_row(write_calib(tmp_path / "stale.yaml", "0" * 64))
    assert stale.status == FAIL and stale.detail.startswith("stale:")
    (tmp_path / "junk.yaml").write_text("not: a calibration\n", encoding="utf-8")
    assert pf.calibration_row(tmp_path / "junk.yaml").status == FAIL


def test_git_row_ignores_an_inherited_git_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A git hook exports GIT_DIR and GIT_INDEX_FILE; the row must still read the repo it was given."""
    clean, other = make_repo(tmp_path / "clean"), make_repo(tmp_path / "other")
    (other / "a.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert pf.git_row(clean).status == PASS
    assert pf.git_row(other).status == FAIL


def test_disk_row_compares_free_space_against_the_target(tmp_path: Path) -> None:
    assert pf.disk_row(tmp_path, target_gb=0.0).status == PASS
    tight = pf.disk_row(tmp_path, target_gb=1e9)
    assert tight.status == FAIL and "Q-002" in tight.detail and not tight.motion_relevant


def make_repo(path: Path) -> Path:
    """A throwaway repo with one committed file, built without touching the user's git config."""
    path.mkdir(parents=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "a.txt"], ["commit", "-qm", "a"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, env=env, capture_output=True)
    return path


def test_git_row_reads_head_and_the_tree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    clean = pf.git_row(repo)
    assert clean.status == PASS and "tree clean" in clean.detail
    (repo / "a.txt").write_text("b\n", encoding="utf-8")
    dirty = pf.git_row(repo)
    assert dirty.status == FAIL and "uncommitted path(s)" in dirty.detail
    assert pf.git_row(tmp_path / "not-a-repo").status == FAIL


# --------------------------------------------------------------------------------------------
# the exit code, the table and the CLI
# --------------------------------------------------------------------------------------------


def test_exit_code_counts_only_motion_relevant_rows() -> None:
    passing = [Row("a", PASS, "", True), Row("b", FAIL, "", False), Row("c", SKIP, "", False)]
    assert pf.exit_code(passing) == 0
    assert pf.exit_code([*passing, Row("d", FAIL, "", True)]) == 1
    assert pf.exit_code([*passing, Row("d", SKIP, "", True)]) == 1
    assert pf.exit_code([]) == 0


def test_collect_exits_zero_only_when_the_motion_rows_all_pass(tmp_path: Path) -> None:
    root = measured_root(tmp_path, checklist=["e-stop: the mains switch behind the rig"])
    fakes = make_fn_for(arm=FakeQueued(), hand=FakePolled())
    rows = pf.collect(
        config_root=root, gate=FakeGate(FakeStatus(False, "closed")), make_fn=fakes,
        budget=BUDGET, devices=("arm", "hand"),
    )
    assert pf.exit_code(rows) == 0, [(r.check, r.status, r.detail) for r in rows if r.motion_relevant]
    assert any(row.status == FAIL and not row.motion_relevant for row in rows)  # disk/git/calib
    silent = pf.collect(
        config_root=root, gate=FakeGate(FakeStatus(False, "closed")),
        make_fn=make_fn_for(arm=FakeQueued(silent=True), hand=FakePolled()),
        budget=BUDGET, devices=("arm", "hand"),
    )
    assert pf.exit_code(silent) == 1


def test_render_marks_the_motion_rows_and_states_the_verdict() -> None:
    table = pf.render([Row("a", PASS, "fine", True), Row("b", FAIL, "broken", False)])
    assert "* a" in table and "  b" in table
    assert "1/1 motion-relevant checks pass" in table and "GO:" in table
    no_go = pf.render([Row("a", SKIP, "absent", True)])
    assert "NO-GO for a motion session." in no_go and "0/1" in no_go


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "tools/hardware_checks/session_preflight.py", *args],
        cwd=REPO, capture_output=True, text=True, timeout=300,
    )


def test_cli_prints_the_table_and_exits_1_on_this_repo_today() -> None:
    done = run_cli("--no-devices")
    assert done.returncode == 1, done.stderr
    assert "NO-GO for a motion session." in done.stdout
    assert "config safety.workspace_box_m.min" in done.stdout


def test_cli_json_is_the_same_rows_as_a_list() -> None:
    done = run_cli("--no-devices", "--json")
    assert done.returncode == 1
    rows = json.loads(done.stdout)
    assert isinstance(rows, list)
    assert {"check", "status", "detail", "motion_relevant"} == set(rows[0])
    assert {row["check"] for row in rows} >= {"session gate", "e-stop named", "dataset disk", "git"}
    assert [row["check"] for row in rows if row["motion_relevant"]][0] == "e-stop named"


def test_cli_rejects_a_non_positive_budget() -> None:
    assert run_cli("--budget", "0").returncode == 2


@pytest.mark.readonly
def test_cli_with_devices_runs_read_only_on_whatever_is_plugged_in() -> None:
    done = run_cli("--budget", "0.2", "--json")
    assert done.returncode in (0, 1)
    devices = [row for row in json.loads(done.stdout) if row["check"].startswith("device ")]
    assert len(devices) == len(pf.DEVICES)
    assert all(row["status"] in (PASS, FAIL, SKIP) for row in devices)
