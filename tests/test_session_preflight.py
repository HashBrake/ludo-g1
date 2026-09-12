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
from tools.hardware_checks.session_preflight import ALL_STEPS, FAIL, NO_STATUS, PASS, SKIP, STEPS, Row

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


#: The envelope and gain keys a human approves before the first session (D-022), file by file.
APPROVED: dict[str, tuple[str, ...]] = {
    "safety": (
        "workspace_box_m.min", "workspace_box_m.max", "workspace_box_m.margin_m", "joint_limits_rad",
        "waist_yaw_clamp_rad", "joint_velocity_limit_rad_s", "first_command_max_step_rad", "watchdog_timeout_s",
    ),
    "robot": ("control.kp", "control.kd", "control.weight_ramp_s"),
}
#: What day 3 itself measures and so cannot require beforehand (D-022): kept UNMEASURED here.
DAY_THREE: dict[str, tuple[str, ...]] = {
    "robot": ("latency.arm_ms", "latency.hand_ms", "latency.glove_ms", "latency.pico_ms", "teleop.pico_to_pelvis"),
    "hand": ("pinch.open_pose", "pinch.closed_pose"),
}


def _set_status(data: dict, dotted: str, status: str) -> None:
    """Write ``<leaf>_status: status`` beside the leaf ``dotted`` names."""
    *parents, leaf = dotted.split(".")
    node = data
    for part in parents:
        node = node[part]
    node[f"{leaf}{config.STATUS_SUFFIX}"] = status


def approved_root(tmp_path: Path, checklist: list[str] | None = None) -> Path:
    """A config directory as it stands on the morning of day 3, under D-022.

    Everything days 1 and 2 measured read-only is MEASURED; the envelope and the arm gains are
    HUMAN_APPROVED placeholders a human committed; the latencies, the controller transform and the
    pinch poses -- what the motion day itself measures -- are still UNMEASURED.
    """
    root = measured_root(tmp_path, checklist=checklist)
    for name in ("safety", "robot", "hand"):
        path = root / f"{name}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key in APPROVED.get(name, ()):
            _set_status(data, key, config.HUMAN_APPROVED)
        for key in DAY_THREE.get(name, ()):
            _set_status(data, key, config.UNMEASURED)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


def row_named(rows: list[Row], check: str) -> Row:
    return next(row for row in rows if row.check == check)


# --------------------------------------------------------------------------------------------
# config rows
# --------------------------------------------------------------------------------------------


def test_every_motion_key_names_a_real_config_file_a_reason_and_the_steps_it_gates() -> None:
    for entry in pf.MOTION_KEYS:
        assert entry.name in config.NAMES, entry.key
        assert pf._lookup(config.load(entry.name), entry.key)[0], f"{entry.name}.{entry.key} does not exist"
        assert len(entry.why) > 10, f"{entry.name}.{entry.key} has no stated reason"
        assert entry.gates, f"{entry.name}.{entry.key} gates nothing"
        assert set(entry.gates) <= set(STEPS), entry.gates
        assert entry.gates_step(ALL_STEPS)


def test_only_the_envelope_and_the_gains_may_be_passed_by_a_human_approval() -> None:
    """D-022 allows HUMAN_APPROVED for the values the session itself measures, and nothing else."""
    approvable = {f"{entry.name}.{entry.key}" for entry in pf.MOTION_KEYS if entry.approved_ok}
    assert approvable == {
        "safety.workspace_box_m.min", "safety.workspace_box_m.max", "safety.workspace_box_m.margin_m",
        "safety.joint_limits_rad", "safety.waist_yaw_clamp_rad", "safety.joint_velocity_limit_rad_s",
        "safety.first_command_max_step_rad", "safety.watchdog_timeout_s",
        "robot.control.kp", "robot.control.kd", "robot.control.weight_ramp_s",
    }
    # ...and every one of them is in force whatever the run is doing.
    assert all(entry.gates == STEPS for entry in pf.MOTION_KEYS if entry.approved_ok)


def test_the_first_motion_step_is_not_gated_by_what_it_measures() -> None:
    """T-021 measures the latencies; D-022: they gate the later steps, never the first one."""
    gated = {f"{e.name}.{e.key}" for e in pf.MOTION_KEYS if e.gates_step("t021_latency")}
    assert "robot.latency.arm_ms" not in gated and "robot.teleop.pico_to_pelvis" not in gated
    assert "hand.pinch.open_pose" not in gated  # measured by t022_hand's synergy tool
    assert "robot.network.dds_interface" in gated  # measured read-only on day 1
    assert {"safety.joint_limits_rad", "robot.control.kp"} <= gated


