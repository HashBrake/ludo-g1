"""Forward kinematics of the 8 commanded joints to the workspace-box points (CLAUDE.md R3, T-011).

``runtime/safety.py`` checks the box on points, not on joint angles, so it needs a map from the 8
commanded joints (arm then waist yaw, ``config/robot.yaml`` ``action_order``) to those points. This
is that map, and the default ``fk`` of ``Envelope.from_config``. The model is the vendored G1 29-DoF
MJCF ``third_party/unitree_g1_mjcf/g1_29dof.xml`` (T-012, checksums in its ``MANIFEST.txt``),
compiled once and cached; it is the model the arm IK solves on (D-006), so the box and the IK cannot
disagree about geometry. The floating base is pinned to the identity pose, so mujoco's positions come
out in the pelvis frame (``workspace_box_m.frame: g1_pelvis``), and the joints this project never
commands -- legs, right arm, waist roll and pitch -- are held at the model's ``qpos0``, zero for all
of them. Only kinematics is evaluated: no dynamics, no contacts, no gravity.

Two points come out (T-043, D-010):

``workspace_box_m.point``
    The body or site that key names, today ``left_wrist_yaw_link``: the origin of the wrist frame,
    which is where the DexH15 bolts on. :func:`left_arm_fk` returns it and nothing about it changed.

:data:`PINCH_POINT`
    The DexH15 fingertip pinch point, that same origin plus ``config/robot.yaml``
    ``tool.pinch_offset_m`` **rotated into the pelvis frame by the wrist body's own orientation**.
    Because the offset is rotated and not merely added, this point moves when the wrist rolls,
    pitches or yaws -- which the wrist origin does not do for roll and yaw, so a wrist-only box left
    two of the eight commanded joints unconstrained (D-010). The offset itself is an UNMEASURED
    placeholder until T-022 measures it on the hand.

:func:`left_arm_points` returns both, by name, and is what ``Envelope.from_config`` injects.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from runtime import config
from runtime.types import ARM_DOF, JOINT_DIM

__all__ = ["BASE_QPOS", "BOX_FRAME", "MJCF", "PINCH_POINT", "kinematics", "left_arm_fk", "left_arm_points"]

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
MJCF: Path = REPO_ROOT / "third_party" / "unitree_g1_mjcf" / "g1_29dof.xml"
#: Base qpos: origin, identity quaternion (w,x,y,z). BOX_FRAME is what config/safety.yaml calls it.
BASE_QPOS: tuple[float, ...] = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
BOX_FRAME = "g1_pelvis"
#: Name of the second checked point (config/safety.yaml ``workspace_box_m.points``). It is not a body
#: or a site of the MJCF: it is the wrist frame offset by ``config/robot.yaml tool.pinch_offset_m``.
PINCH_POINT = "pinch_point"

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

        self.tool_offset = self._tool_offset(robot)
        #: The names :meth:`positions` produces, in the order they are checked.
        self.names_out: tuple[str, ...] = (self.point, PINCH_POINT)
        unknown = [str(p) for p in box.get("points", [self.point]) if str(p) not in self.names_out]
        if unknown:
            raise config.ConfigError(
                f"config/safety.yaml workspace_box_m.points names {unknown}, which runtime/fk.py does "
                f"not produce; it produces {list(self.names_out)}"
            )
        # Uncommanded joints at their rest value, base pinned; rewritten into qpos on every call.
        self.rest = self.model.qpos0.copy()
        self.rest[0:7] = BASE_QPOS

    @staticmethod
    def _tool_offset(robot: dict) -> np.ndarray:
        """``config/robot.yaml`` ``tool.pinch_offset_m`` as 3 finite metres in the wrist frame."""
        tool = robot.get("tool")
        if not isinstance(tool, dict) or "pinch_offset_m" not in tool:
            raise config.ConfigError(
                "config/robot.yaml has no tool.pinch_offset_m; runtime/fk.py needs it to place the "
                f"{PINCH_POINT} config/safety.yaml checks the workspace box at"
            )
        try:
            offset = np.asarray([float(v) for v in tool["pinch_offset_m"]], dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise config.ConfigError(f"config/robot.yaml tool.pinch_offset_m is not 3 numbers: {exc}") from exc
        if offset.shape != (3,) or not np.all(np.isfinite(offset)):
            raise config.ConfigError(
                f"config/robot.yaml tool.pinch_offset_m must hold 3 finite metres, got {tool['pinch_offset_m']!r}"
            )
        return offset

    def positions(self, joints: np.ndarray) -> dict[str, np.ndarray]:
        """Both checked points, metres, pelvis frame. Not reentrant: shared MjData."""
        self.data.qpos[:] = self.rest
        self.data.qpos[self.qadr] = joints
        self.mj.mj_kinematics(self.model, self.data)
        if self.body != -1:
            origin, rotation = self.data.xpos[self.body], self.data.xmat[self.body]
        else:
            origin, rotation = self.data.site_xpos[self.site], self.data.site_xmat[self.site]
        wrist = np.array(origin, dtype=np.float64)
        # xmat is the body's rotation into the pelvis frame, row-major; rotating the offset is what
        # makes the pinch point follow wrist roll, pitch and yaw (D-010).
        pinch = wrist + np.asarray(rotation, dtype=np.float64).reshape(3, 3) @ self.tool_offset
        return {self.point: wrist, PINCH_POINT: pinch}


def kinematics() -> _Kinematics:
    """The cached model. The first call compiles the MJCF (~0.2 s); later calls are free."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _Kinematics()
        return _cache


