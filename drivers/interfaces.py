"""The driver contract: five protocols and the three sample types they carry (CLAUDE.md 5.1).

Every device this project touches sits behind one of these protocols, and every implementation --
the mocks in ``drivers/mock/`` today, the real wrappers of Phase 1 -- satisfies the same one, so
``runtime/controller.py``, ``teleop/`` and ``eval/`` never learn which is which. The protocols are
the contract only: they hold no behaviour, no configuration and no device knowledge.

Two rules the protocols cannot express in types and that every implementation obeys:

* **Timestamps.** Every sample is a :class:`runtime.clock.Stamped`, stamped from
  :func:`runtime.clock.now_ns` (or from the injected clock of a mock) at the instant the sample was
  obtained. That is what makes ``runtime/clock.py`` able to align the streams (docs/clock.md).
* **R1 and R3.** Both write calls (:meth:`ArmDriver.send_targets`, :meth:`HandDriver.send_pinch`)
  pass their command through :meth:`runtime.safety.Guard.admit` and send only what it returns; a
  refused command raises :class:`runtime.safety.SafetyViolation` and nothing is sent. There is no
  other way to the actuators (docs/safety.md).

``read``/``grab``/``read_state`` never move anything and are allowed at any time, session or not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from runtime.clock import Stamped
from runtime.types import MotionCommand, RobotState

__all__ = [
    "ArmDriver",
    "CameraDriver",
    "GloveDriver",
    "GloveSample",
    "HandDriver",
    "HandState",
    "PoseDriver",
    "WristPose",
]


def _vector(value: object, what: str) -> np.ndarray:
    arr = np.array(value, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        raise ValueError(f"{what} must hold at least one value")
    arr.flags.writeable = False
    return arr


@dataclass(frozen=True, eq=False, slots=True)
class HandState:
    """Measured DexH15 pose: the raw joint angles plus the pinch scalar they correspond to.

    ``joints_rad`` holds one value per entry of ``config/hand.yaml`` ``joint_order`` (15), in real
    radians -- the driver owns the SDK's normalised-angle conversion (docs/sdks.md 4.6). ``pinch`` is
    the scalar of CLAUDE.md 5.4, which is what the policy and the recorder consume; the raw joints
    are recorded anyway (5.6).
    """

    joints_rad: np.ndarray
    pinch: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "joints_rad", _vector(self.joints_rad, "HandState.joints_rad"))
        object.__setattr__(self, "pinch", float(self.pinch))


@dataclass(frozen=True, eq=False, slots=True)
class GloveSample:
    """One PxCap Pro frame: the encoder angles in degrees plus the pinch scalar derived from them.

    ``angles_deg`` holds the glove's 17 encoder channels in degrees, in the device's own channel
    order (docs/sdks.md 6.3); ``config/training.yaml`` ``observation.extra_recorded.glove_channels``
    is how many that is. ``pinch`` is the same one-dimensional intent the hand executes (5.4), so
    that operator and robot live in one space.
    """

    angles_deg: np.ndarray
    pinch: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "angles_deg", _vector(self.angles_deg, "GloveSample.angles_deg"))
        object.__setattr__(self, "pinch", float(self.pinch))


@dataclass(frozen=True, eq=False, slots=True)
class WristPose:
    """The operator's wrist pose from the Pico controller in the glove jig.

    ``position_m`` is 3 metres, ``quat_xyzw`` is a unit quaternion in **xyzw** order: pico_bridge's
    own conventions (docs/sdks.md 7.1), carried through unchanged so that no frame or ordering
    convention is invented between the transport and ``teleop/retarget.py``.
    """

    position_m: np.ndarray
    quat_xyzw: np.ndarray

    def __post_init__(self) -> None:
        position = _vector(self.position_m, "WristPose.position_m")
        quat = _vector(self.quat_xyzw, "WristPose.quat_xyzw")
        if position.shape != (3,):
            raise ValueError(f"WristPose.position_m must hold 3 values, got shape {position.shape}")
        if quat.shape != (4,):
            raise ValueError(f"WristPose.quat_xyzw must hold 4 values (xyzw), got shape {quat.shape}")
        object.__setattr__(self, "position_m", position)
        object.__setattr__(self, "quat_xyzw", quat)


@runtime_checkable
class ArmDriver(Protocol):
    """The G1 left arm and waist yaw: 8 commanded joints plus the pinch scalar of the state (5.3)."""

    def read_state(self) -> Stamped[RobotState]:
        """The latest measured state. Read-only; allowed with no session (R1)."""
        ...

    def send_targets(self, cmd: MotionCommand) -> MotionCommand:
        """Send one absolute joint target through the guard; return what the guard admitted.

        Raises :class:`runtime.safety.SafetyViolation` and sends nothing when the command is refused.
        """
        ...


@runtime_checkable
class HandDriver(Protocol):
    """The Paxini DexH15: one pinch scalar in, 15 joint targets out, plus the palm camera."""

    def read_state(self) -> Stamped[HandState]:
        """The latest measured hand pose. Read-only; allowed with no session (R1)."""
        ...

    def send_pinch(self, scalar: float) -> np.ndarray:
        """Send one pinch scalar through the guard; return the 15 joint targets actually sent.

        The scalar is expanded through the synergy of ``config/hand.yaml`` (CLAUDE.md 5.4) *after*
        the guard has admitted it, so the hand envelope is one number and not fifteen.
        Raises :class:`runtime.safety.SafetyViolation` and sends nothing when it is refused.
        """
        ...

    def palm_frame(self) -> Stamped[np.ndarray]:
        """The latest palm-camera frame, ``(h, w, 3)`` uint8 at ``config/cameras.yaml`` palm size."""
        ...


@runtime_checkable
class GloveDriver(Protocol):
    """The PxCap Pro glove. Input device: there is no write call."""

    def read(self) -> Stamped[GloveSample]:
        """The latest glove frame."""
        ...


@runtime_checkable
class PoseDriver(Protocol):
    """The Pico controller's 6-DoF pose. Input device: there is no write call."""

    def read(self) -> Stamped[WristPose]:
        """The latest wrist pose."""
        ...


@runtime_checkable
class CameraDriver(Protocol):
    """One camera stream (``top``, ``oblique`` or ``palm`` in ``config/cameras.yaml``)."""

    def grab(self) -> Stamped[np.ndarray]:
        """The latest frame, ``(h, w, 3)`` uint8 at the stream's ``policy_resolution``."""
        ...
