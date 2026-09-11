"""Forward kinematics of the 8 commanded joints to the workspace-box point (CLAUDE.md R3, T-011).

``runtime/safety.py`` checks the box on a point, not on joint angles, so it needs a map from the 8
commanded joints (arm then waist yaw, ``config/robot.yaml`` ``action_order``) to that point. This is
that map, and the default ``fk`` of ``Envelope.from_config``. The model is the vendored G1 29-DoF
MJCF ``third_party/unitree_g1_mjcf/g1_29dof.xml`` (T-012, checksums in its ``MANIFEST.txt``),
compiled once and cached; it is the model the arm IK solves on (D-006), so the box and the IK cannot
disagree about geometry. The floating base is pinned to the identity pose, so mujoco's positions come
out in the pelvis frame (``workspace_box_m.frame: g1_pelvis``), and the joints this project never
commands -- legs, right arm, waist roll and pitch -- are held at the model's ``qpos0``, zero for all
of them. Only kinematics is evaluated: no dynamics, no contacts, no gravity. The point is the body or
site ``workspace_box_m.point`` names, today ``left_wrist_yaw_link``; the offset on to the DexH15
fingertip is UNMEASURED until Phase 1, so the box is checked at the wrist (docs/safety.md).
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from runtime import config
from runtime.types import ARM_DOF, JOINT_DIM

__all__ = ["BASE_QPOS", "BOX_FRAME", "MJCF", "kinematics", "left_arm_fk"]

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
MJCF: Path = REPO_ROOT / "third_party" / "unitree_g1_mjcf" / "g1_29dof.xml"
#: Base qpos: origin, identity quaternion (w,x,y,z). BOX_FRAME is what config/safety.yaml calls it.
BASE_QPOS: tuple[float, ...] = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
BOX_FRAME = "g1_pelvis"

#: Reentrant: the public function takes it to build the cache and again to use the shared MjData.
_lock = threading.RLock()
_cache: _Kinematics | None = None


class _Kinematics:
    """The compiled model, one scratch ``MjData``, and the qpos addresses of the 8 commanded joints.

    Built once. The constructor re-measures every address against the model rather than trusting
    ``config/robot.yaml``, so a changed model raises here instead of misplacing a target in silence.
    """

    def __init__(self) -> None:
        import mujoco

        self.mj = mujoco
        self.model = mujoco.MjModel.from_xml_path(str(MJCF))
        self.data = mujoco.MjData(self.model)
        robot = config.load("robot")
        entries = list(robot["arm"]["joints"]) + list(robot["waist"]["joints"])[:1]
        if len(entries) != JOINT_DIM:
            raise config.ConfigError(f"config/robot.yaml lists {len(entries)} commanded joints, need {JOINT_DIM}")
        self.names = tuple(str(e["name"]) for e in entries)
        self.qadr = np.empty(JOINT_DIM, dtype=np.int64)
        for i, entry in enumerate(entries):
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, self.names[i])
            if jid == -1:
                raise config.ConfigError(f"{MJCF.name} has no joint named {self.names[i]!r} (config/robot.yaml)")
            self.qadr[i] = int(self.model.jnt_qposadr[jid])
            if int(entry["mjcf_qpos_index"]) != self.qadr[i]:
                raise config.ConfigError(
                    f"config/robot.yaml puts {self.names[i]} at qpos {entry['mjcf_qpos_index']}, "
                    f"{MJCF.name} at {self.qadr[i]}"
                )

        box = config.load("safety")["workspace_box_m"]
        if str(box["frame"]) != BOX_FRAME:
            raise config.ConfigError(
                f"config/safety.yaml workspace_box_m.frame is {box['frame']!r}; fk.py produces {BOX_FRAME!r}"
            )
        self.point = str(box["point"])
        self.body = int(mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, self.point))
        self.site = int(mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, self.point))
        if self.body == -1 and self.site == -1:
            raise config.ConfigError(f"workspace_box_m.point {self.point!r}: no such body or site in {MJCF.name}")
        # Uncommanded joints at their rest value, base pinned; rewritten into qpos on every call.
        self.rest = self.model.qpos0.copy()
        self.rest[0:7] = BASE_QPOS

    def position(self, joints: np.ndarray) -> np.ndarray:
        """Position of the configured point, metres, pelvis frame. Not reentrant: shared MjData."""
        self.data.qpos[:] = self.rest
        self.data.qpos[self.qadr] = joints
        self.mj.mj_kinematics(self.model, self.data)
        source = self.data.xpos[self.body] if self.body != -1 else self.data.site_xpos[self.site]
        return np.array(source, dtype=np.float64)


def kinematics() -> _Kinematics:
    """The cached model. The first call compiles the MJCF (~0.2 s); later calls are free."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _Kinematics()
        return _cache


def left_arm_fk(q7: np.ndarray, waist_yaw: float | None = None) -> np.ndarray:
    """Position of ``workspace_box_m.point`` in the pelvis frame, metres, as a float64 ``(3,)``.

    Two call shapes, one implementation: ``left_arm_fk(q7, waist_yaw)``, and ``left_arm_fk(joints8)``
    over ``action_order`` -- the :data:`runtime.safety.FkFn` signature the envelope calls.
    """
    q = np.asarray(q7, dtype=np.float64).reshape(-1)
    if waist_yaw is None:
        if q.shape != (JOINT_DIM,):
            raise ValueError(f"left_arm_fk(joints) wants {JOINT_DIM} values (arm then waist yaw), got {q.shape}")
        joints = q
    else:
        if q.shape != (ARM_DOF,):
            raise ValueError(f"left_arm_fk(q7, waist_yaw) wants {ARM_DOF} arm values, got {q.shape}")
        joints = np.concatenate([q, [float(waist_yaw)]])
    if not np.all(np.isfinite(joints)):
        raise ValueError(f"left_arm_fk got a non-finite joint value: {joints.tolist()}")
    with _lock:
        return kinematics().position(joints)