def test_config_rows_fail_on_the_repo_today_and_are_all_motion_relevant() -> None:
    rows = pf.config_rows()
    assert len(rows) == len(pf.MOTION_KEYS)
    assert all(row.motion_relevant for row in rows)
    assert all(row.status == FAIL and row.detail.startswith("UNMEASURED:") for row in rows)
    assert all(row.key_status == "UNMEASURED" for row in rows)


def test_config_rows_pass_when_the_placeholders_are_gone(tmp_path: Path) -> None:
    rows = pf.config_rows(measured_root(tmp_path))
    assert [row.status for row in rows] == [PASS] * len(pf.MOTION_KEYS)
    assert row_named(rows, "config robot.network.dds_interface").detail.startswith("measured:")


def test_config_row_fails_when_the_key_is_gone(tmp_path: Path) -> None:
    root = measured_root(tmp_path, board="apriltags.centres_mm")
    row = row_named(pf.config_rows(root), "config board.apriltags.centres_mm")
    assert row.status == FAIL and "has no key" in row.detail


def test_the_step_the_key_gates_decides_whether_it_is_part_of_the_verdict(tmp_path: Path) -> None:
    """`--for` moves the star and the exit code, never the row: every key is still printed."""
    rows = pf.config_rows(measured_root(tmp_path), "t021_latency")
    assert len(rows) == len(pf.MOTION_KEYS)
    starred = {row.check for row in rows if row.motion_relevant}
    assert "config robot.latency.arm_ms" not in starred
    assert row_named(rows, "config robot.latency.arm_ms").detail.endswith("not t021_latency]")
    assert "config safety.joint_limits_rad" in starred
    assert {row.check for row in pf.config_rows(measured_root(tmp_path), ALL_STEPS) if row.motion_relevant} == {
        f"config {entry.name}.{entry.key}" for entry in pf.MOTION_KEYS
    }


def test_an_approved_envelope_is_a_go_for_the_first_step_and_a_no_go_for_recording(tmp_path: Path) -> None:
    """The acceptance case of T-045: D-022's morning-of-day-3 config."""
    root = approved_root(tmp_path)
    first = pf.config_rows(root, "t021_latency")
    assert pf.exit_code(first) == 0, [(r.check, r.detail) for r in first if r.motion_relevant and r.status != PASS]
    approved = row_named(first, "config safety.joint_limits_rad")
    assert (approved.status, approved.key_status) == (PASS, "HUMAN_APPROVED")
    assert "D-022" in approved.detail
    dds = row_named(first, "config robot.network.dds_interface")
    assert (dds.status, dds.key_status) == (PASS, NO_STATUS)  # form 1: the placeholder value is gone

    later = pf.config_rows(root, "phase2_recording")
    assert pf.exit_code(later) == 1
    failed = {row.check for row in later if row.motion_relevant and row.status == FAIL}
    assert failed == {
        "config robot.latency.arm_ms", "config robot.latency.hand_ms", "config robot.latency.glove_ms",
        "config robot.latency.pico_ms", "config robot.teleop.pico_to_pelvis",
        "config hand.pinch.open_pose", "config hand.pinch.closed_pose",
    }
    assert pf.exit_code(pf.config_rows(root, ALL_STEPS)) == 1


