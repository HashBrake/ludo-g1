"""Operator input to robot targets: Pico controller pose -> 8 joints, glove pinch -> one scalar.

This is the retargeting half of teleop (CLAUDE.md 5.1, D-006). Three maps live here and nothing else:

* :func:`pico_to_g1_base` -- the controller's 6-DoF pose, which ``pico_bridge`` reports in its own
  ``pico_native`` frame in metres with an xyzw quaternion (docs/sdks.md 7.1), expressed in the G1
  pelvis frame that ``config/safety.yaml``'s workspace box and :mod:`runtime.fk` work in. The
  transform is ``config/robot.yaml`` ``teleop.pico_to_pelvis`` and is an UNMEASURED identity until
  Phase 1 calibrates it.
* :class:`ArmIK` -- that pelvis-frame wrist pose to the 8 commanded joints (7 left-arm joints then
  waist yaw, ``config/robot.yaml`` ``action_order``), solved with `mink` over the vendored G1 MJCF.
  D-006 replaced the vendored Teleopit stack with this: no body skeleton, no GMR, no RL policy.
* :func:`pinch_from_glove` -- the PxCap Pro's thumb-to-index tip distance to the single pinch scalar
  of CLAUDE.md 5.4, normalised between the operator's own calibration bounds in ``config/hand.yaml``.

Two boundaries matter. **Nothing here sends anything.** A solved ``q8`` is a proposal; it becomes a
command only by going through :mod:`runtime.safety` and a driver (R1, R3). And the model is the same
``third_party/unitree_g1_mjcf/g1_29dof.xml`` that :mod:`runtime.fk` checks the box on, with the same
identity floating base and the same uncommanded joints held at ``qpos0``, so the IK and the envelope
cannot disagree about geometry. The IK additionally clamps the 8 commanded joints to the
``config/safety.yaml`` limits and the waist clamp, which is belt and braces, not a substitute for the
guard: the guard is what decides.

On the solver's timestep: ``teleop.ik.step_dt_s`` is a trust region on one solver iteration, not a
control period. The command-level velocity limit is enforced by :mod:`runtime.safety` against the
measured state and real elapsed time; a single cold :meth:`ArmIK.solve` can return a pose further
from ``q_current`` than one 30 Hz tick allows, and the caller is responsible for not stepping into
it (teleop engages from a clutch, so tracking starts with the target at the current wrist pose).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from runtime import config, fk
from runtime.types import JOINT_DIM

__all__ = ["ArmIK", "IkResult", "pico_to_g1_base", "pinch_from_glove"]


# --------------------------------------------------------------------------------------------------
# frame transform and the glove scalar
# --------------------------------------------------------------------------------------------------


def pico_to_g1_base(
    position_m: np.ndarray, quat_xyzw: np.ndarray, *, root: Path | str | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Express a ``pico_bridge`` controller pose in the G1 pelvis frame.

    ``position_m`` is metres and ``quat_xyzw`` is the xyzw quaternion pico_bridge reports
    (docs/sdks.md 7.1). Returns ``(position_m, quat_xyzw)`` in the pelvis frame, the frame
    ``config/safety.yaml`` ``workspace_box_m`` and :class:`ArmIK` targets are in. The transform is
    UNMEASURED (identity) until Phase 1, so today this is a units-and-order check, not a calibration.
    """
    import mujoco

    matrix = np.asarray(config.load("robot", root)["teleop"]["pico_to_pelvis"], dtype=np.float64)
    if matrix.shape != (4, 4) or not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0]):
        raise config.ConfigError(
            f"config/robot.yaml teleop.pico_to_pelvis must be a 4x4 [R t; 0 0 0 1], got shape {matrix.shape}"
        )
    position = np.asarray(position_m, dtype=np.float64).reshape(-1)
    quat = np.asarray(quat_xyzw, dtype=np.float64).reshape(-1)
    if position.shape != (3,) or quat.shape != (4,):
        raise ValueError(
            "pico_to_g1_base wants a (3,) position and a (4,) xyzw quaternion, "
            f"got {position.shape} and {quat.shape}"
        )
    norm = float(np.linalg.norm(quat))
    if not np.isfinite(norm) or norm < 1e-9:
        raise ValueError(f"pico_to_g1_base got a degenerate quaternion: {quat.tolist()}")

    rotation = np.empty(4)
    mujoco.mju_mat2Quat(rotation, np.ascontiguousarray(matrix[:3, :3]).reshape(9))
    out_quat = np.empty(4)
    mujoco.mju_mulQuat(out_quat, rotation, _xyzw_to_wxyz(quat / norm))
    return matrix[:3, :3] @ position + matrix[:3, 3], _wxyz_to_xyzw(out_quat)


