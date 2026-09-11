"""Tests for the vendored G1 MJCF and the three dependencies of the arm IK path (T-012).

The arm IK path decided in agents/DECISIONS.md D-006 rests on three things that live outside this
repo's own code: the ``mujoco`` model of the G1, the ``mink`` solver, and ``pico_bridge`` as the
source of the left controller's 6-DoF pose. This module pins all three.

The qpos-address test is the one that matters at runtime. ``config/robot.yaml`` records, per joint,
the address that joint occupies in ``MjModel.qpos`` for this exact MJCF (``mjcf_qpos_index``). T-013
writes IK results into ``data.qpos`` at those addresses, so a silent change of the model -- a new
body, a different floating base, a reordered kinematic tree -- would corrupt every target without
raising anything. The test measures the addresses by loading the model and asserts the yaml agrees.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from pathlib import Path

import pytest

from runtime import config

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
MJCF_DIR: Path = REPO_ROOT / "third_party" / "unitree_g1_mjcf"
MJCF: Path = MJCF_DIR / "g1_29dof.xml"
MANIFEST: Path = MJCF_DIR / "MANIFEST.txt"

#: sha256sum-format line: 64 hex digits, two spaces, path. Comment lines start with '#'.
_SUM_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")


def _robot_joints() -> list[dict]:
    """The 8 joints LUDO-G1 commands, arm first then waist, as config/robot.yaml lists them."""
    robot = config.load("robot")
    return list(robot["arm"]["joints"]) + list(robot["waist"]["joints"])


def _manifest_entries() -> list[tuple[str, str]]:
    entries = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        match = _SUM_LINE.match(line)
        assert match, f"MANIFEST.txt line is not sha256sum format: {line!r}"
        entries.append((match.group(1), match.group(2)))
    return entries


@pytest.fixture(scope="module")
def model():
    """The compiled G1 model. Compiling it is the mesh test too: a missing STL raises here."""
    import mujoco

    return mujoco.MjModel.from_xml_path(str(MJCF))


# --------------------------------------------------------------------------------------------
# the vendored asset
# --------------------------------------------------------------------------------------------


def test_vendored_tree_holds_the_xml_its_license_and_nothing_unlisted():
    assert MJCF.is_file()
    assert (MJCF_DIR / "LICENSE").is_file()
    assert (MJCF_DIR / "README.md").is_file()
    on_disk = {
        str(p.relative_to(MJCF_DIR)) for p in MJCF_DIR.rglob("*") if p.is_file()
    } - {"MANIFEST.txt"}
    assert on_disk == {rel for _, rel in _manifest_entries()}


def test_manifest_checksums_match_the_files_on_disk():
    entries = _manifest_entries()
    assert len(entries) == 38, "3 top-level files + the 35 meshes g1_29dof.xml references"
    for want, rel in entries:
        got = hashlib.sha256((MJCF_DIR / rel).read_bytes()).hexdigest()
        assert got == want, f"{rel}: manifest says {want}, file hashes {got}"


def test_only_the_referenced_meshes_are_vendored_and_meshdir_resolves():
    xml = MJCF.read_text(encoding="utf-8")
    assert 'meshdir="meshes"' in xml, "the relative layout the vendored copy preserves"
    referenced = re.findall(r'<mesh[^>]*file="([^"]+)"', xml)
    assert len(referenced) == 35
    vendored = {str(p.relative_to(MJCF_DIR / "meshes")) for p in (MJCF_DIR / "meshes").rglob("*") if p.is_file()}
    assert vendored == set(referenced)
    for rel in referenced:
        assert (MJCF_DIR / "meshes" / rel).is_file()


def test_robot_yaml_limits_source_points_at_the_vendored_file():
    source = Path(config.load("robot")["limits_source"])
    assert not source.is_absolute(), "the vendored path is repo-relative, the reference tree was not"
    assert (REPO_ROOT / source).resolve() == MJCF.resolve()


# --------------------------------------------------------------------------------------------
# the model itself
# --------------------------------------------------------------------------------------------


def test_mujoco_loads_the_mjcf(model):
    assert model.njnt == 30, "29 actuated joints + the floating base"
    assert model.nq == 36, "29 hinges + a 7-qpos free joint"


def test_model_has_every_joint_config_names(model):
    import mujoco

    for joint in _robot_joints():
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint["name"])
        assert jid != -1, f"{joint['name']} is not a joint of {MJCF.name}"
        assert model.jnt_type[jid] == mujoco.mjtJoint.mjJNT_HINGE


def test_yaml_qpos_addresses_match_the_model(model):
    import mujoco

    for joint in _robot_joints():
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint["name"])
        measured = int(model.jnt_qposadr[jid])
        assert joint["mjcf_qpos_index"] == measured, (
            f"{joint['name']}: config/robot.yaml says qpos index {joint['mjcf_qpos_index']}, "
            f"the model says {measured}"
        )


def test_yaml_joint_ranges_match_the_model(model):
    import mujoco

    for joint in _robot_joints():
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint["name"])
        low, high = (float(v) for v in model.jnt_range[jid])
        assert joint["limit_rad"][0] == pytest.approx(low, rel=1e-5)
        assert joint["limit_rad"][1] == pytest.approx(high, rel=1e-5)


# --------------------------------------------------------------------------------------------
# the dependencies
# --------------------------------------------------------------------------------------------


def test_the_three_ik_dependencies_import():
    import mink
    import mujoco
    import pico_bridge

    assert mujoco.__version__.startswith("3.")
    assert hasattr(mink, "SO3"), "mink's lie-group types, used to build the wrist pose task (T-013)"
    assert hasattr(pico_bridge, "__file__")


def test_pico_bridge_controller_state_carries_a_pose():
    from pico_bridge.frames import ControllerState

    assert dataclasses.is_dataclass(ControllerState)
    assert "pose" in {f.name for f in dataclasses.fields(ControllerState)}
