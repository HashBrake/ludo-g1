"""Tests for runtime/config.py and for the six config files themselves (T-003).

Two kinds of test live here: mechanism tests for the loader (schema, hash, UNMEASURED tags), driven
by synthetic files in tmp_path; and content tests that pin the facts the real config files encode,
so that a later edit cannot quietly move a joint index, drop a cell or loosen a safety limit.
"""

from __future__ import annotations

import math

import pytest
import yaml

from runtime import config
from runtime.config import ConfigError

# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------


def _write(root, name: str, data: dict, sort_keys: bool = False) -> None:
    (root / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=sort_keys), encoding="utf-8")


def _minimal(name: str) -> dict:
    """A synthetic config satisfying exactly the required keys of ``name`` and nothing else."""
    data: dict = {}
    for dotted in config.REQUIRED_KEYS[name]:
        node = data
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = 1
    return data


# --------------------------------------------------------------------------------------------
# acceptance 1: all six files load
# --------------------------------------------------------------------------------------------


def test_names_are_the_six_config_files():
    assert config.NAMES == ("board", "cameras", "hand", "robot", "safety", "training")


@pytest.mark.parametrize("name", config.NAMES)
def test_every_config_loads(name):
    data = config.load(name)
    assert isinstance(data, dict) and data


@pytest.mark.parametrize("name", config.NAMES)
def test_every_config_file_exists_on_disk(name):
    assert (config.CONFIG_DIR / f"{name}.yaml").is_file()


def test_unknown_config_name_is_rejected():
    with pytest.raises(ConfigError, match="unknown config 'nope'"):
        config.load("nope")


# --------------------------------------------------------------------------------------------
# acceptance 2: config_hash is deterministic, order-independent, and value-sensitive
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", config.NAMES)
def test_config_hash_is_deterministic_across_two_loads(name):
    first = config.config_hash(name)
    second = config.config_hash(name)
    assert first == second
    assert len(first) == 64 and all(c in "0123456789abcdef" for c in first)


def test_config_hash_is_stable_across_key_order(tmp_path):
    data = config.load("training")
    _write(tmp_path, "training", data, sort_keys=False)
    a = config.config_hash("training", root=tmp_path)
    _write(tmp_path, "training", data, sort_keys=True)
    b = config.config_hash("training", root=tmp_path)
    reversed_top = {k: data[k] for k in reversed(list(data))}
    _write(tmp_path, "training", reversed_top, sort_keys=False)
    c = config.config_hash("training", root=tmp_path)
    assert a == b == c


def test_config_hash_changes_when_a_value_changes(tmp_path):
    data = config.load("training")
    _write(tmp_path, "training", data)
    before = config.config_hash("training", root=tmp_path)
    data["diffusion"]["chunk"] = data["diffusion"]["chunk"] + 1
    _write(tmp_path, "training", data)
    after = config.config_hash("training", root=tmp_path)
    assert before != after


def test_config_hash_ignores_comments_and_whitespace(tmp_path):
    src = (config.CONFIG_DIR / "training.yaml").read_text(encoding="utf-8")
    (tmp_path / "training.yaml").write_text(src, encoding="utf-8")
    stripped = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    (tmp_path / "training.yaml").write_text(stripped + "\n\n\n", encoding="utf-8")
    assert config.config_hash("training", root=tmp_path) == config.config_hash("training")


# --------------------------------------------------------------------------------------------
# acceptance 3: unmeasured()
# --------------------------------------------------------------------------------------------


def test_unmeasured_safety_is_non_empty():
    found = config.unmeasured("safety")
    assert found
    # Every number Fable has to review before a session is placed on the record.
    for key in (
        "workspace_box_m.min",
        "workspace_box_m.max",
        "joint_limits_rad",
        "waist_yaw_clamp_rad",
        "joint_velocity_limit_rad_s",
        "command_rate_limit_hz",
    ):
        assert key in found


def test_unmeasured_training_is_empty():
    assert config.unmeasured("training") == []


@pytest.mark.parametrize("name", ("robot", "cameras", "hand", "board"))
def test_the_other_configs_declare_placeholders(name):
    assert config.unmeasured(name)