def pinch_from_glove(distance_m: float, *, root: Path | str | None = None) -> float:
    """The pinch scalar of CLAUDE.md 5.4 from the glove's thumb-to-index tip distance, in metres.

    ``config/hand.yaml`` ``glove.open_distance_mm`` maps to 0 (hand open) and ``closed_distance_mm``
    to 1 (pinched), linearly, clamped to ``pinch.scalar_range``. Both bounds are UNMEASURED: they are
    captured per operator at the start of a session, so this is a stub of the mapping, not of the
    arithmetic.
    """
    hand = config.load("hand", root)
    glove = hand["glove"]
    open_m = float(glove["open_distance_mm"]) / 1000.0
    closed_m = float(glove["closed_distance_mm"]) / 1000.0
    if not np.isfinite([open_m, closed_m]).all() or abs(open_m - closed_m) < 1e-6:
        raise config.ConfigError(
            f"config/hand.yaml glove open/closed distances must differ, got {open_m} m and {closed_m} m"
        )
    distance = float(distance_m)
    if not np.isfinite(distance):
        raise ValueError(f"pinch_from_glove got a non-finite distance: {distance_m!r}")
    low, high = (float(x) for x in hand["pinch"]["scalar_range"])
    return float(np.clip((open_m - distance) / (open_m - closed_m), low, high))


def _xyzw_to_wxyz(quat: np.ndarray) -> np.ndarray:
    return np.array([quat[3], quat[0], quat[1], quat[2]], dtype=np.float64)


def _wxyz_to_xyzw(quat: np.ndarray) -> np.ndarray:
    return np.array([quat[1], quat[2], quat[3], quat[0]], dtype=np.float64)


# --------------------------------------------------------------------------------------------------
# the arm IK
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IkResult:
    """What one :meth:`ArmIK.solve_detailed` call did. ``joints`` is the 8-vector in ``action_order``."""

    joints: np.ndarray
    iterations: int
    position_error_m: float
    orientation_error_rad: float
    converged: bool
    #: Every intermediate configuration including the seed, only when ``record_steps`` was set.
    steps: tuple[np.ndarray, ...] = ()