def _joints(q7: np.ndarray, waist_yaw: float | None, who: str) -> np.ndarray:
    """The 8 commanded joints from either call shape, checked. ``who`` names the caller in errors."""
    q = np.asarray(q7, dtype=np.float64).reshape(-1)
    if waist_yaw is None:
        if q.shape != (JOINT_DIM,):
            raise ValueError(f"{who}(joints) wants {JOINT_DIM} values (arm then waist yaw), got {q.shape}")
        joints = q
    else:
        if q.shape != (ARM_DOF,):
            raise ValueError(f"{who}(q7, waist_yaw) wants {ARM_DOF} arm values, got {q.shape}")
        joints = np.concatenate([q, [float(waist_yaw)]])
    if not np.all(np.isfinite(joints)):
        raise ValueError(f"{who} got a non-finite joint value: {joints.tolist()}")
    return joints


def left_arm_fk(q7: np.ndarray, waist_yaw: float | None = None) -> np.ndarray:
    """Position of ``workspace_box_m.point`` in the pelvis frame, metres, as a float64 ``(3,)``.

    The wrist point alone, unchanged by T-043: this is what ``teleop/retarget.py``'s IK is
    cross-checked against and what every caller that means "where is the wrist" wants. The envelope
    checks :func:`left_arm_points`.

    Two call shapes, one implementation: ``left_arm_fk(q7, waist_yaw)``, and ``left_arm_fk(joints8)``
    over ``action_order``.
    """
    with _lock:
        kin = kinematics()
        return kin.positions(_joints(q7, waist_yaw, "left_arm_fk"))[kin.point]


def left_arm_points(q7: np.ndarray, waist_yaw: float | None = None) -> dict[str, np.ndarray]:
    """Every point the workspace box is checked at, by name, in the pelvis frame, metres.

    ``{workspace_box_m.point: wrist origin, PINCH_POINT: fingertip pinch point}`` -- the second one
    being the first offset by ``config/robot.yaml`` ``tool.pinch_offset_m`` rotated by the wrist
    body's orientation, so it follows wrist roll, pitch and yaw (T-043, D-010). This is the
    :data:`runtime.safety.FkFn` the envelope injects by default; the same two call shapes as
    :func:`left_arm_fk`.
    """
    with _lock:
        return kinematics().positions(_joints(q7, waist_yaw, "left_arm_points"))