def test_unmeasured_reports_both_tag_forms_and_dedupes(tmp_path):
    data = _minimal("training")
    data["dataset"]["root"] = "UNMEASURED"  # literal form
    data["rates"]["camera_hz"] = 30  # sibling-status form
    data["rates"]["camera_hz_status"] = "UNMEASURED"
    data["action"]["hz"] = "UNMEASURED"  # both forms at once
    data["action"]["hz_status"] = "UNMEASURED"
    data["act"]["chunk"] = 32
    data["act"]["chunk_status"] = "MEASURED"  # measured: not reported
    data["observation"]["task_ids"] = ["move", "UNMEASURED"]  # inside a list
    _write(tmp_path, "training", data)
    found = config.unmeasured("training", root=tmp_path)
    assert "dataset.root" in found
    assert "rates.camera_hz" in found and "rates.camera_hz_status" not in found
    assert found.count("action.hz") == 1
    assert "act.chunk" not in found
    assert "observation.task_ids[1]" in found


def test_unmeasured_is_deterministic(tmp_path):
    assert config.unmeasured("robot") == config.unmeasured("robot")


def test_status_key_must_annotate_an_existing_sibling(tmp_path):
    data = _minimal("training")
    data["ghost_status"] = "UNMEASURED"
    _write(tmp_path, "training", data)
    with pytest.raises(ConfigError, match=r"annotates 'ghost', which does not exist"):
        config.load("training", root=tmp_path)


def test_status_key_must_carry_a_known_status(tmp_path):
    data = _minimal("training")
    data["action"]["hz_status"] = "unmeasured"  # wrong case: a typo must not hide a placeholder
    _write(tmp_path, "training", data)
    with pytest.raises(ConfigError, match=r"has status 'unmeasured'"):
        config.load("training", root=tmp_path)


# --------------------------------------------------------------------------------------------
# acceptance 4: a missing required key raises a clear error naming file and key
# --------------------------------------------------------------------------------------------


def test_missing_required_key_names_file_and_key(tmp_path):
    data = config.load("safety")
    del data["workspace_box_m"]["max"]
    del data["workspace_box_m"]["max_status"]
    _write(tmp_path, "safety", data)
    with pytest.raises(ConfigError) as exc:
        config.load("safety", root=tmp_path)
    message = str(exc.value)
    assert "config/safety.yaml" in message
    assert "'workspace_box_m.max'" in message
    assert "missing required key" in message


def test_missing_file_names_the_file(tmp_path):
    with pytest.raises(ConfigError, match=r"config/robot\.yaml: cannot read"):
        config.load("robot", root=tmp_path)