class ArmIK:
    """8-DoF inverse kinematics for the left wrist: target pose in, ``action_order`` joints out.

    One instance owns one compiled model and one `mink` configuration and is **not** thread-safe or
    reentrant; build one per teleop loop. Every joint the project never commands -- legs, right arm,
    waist roll and pitch -- is frozen at the model's ``qpos0`` by a zero velocity limit, and the
    floating base is frozen at :data:`runtime.fk.BASE_QPOS`, so the solution is expressed in the
    pelvis frame exactly as :mod:`runtime.fk` is.
    """

    def __init__(self, *, root: Path | str | None = None) -> None:
        import mink
        import mujoco

        self._mink, self._mujoco = mink, mujoco
        robot, safety = config.load("robot", root), config.load("safety", root)
        self.settings = dict(robot["teleop"]["ik"])
        self.names = tuple(str(e["name"]) for e in list(robot["arm"]["joints"]) + list(robot["waist"]["joints"])[:1])
        self.rest = np.asarray(robot["teleop"]["rest_pose_rad"], dtype=np.float64).reshape(-1)
        if len(self.names) != JOINT_DIM or self.rest.shape != (JOINT_DIM,):
            raise config.ConfigError(
                f"config/robot.yaml must name {JOINT_DIM} commanded joints and a rest pose for each"
            )

        self.model = mujoco.MjModel.from_xml_path(str(fk.MJCF))
        self.qadr = np.array([self.model.jnt_qposadr[self.model.joint(n).id] for n in self.names], dtype=np.int64)
        self.lower, self.upper = self._limits(safety, self.names)
        for i, name in enumerate(self.names):
            # mink's ConfigurationLimit reads jnt_range, and this model instance is ours alone, so
            # the safety limits (R3) go in where the solver will actually see them. mj_kinematics
            # does not read jnt_range, so runtime/fk.py's geometry is untouched.
            self.model.jnt_range[self.model.joint(name).id] = (self.lower[i], self.upper[i])
        if np.any(self.rest < self.lower) or np.any(self.rest > self.upper):
            raise config.ConfigError(
                "config/robot.yaml teleop.rest_pose_rad is outside config/safety.yaml joint_limits_rad"
            )

        # The IK targets the point the box is checked on, so the two cannot address different frames.
        point = str(safety["workspace_box_m"]["point"])
        is_site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, point) != -1
        if not is_site and mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, point) == -1:
            raise config.ConfigError(
                f"config/safety.yaml workspace_box_m.point {point!r}: no such body or site in {fk.MJCF.name}"
            )
        self.point, self.frame_type = point, "site" if is_site else "body"

        self._data = mujoco.MjData(self.model)
        self._seed = self.model.qpos0.copy()
        self._seed[0:7] = fk.BASE_QPOS
        self._configuration = mink.Configuration(self.model)
        self._frame_task = mink.FrameTask(
            point,
            self.frame_type,
            position_cost=float(self.settings["position_cost"]),
            orientation_cost=float(self.settings["orientation_cost"]),
            lm_damping=float(self.settings["lm_damping"]),
        )
        posture = mink.PostureTask(self.model, cost=float(self.settings["posture_cost"]))
        posture.set_target(self._full_qpos(self.rest))
        self._tasks = [self._frame_task, posture]
        velocities = {
            name: (float(safety["joint_velocity_limit_rad_s"]) if name in self.names else 0.0)
            for j in range(self.model.njnt)
            if (name := mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_JOINT, j)) is not None
            and self.model.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE
        }
        self.velocity_limit_rad_s = float(safety["joint_velocity_limit_rad_s"])
        self._limits_list = [
            mink.ConfigurationLimit(self.model),
            mink.VelocityLimit(self.model, velocities),
            mink.FreeJointVelocityLimit(self.model, 0.0, 0.0),
        ]

    def __repr__(self) -> str:
        return f"ArmIK(point={self.point!r}, joints={len(self.names)}, max_iters={self.settings['max_iters']})"

    @staticmethod
    def _limits(safety: dict, names: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
        """The 8 joint limits of ``config/safety.yaml`` in ``names`` order, waist yaw clamped (R3)."""
        limits = safety["joint_limits_rad"]
        missing = [n for n in names if n not in limits]
        if missing:
            raise config.ConfigError(f"config/safety.yaml joint_limits_rad is missing {', '.join(missing)}")
        lower = np.array([float(limits[n][0]) for n in names])
        upper = np.array([float(limits[n][1]) for n in names])
        clamp = abs(float(safety["waist_yaw_clamp_rad"]))
        waist = names.index("waist_yaw_joint")
        lower[waist], upper[waist] = max(lower[waist], -clamp), min(upper[waist], clamp)
        return lower, upper

    @property
    def full_qpos(self) -> np.ndarray:
        """The model's whole 36-vector qpos as the last solve left it. Frozen dofs must not move."""
        return self._configuration.q.copy()

    def _full_qpos(self, joints: np.ndarray) -> np.ndarray:
        """The model's 36-vector qpos with the 8 commanded joints set and everything else at rest."""
        qpos = self._seed.copy()
        qpos[self.qadr] = joints
        return qpos

    def fk_pose(self, joints: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pose of the target point in the pelvis frame: ``(position_m, quat_xyzw)``.

        The position agrees with :func:`runtime.fk.left_arm_fk` by construction (same model, same
        base, same uncommanded joints); this adds the orientation, which the box does not need and
        the IK does.
        """
        q = np.asarray(joints, dtype=np.float64).reshape(-1)
        if q.shape != (JOINT_DIM,):
            raise ValueError(f"fk_pose wants {JOINT_DIM} joint values in action_order, got {q.shape}")
        self._data.qpos[:] = self._full_qpos(q)
        self._mujoco.mj_kinematics(self.model, self._data)
        if self.frame_type == "site":
            sid = self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_SITE, self.point)
            quat = np.empty(4)
            self._mujoco.mju_mat2Quat(quat, np.ascontiguousarray(self._data.site_xmat[sid]))
            return np.array(self._data.site_xpos[sid]), _wxyz_to_xyzw(quat)
        bid = self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_BODY, self.point)
        return np.array(self._data.xpos[bid]), _wxyz_to_xyzw(np.array(self._data.xquat[bid]))

    def solve(self, target_pos_m: np.ndarray, target_quat_xyzw: np.ndarray, q_current: np.ndarray) -> np.ndarray:
        """The 8 joint targets in ``action_order`` that put the target point at the given pose.

        Iterates at most ``teleop.ik.max_iters`` times from ``q_current``; each iteration obeys the
        ``config/safety.yaml`` joint limits, waist clamp and velocity limit over ``step_dt_s``. The
        result is always inside the joint limits, and is the best pose found if the target is out of
        reach -- :attr:`IkResult.converged` of :meth:`solve_detailed` is what says which happened.
        """
        return self.solve_detailed(target_pos_m, target_quat_xyzw, q_current).joints

    def solve_detailed(
        self,
        target_pos_m: np.ndarray,
        target_quat_xyzw: np.ndarray,
        q_current: np.ndarray,
        *,
        record_steps: bool = False,
    ) -> IkResult:
        """:meth:`solve` with the iteration count, the residual errors and optionally every step."""
        position = np.asarray(target_pos_m, dtype=np.float64).reshape(-1)
        quat = np.asarray(target_quat_xyzw, dtype=np.float64).reshape(-1)
        start = np.asarray(q_current, dtype=np.float64).reshape(-1)
        if position.shape != (3,) or quat.shape != (4,) or start.shape != (JOINT_DIM,):
            raise ValueError(
                f"solve wants a (3,) position, a (4,) xyzw quaternion and {JOINT_DIM} current joints, "
                f"got {position.shape}, {quat.shape} and {start.shape}"
            )
        if not (np.all(np.isfinite(position)) and np.all(np.isfinite(quat)) and np.all(np.isfinite(start))):
            raise ValueError("solve got a non-finite target or seed")
        norm = float(np.linalg.norm(quat))
        if norm < 1e-9:
            raise ValueError(f"solve got a degenerate target quaternion: {quat.tolist()}")

        mink = self._mink
        self._frame_task.set_target(
            mink.SE3.from_rotation_and_translation(mink.SO3(_xyzw_to_wxyz(quat / norm)), position)
        )
        self._configuration.update(self._full_qpos(np.clip(start, self.lower, self.upper)))
        dt = float(self.settings["step_dt_s"])
        pos_tol = float(self.settings["position_tolerance_m"])
        rot_tol = float(self.settings["orientation_tolerance_rad"])
        steps: list[np.ndarray] = [self._configuration.q[self.qadr].copy()] if record_steps else []

        iterations, budget = 0, int(self.settings["max_iters"])
        while True:
            error = self._frame_task.compute_error(self._configuration)
            pos_err, rot_err = float(np.linalg.norm(error[:3])), float(np.linalg.norm(error[3:]))
            converged = pos_err <= pos_tol and rot_err <= rot_tol
            if converged or iterations >= budget:
                break
            velocity = mink.solve_ik(
                self._configuration, self._tasks, dt, str(self.settings["solver"]), limits=self._limits_list
            )
            self._configuration.integrate_inplace(velocity, dt)
            iterations += 1
            if record_steps:
                steps.append(self._configuration.q[self.qadr].copy())
        return IkResult(
            joints=np.clip(self._configuration.q[self.qadr].copy(), self.lower, self.upper),
            iterations=iterations,
            position_error_m=pos_err,
            orientation_error_rad=rot_err,
            converged=converged,
            steps=tuple(steps),
        )