def test_an_unmeasured_envelope_still_fails_the_first_step(tmp_path: Path) -> None:
    """HUMAN_APPROVED is the only thing that passes an envelope key; UNMEASURED never does."""
    root = approved_root(tmp_path)
    data = yaml.safe_load((root / "safety.yaml").read_text(encoding="utf-8"))
    data["workspace_box_m"]["min_status"] = config.UNMEASURED
    (root / "safety.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    row = row_named(pf.config_rows(root, "t021_latency"), "config safety.workspace_box_m.min")
    assert (row.status, row.key_status, row.motion_relevant) == (FAIL, "UNMEASURED", True)
    assert row.detail.startswith("UNMEASURED:")


def test_a_human_approval_is_refused_on_a_key_that_is_measured_read_only(tmp_path: Path) -> None:
    """Nobody approves their way past the DDS interface: day 1 reads it off the machine."""
    root = approved_root(tmp_path)
    data = yaml.safe_load((root / "robot.yaml").read_text(encoding="utf-8"))
    data["network"]["dds_interface"] = "enp0s31f6"
    data["network"]["dds_interface_status"] = config.HUMAN_APPROVED
    (root / "robot.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    row = row_named(pf.config_rows(root, "t021_latency"), "config robot.network.dds_interface")
    assert (row.status, row.key_status) == (FAIL, "HUMAN_APPROVED")
    assert "approval is not enough" in row.detail
    assert pf.exit_code(pf.config_rows(root, "t021_latency")) == 1


def test_the_status_column_is_the_word_the_config_carries(tmp_path: Path) -> None:
    rows = pf.config_rows(measured_root(tmp_path))
    # MEASURED where a status key annotates the value, "-" where the placeholder value itself was
    # replaced (form 1 leaves nothing behind to annotate). Nothing is UNMEASURED in that directory.
    assert {row.key_status for row in rows} == {"MEASURED", NO_STATUS}
    assert pf.session_row(FakeGate(FakeStatus(True, "open"))).key_status == NO_STATUS
    assert pf.estop_row().key_status == NO_STATUS


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


def test_render_names_the_step_in_the_verdict_and_prints_the_status_column() -> None:
    rows = [Row("a", PASS, "fine", True, "HUMAN_APPROVED"), Row("b", FAIL, "broken", False)]
    step = pf.render(rows, "t021_latency")
    assert "1/1 checks that gate t021_latency pass" in step
    assert "GO: every check that gates t021_latency passes." in step
    assert "HUMAN_APPROVED" in step and "status" in step.splitlines()[0]
    assert "NO-GO for t024_envelope." in pf.render([Row("a", SKIP, "absent", True)], "t024_envelope")


def test_collect_takes_the_step_through_to_the_rows(tmp_path: Path) -> None:
    """A step-scoped run of the whole table: only the keys that gate it carry a star."""
    rows = pf.collect(
        config_root=approved_root(tmp_path, checklist=["e-stop: the mains switch behind the rig"]),
        gate=FakeGate(FakeStatus(False, "closed")), make_fn=make_fn_for(arm=FakeQueued(), hand=FakePolled()),
        budget=BUDGET, devices=("arm", "hand"), step="t021_latency",
    )
    assert pf.exit_code(rows) == 0, [(r.check, r.detail) for r in rows if r.motion_relevant and r.status != PASS]
    assert not row_named(rows, "config robot.latency.arm_ms").motion_relevant
    assert row_named(rows, "e-stop named").motion_relevant  # every step needs the e-stop (Q-004)
    assert row_named(rows, "device arm").motion_relevant


def test_approval_report_shows_the_values_and_the_exact_status_line() -> None:
    text = pf.approval_report()
    assert "D-022" in text and "R3" in text
    for key in ("workspace_box_m.min", "joint_limits_rad", "control.kp"):
        assert key in text
    assert "min_status: HUMAN_APPROVED" in text
    assert "kp_status: HUMAN_APPROVED" in text
    assert "left_shoulder_pitch_joint: [-3.0019, 2.5831]" in text  # the value, so a human can read it
    assert "\n..." not in text  # pyyaml's end-of-document marker never reaches the human


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
    assert {"check", "status", "detail", "motion_relevant", "key_status"} == set(rows[0])
    assert {row["check"] for row in rows} >= {"session gate", "e-stop named", "dataset disk", "git"}
    assert [row["check"] for row in rows if row["motion_relevant"]][0] == "e-stop named"


def test_cli_for_the_first_motion_step_is_no_go_on_this_repo_today() -> None:
    """T-045 acceptance: nothing is approved yet, so the e-stop and the envelope rows fail."""
    done = run_cli("--no-devices", "--for", "t021_latency")
    assert done.returncode == 1, done.stderr
    assert "NO-GO for t021_latency." in done.stdout
    assert "checks that gate t021_latency pass" in done.stdout
    for named in ("e-stop named", "config safety.workspace_box_m.min", "config robot.control.kp"):
        assert named in done.stdout.split("checks that gate")[1], f"{named} is not named in the verdict"
    # ...while the keys day 3 measures are printed, unstarred, and are not the reason it says NO-GO.
    body = done.stdout.split("* 0/")[0]
    assert "  config robot.latency.arm_ms" in body and "not t021_latency]" in body


def test_cli_rejects_an_unknown_step() -> None:
    assert run_cli("--no-devices", "--for", "t099_nope").returncode == 2


def test_cli_show_envelope_prints_the_approval_and_writes_nothing() -> None:
    before = (REPO / "config" / "safety.yaml").read_bytes()
    done = run_cli("--show-envelope")
    assert done.returncode == 0, done.stderr
    assert "HUMAN_APPROVED" in done.stdout and "D-022" in done.stdout
    assert (REPO / "config" / "safety.yaml").read_bytes() == before
    assert not (REPO / "hardware" / "session.enable").exists()


def test_cli_rejects_a_non_positive_budget() -> None:
    assert run_cli("--budget", "0").returncode == 2


@pytest.mark.readonly
def test_cli_with_devices_runs_read_only_on_whatever_is_plugged_in() -> None:
    done = run_cli("--budget", "0.2", "--json")
    assert done.returncode in (0, 1)
    devices = [row for row in json.loads(done.stdout) if row["check"].startswith("device ")]
    assert len(devices) == len(pf.DEVICES)
    assert all(row["status"] in (PASS, FAIL, SKIP) for row in devices)