def test_non_mapping_document_is_rejected(tmp_path):
    (tmp_path / "robot.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        config.load("robot", root=tmp_path)


def test_invalid_yaml_is_rejected(tmp_path):
    (tmp_path / "robot.yaml").write_text("a: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid yaml"):
        config.load("robot", root=tmp_path)


@pytest.mark.parametrize("name", config.NAMES)
def test_each_required_key_is_individually_enforced(name, tmp_path):
    """Dropping any single required key must fail the load, i.e. the schema is really checked."""
    for dotted in config.REQUIRED_KEYS[name]:
        data = config.load(name)
        node = data
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node[part]
        del node[parts[-1]]
        node.pop(parts[-1] + config.STATUS_SUFFIX, None)
        _write(tmp_path, name, data)
        with pytest.raises(ConfigError, match="missing required key"):
            config.load(name, root=tmp_path)


# --------------------------------------------------------------------------------------------
# content: robot.yaml encodes the joint facts from docs/sdks.md (T-002)
# --------------------------------------------------------------------------------------------


def test_robot_joint_indices_match_the_unitree_order():
    robot = config.load("robot")
    arm = {j["name"]: j["index"] for j in robot["arm"]["joints"]}
    assert arm == {
        "left_shoulder_pitch_joint": 15,
        "left_shoulder_roll_joint": 16,
        "left_shoulder_yaw_joint": 17,
        "left_elbow_joint": 18,
        "left_wrist_roll_joint": 19,
        "left_wrist_pitch_joint": 20,
        "left_wrist_yaw_joint": 21,
    }
    assert robot["arm"]["dof"] == len(arm) == 7
    assert robot["arm"]["side"] == "left"
    waist = {j["name"]: j["index"] for j in robot["waist"]["joints"]}
    assert waist == {"waist_yaw_joint": 12}


def test_robot_action_order_is_the_nine_dimensional_action_of_5_3():
    robot = config.load("robot")
    names = [j["name"] for j in robot["arm"]["joints"]] + [j["name"] for j in robot["waist"]["joints"]]
    assert robot["action_order"] == names + ["pinch_scalar"]
    assert len(robot["action_order"]) == config.load("training")["action"]["dim"] == 9


def test_robot_write_path_is_arm_sdk_only():
    """D-007: rt/lowcmd owns the legs and must never appear as this project's command topic."""
    robot = config.load("robot")
    assert robot["topics"]["command"] == "rt/arm_sdk"
    assert robot["topics"]["state"] == "rt/lowstate"
    assert robot["topics"]["arm_sdk_weight_index"] == 29


def test_robot_joint_limits_are_finite_and_ordered():
    robot = config.load("robot")
    for joint in robot["arm"]["joints"] + robot["waist"]["joints"]:
        lo, hi = joint["limit_rad"]
        assert math.isfinite(lo) and math.isfinite(hi)
        assert lo < 0 < hi, joint["name"]


def test_robot_latencies_are_placeholders_not_claims():
    """Nothing may treat a latency as measured until Phase 1 fills it in."""
    robot = config.load("robot")
    unmeasured = config.unmeasured("robot")
    for key, value in robot["latency"].items():
        if key.endswith(config.STATUS_SUFFIX) or key == "method":
            continue
        assert value == 0.0
        assert f"latency.{key}" in unmeasured


# --------------------------------------------------------------------------------------------
# content: safety.yaml (R3)
# --------------------------------------------------------------------------------------------

# Degrees the safety limits are tightened by on each side relative to the MJCF range.
TIGHTEN_RAD = math.radians(5.0)


def test_safety_joint_limits_are_strictly_inside_the_mechanical_range():
    robot = config.load("robot")
    safety = config.load("safety")
    mech = {j["name"]: j["limit_rad"] for j in robot["arm"]["joints"] + robot["waist"]["joints"]}
    assert set(safety["joint_limits_rad"]) == set(mech)
    for name, (lo, hi) in safety["joint_limits_rad"].items():
        mlo, mhi = mech[name]
        assert lo > mlo and hi < mhi, name
        assert lo == pytest.approx(mlo + TIGHTEN_RAD, abs=1e-4), name
        assert hi == pytest.approx(mhi - TIGHTEN_RAD, abs=1e-4), name


def test_safety_waist_clamp_is_tighter_than_the_waist_joint_limit():
    safety = config.load("safety")
    clamp = safety["waist_yaw_clamp_rad"]
    lo, hi = safety["joint_limits_rad"]["waist_yaw_joint"]
    assert 0 < clamp <= min(abs(lo), hi)


def test_safety_workspace_box_is_a_real_box_in_front_of_the_robot():
    box = config.load("safety")["workspace_box_m"]
    lo, hi = box["min"], box["max"]
    assert len(lo) == len(hi) == 3
    assert all(a < b for a, b in zip(lo, hi, strict=True))
    # In front of the pelvis (+x forward), never behind it, and never above head height.
    assert lo[0] > 0.0
    assert hi[0] <= 0.8
    assert hi[2] <= 0.5
    # The left arm is the only arm used, so the box reaches to the robot's left (+y) far more than
    # to its right.
    assert hi[1] > 0.0 and lo[1] >= -0.2
    assert box["frame"] == "g1_pelvis"


def test_safety_session_defaults_match_the_brief():
    session = config.load("safety")["session"]
    assert session["default_seconds"] == 7200  # CLAUDE.md 4.6
    assert session["file"] == "hardware/session.enable"
    assert session["required_checklist_value"] == "confirmed"
    assert set(session["required_fields"]) == {"enabled_by", "enabled_at", "expires_at", "checklist"}
    assert session["max_seconds"] >= session["default_seconds"]


def test_safety_rate_and_velocity_limits_are_conservative():
    safety = config.load("safety")
    assert 0 < safety["joint_velocity_limit_rad_s"] <= 2.0
    assert 0 < safety["command_rate_limit_hz"] <= 200
    assert safety["command_rate_limit_hz"] >= config.load("robot")["control"]["command_hz"]
    assert safety["hand"]["pinch_scalar_range"] == [0.0, 1.0]


def test_safety_file_carries_the_r3_warning():
    text = (config.CONFIG_DIR / "safety.yaml").read_text(encoding="utf-8")
    assert "tighten or loosen only by human commit, r3" in text.lower()


# --------------------------------------------------------------------------------------------
# content: board.yaml (placeholder topology; the engine team owns the real one)
# --------------------------------------------------------------------------------------------


def _cells() -> dict[str, tuple[float, float]]:
    layout = config.load("board")["layout"]
    return {c["id"]: tuple(c["board_xy_mm"]) for c in layout["cells"]}


def test_board_cell_counts_and_uniqueness():
    board = config.load("board")
    layout = board["layout"]
    cells = layout["cells"]
    ids = [c["id"] for c in cells]
    assert len(ids) == len(set(ids)) == 48 + 4 * 4 + 4 * 6 == 88
    assert sum(1 for i in ids if i.startswith("track-")) == layout["track_length"] == 48
    for color in layout["colors"]:
        assert sum(1 for i in ids if i.startswith(f"{color}-base-")) == layout["base_size"] == 4
        assert sum(1 for i in ids if i.startswith(f"{color}-home-")) == layout["home_length"] == 6


def test_board_cells_fit_on_the_board():
    board = config.load("board")
    half_x, half_y = (v / 2.0 for v in board["size_mm"])
    pitch = board["layout"]["cell_pitch_mm"]
    for cell_id, (x, y) in _cells().items():
        assert abs(x) + pitch / 2 <= half_x, cell_id
        assert abs(y) + pitch / 2 <= half_y, cell_id


def test_board_track_is_a_closed_cycle_with_documented_step_lengths():
    cells = _cells()
    pitch = config.load("board")["layout"]["cell_pitch_mm"]
    allowed = {pitch, pitch * math.sqrt(2.0), pitch * 2.0}
    steps = []
    for i in range(48):
        x0, y0 = cells[f"track-{i}"]
        x1, y1 = cells[f"track-{(i + 1) % 48}"]
        steps.append(math.hypot(x1 - x0, y1 - y0))
    for i, step in enumerate(steps):
        assert any(step == pytest.approx(a, abs=1e-6) for a in allowed), (i, step)
    # 40 lane steps, 4 diagonals at the outer corners, 4 double steps across the home-lane mouths.
    assert sum(1 for s in steps if s == pytest.approx(pitch, abs=1e-6)) == 40
    assert sum(1 for s in steps if s == pytest.approx(pitch * math.sqrt(2.0), abs=1e-6)) == 4
    assert sum(1 for s in steps if s == pytest.approx(pitch * 2.0, abs=1e-6)) == 4


def test_board_home_lanes_run_inward_from_the_tip():
    cells = _cells()
    for color in config.load("board")["layout"]["colors"]:
        lane = [cells[f"{color}-home-{i}"] for i in range(6)]
        distances = [math.hypot(x, y) for x, y in lane]
        assert distances == sorted(distances, reverse=True), color
        for a, b in zip(lane, lane[1:], strict=False):
            assert math.hypot(b[0] - a[0], b[1] - a[1]) == pytest.approx(40.0, abs=1e-6)


def test_board_starts_and_home_entries_are_track_cells_evenly_spaced():
    layout = config.load("board")["layout"]
    ids = {c["id"] for c in layout["cells"]}
    starts = layout["starts"]
    entries = layout["home_entries"]
    assert set(starts) == set(entries) == set(layout["colors"])
    assert set(starts.values()) <= ids and set(entries.values()) <= ids
    indices = sorted(int(v.split("-")[1]) for v in starts.values())
    assert indices == [0, 12, 24, 36]
    for color, entry in entries.items():
        # The home entry is the track cell immediately before the NEXT colour's start.
        start = int(starts[color].split("-")[1])
        assert int(entry.split("-")[1]) == (start - 1) % 48, color


def test_board_topology_is_marked_as_the_engine_team_s_to_replace():
    assert "layout" in config.unmeasured("board")
    text = (config.CONFIG_DIR / "board.yaml").read_text(encoding="utf-8")
    assert "ENGINE TEAM OWNS THE TRUE TOPOLOGY" in text


# --------------------------------------------------------------------------------------------
# content: hand.yaml and cameras.yaml
# --------------------------------------------------------------------------------------------


def test_hand_has_fifteen_joints_seven_motors_and_matching_limits():
    hand = config.load("hand")
    assert len(hand["joint_order"]) == len(set(hand["joint_order"])) == 15
    assert len(hand["motor_order"]) == len(set(hand["motor_order"])) == 7
    assert set(hand["joint_limits_rad"]) == set(hand["joint_order"])
    for name, (lo, hi) in hand["joint_limits_rad"].items():
        assert lo < hi, name


def test_hand_joint_order_is_declared_unverified():
    """docs/sdks.md 4.6: the SDK slot order is a hypothesis until Phase 1 checks it on hardware."""
    assert "joint_order" in config.unmeasured("hand")


def test_hand_transport_matches_the_sdk_example():
    device = config.load("hand")["device"]
    assert device["baud"] == 4000000
    assert device["slave_address"] == 0x78


def test_hand_pinch_synergy_poses_are_placeholders():
    unmeasured = config.unmeasured("hand")
    for key in ("pinch.open_pose", "pinch.closed_pose", "pinch.idle_curl_pose"):
        assert key in unmeasured
    assert config.load("hand")["pinch"]["scalar_range"] == [0.0, 1.0]


def test_camera_policy_resolutions_match_the_observation_space_of_5_3():
    cameras = config.load("cameras")
    images = config.load("training")["observation"]["images"]
    assert cameras["top"]["policy_resolution"] == images["top"] == [640, 480]
    assert cameras["oblique"]["policy_resolution"] == images["oblique"] == [640, 480]
    assert cameras["palm"]["policy_resolution"] == images["palm"] == [320, 240]


def test_camera_devices_are_all_unresolved():
    """No camera node was ever opened (T-002), so no device selector may look like a real one."""
    unmeasured = config.unmeasured("cameras")
    for stream in ("top", "oblique", "palm"):
        assert f"{stream}.device" in unmeasured


# --------------------------------------------------------------------------------------------
# content: training.yaml matches CLAUDE.md 5.2 / 5.3 / 5.7
# --------------------------------------------------------------------------------------------


def test_training_rates_match_section_5_2():
    rates = config.load("training")["rates"]
    assert rates["camera_hz"] == 30
    assert rates["state_hz"] == 100
    assert rates["dataset_hz"] == 30
    assert rates["policy_hz"] == 10
    assert rates["action_hz"] == 30


def test_training_chunking_matches_section_5_7():
    training = config.load("training")
    assert training["diffusion"]["chunk"] == 16
    assert training["diffusion"]["execute"] == 8
    assert training["diffusion"]["inference_steps"] == 10
    assert training["act"]["chunk"] == 32
    assert training["act"]["temporal_ensemble"] is True
    assert training["diffusion"]["vision_encoder"] == training["act"]["vision_encoder"] == "resnet18"


def test_training_never_augments_the_top_frame_geometrically():
    """The goal heatmap lives in the `top` frame; a geometric augmentation would desynchronise it."""
    augmentation = config.load("training")["augmentation"]
    assert augmentation["geometric_on_top"] is False
    assert "top" not in augmentation["random_crop_cameras"]


def test_training_observation_matches_section_5_3():
    observation = config.load("training")["observation"]
    assert observation["state_dim"] == 9
    assert observation["goal_channels"] == 2
    assert observation["task_ids"] == ["move", "roll", "recover"]
    assert observation["extra_recorded"]["dexh15_joints"] == 15
