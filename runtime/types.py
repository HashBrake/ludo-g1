"""The two value types every motion path carries: :class:`MotionCommand` and :class:`RobotState`.

Both hold exactly the 9 numbers of CLAUDE.md 5.3 -- 7 left-arm joints, 1 waist yaw, 1 pinch scalar --
in the order given by ``config/robot.yaml`` ``action_order``. Nothing here knows about devices,
configuration or safety: :mod:`runtime.safety` is what decides whether a command may be sent, and
``drivers/`` is what sends it.

The arrays are float64 and are copied on construction, so a command handed to the safety guard cannot
be mutated behind its back by whoever built it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = [
    "ACTION_DIM",
    "ARM_DOF",
    "JOINT_DIM",
    "MotionCommand",
    "RobotState",
]

#: Left-arm joints (CLAUDE.md 5.3).
ARM_DOF = 7
#: Commanded joints: the arm plus waist yaw. This is what a joint-limit or velocity check runs over.
JOINT_DIM = ARM_DOF + 1
#: The full action vector: the joints plus the pinch scalar.
ACTION_DIM = JOINT_DIM + 1


def _as_arm(value: Any, what: str) -> np.ndarray:
    arr = np.array(value, dtype=np.float64).reshape(-1)
    if arr.shape != (ARM_DOF,):
        raise ValueError(f"{what}.arm must hold {ARM_DOF} joint values, got shape {np.shape(value)}")
    arr.flags.writeable = False
    return arr


@dataclass(frozen=True, eq=False, slots=True)
class MotionCommand:
    """One absolute target for the commanded degrees of freedom, at one instant.

    ``clamped`` names the fields that :meth:`runtime.safety.Envelope.check` had to pull back inside
    the envelope; it is empty on a freshly built command and is filled in on the copy the envelope
    returns. It is a report, never an input: setting it yourself changes nothing.
    """

    arm: np.ndarray
    waist_yaw: float
    pinch: float
    clamped: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        object.__setattr__(self, "arm", _as_arm(self.arm, "MotionCommand"))
        object.__setattr__(self, "waist_yaw", float(self.waist_yaw))
        object.__setattr__(self, "pinch", float(self.pinch))
        object.__setattr__(self, "clamped", tuple(self.clamped))

    def __repr__(self) -> str:
        arm = np.array2string(self.arm, precision=4, separator=", ")
        return (
            f"MotionCommand(arm={arm}, waist_yaw={self.waist_yaw:.4f}, "
            f"pinch={self.pinch:.3f}, clamped={self.clamped})"
        )

    @property
    def joints(self) -> np.ndarray:
        """The 8 commanded joint targets: the 7 arm joints then waist yaw."""
        return np.concatenate([self.arm, [self.waist_yaw]])

    def to_action(self) -> np.ndarray:
        """The 9-vector of ``config/robot.yaml`` ``action_order``: arm, waist yaw, pinch."""
        return np.concatenate([self.arm, [self.waist_yaw, self.pinch]])

    @classmethod
    def from_action(cls, action: Any, clamped: tuple[str, ...] = ()) -> MotionCommand:
        """Build a command from a 9-vector in ``action_order``."""
        arr = np.array(action, dtype=np.float64).reshape(-1)
        if arr.shape != (ACTION_DIM,):
            raise ValueError(f"action must hold {ACTION_DIM} values, got shape {np.shape(action)}")
        return cls(arm=arr[:ARM_DOF], waist_yaw=float(arr[ARM_DOF]), pinch=float(arr[ARM_DOF + 1]), clamped=clamped)

    def replace(
        self,
        *,
        arm: Any | None = None,
        waist_yaw: float | None = None,
        pinch: float | None = None,
        clamped: tuple[str, ...] | None = None,
    ) -> MotionCommand:
        """A copy with the given fields replaced."""
        return MotionCommand(
            arm=self.arm if arm is None else arm,
            waist_yaw=self.waist_yaw if waist_yaw is None else waist_yaw,
            pinch=self.pinch if pinch is None else pinch,
            clamped=self.clamped if clamped is None else clamped,
        )


@dataclass(frozen=True, eq=False, slots=True)
class RobotState:
    """Measured positions of the same degrees of freedom, stamped with :func:`runtime.clock.now_ns`.

    ``pinch`` is the scalar the hand driver reports back through the synergy of ``config/hand.yaml``,
    not a raw DexH15 joint. ``ts_ns`` is when the sample was obtained, which is what makes a
    state-referenced velocity check meaningful.
    """

    arm: np.ndarray
    waist_yaw: float
    pinch: float
    ts_ns: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "arm", _as_arm(self.arm, "RobotState"))
        object.__setattr__(self, "waist_yaw", float(self.waist_yaw))
        object.__setattr__(self, "pinch", float(self.pinch))
        object.__setattr__(self, "ts_ns", int(self.ts_ns))

    def __repr__(self) -> str:
        arm = np.array2string(self.arm, precision=4, separator=", ")
        return (
            f"RobotState(arm={arm}, waist_yaw={self.waist_yaw:.4f}, "
            f"pinch={self.pinch:.3f}, ts_ns={self.ts_ns})"
        )

    @property
    def joints(self) -> np.ndarray:
        """The 8 measured joint positions: the 7 arm joints then waist yaw."""
        return np.concatenate([self.arm, [self.waist_yaw]])

    def to_vector(self) -> np.ndarray:
        """The 9-vector of ``config/robot.yaml`` ``action_order``: arm, waist yaw, pinch."""
        return np.concatenate([self.arm, [self.waist_yaw, self.pinch]])
