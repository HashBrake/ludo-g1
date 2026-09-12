"""The teleop clutch: nothing tracks the operator until the operator is where the robot is (D-018).

Split unchanged out of :mod:`teleop.loop` (T-042, D-013), which imports it back and is still the
only caller. It lives on its own because it is the one piece of the loop with a state machine of its
own, and because :class:`teleop.operator_ui.ClutchView` is the protocol it satisfies.

Nothing here sends anything: :meth:`Clutch.target` returns joint values and the loop is what passes
them to a driver, through :meth:`runtime.safety.Guard.admit` (R1, R3). Holding at the measured state
is a measurement echoed back, not a trajectory (R2).
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from pathlib import Path

import numpy as np

from runtime import clock, config
from runtime.log import get_logger
from runtime.types import JOINT_DIM

__all__ = ["Clutch", "ClutchState"]


class ClutchState(Enum):
    """Where the clutch is. Only ``ENGAGED`` passes the operator's IK solution through untouched."""

    DISENGAGED = "disengaged"  # the arm is commanded to hold its own measured state
    ENGAGING = "engaging"      # blending from the measured state towards the IK target
    ENGAGED = "engaged"        # the IK target, unmodified


class Clutch:
    """Nothing tracks the operator until the operator's hand is where the robot's already is (D-018).

    :meth:`target` is called once a tick with the IK solution and the measured joints and returns
    what the loop commands. Disengaged that is the measured state -- a hold, which is a legitimate
    zero-motion command and not a trajectory (R2) -- while the IK keeps running so the operator can
    see how far off they are. :meth:`request_engage` is the operator's key and is refused unless
    every one of the 8 joints was within ``teleop.clutch_engage_tolerance_rad`` of the state on the
    last tick; then the blend ``state + alpha * (ik - state)`` ramps alpha 0 -> 1 over
    ``teleop.clutch_ramp_s`` (both in ``config/robot.yaml``), so engaging costs no step.

    The guard is still what decides: ``config/safety.yaml`` ``first_command_max_step_rad`` refuses a
    first command this clutch would have let through, and any arm refusal disengages it again.
    """

    def __init__(
        self,
        *,
        tolerance_rad: float,
        ramp_s: float,
        now_ns: Callable[[], int] = clock.now_ns,
    ) -> None:
        self.tolerance_rad, self.ramp_s, self._now = float(tolerance_rad), float(ramp_s), now_ns
        if not self.tolerance_rad > 0 or self.ramp_s < 0:
            raise config.ConfigError(
                f"config/robot.yaml teleop.clutch_engage_tolerance_rad must be positive and "
                f"clutch_ramp_s >= 0, got {self.tolerance_rad} and {self.ramp_s}"
            )
        self.state = ClutchState.DISENGAGED
        #: Per-joint |ik - measured| as of the last :meth:`target` call: what engaging is judged on.
        self.distance_rad = np.full(JOINT_DIM, np.inf)
        self._engaged_ns: int | None = None
        self._log = get_logger("teleop.clutch")

    @classmethod
    def from_config(cls, *, root: Path | str | None = None, now_ns: Callable[[], int] = clock.now_ns) -> Clutch:
        teleop = config.load("robot", root=root)["teleop"]
        return cls(tolerance_rad=float(teleop["clutch_engage_tolerance_rad"]),
                   ramp_s=float(teleop["clutch_ramp_s"]), now_ns=now_ns)

    def __repr__(self) -> str:
        return (f"Clutch(state={self.state.value}, worst={self.worst_distance_rad:.4f} rad, "
                f"tolerance={self.tolerance_rad:g} rad, ramp={self.ramp_s:g} s)")

    @property
    def holding(self) -> bool:
        """True while the arm is held at its measured state and nothing is being passed through."""
        return self.state is ClutchState.DISENGAGED

    @property
    def worst_distance_rad(self) -> float:
        """The worst of the 8 joints between the IK target and the measured state, last tick."""
        return float(np.max(self.distance_rad))

    def request_engage(self) -> bool:
        """The operator's key. Refused, and logged, unless every joint is inside the tolerance."""
        if self.state is not ClutchState.DISENGAGED:
            return False
        if not self.worst_distance_rad <= self.tolerance_rad:
            self._log.warning("clutch_engage_refused", worst_rad=round(self.worst_distance_rad, 4),
                              tolerance_rad=self.tolerance_rad)
            return False
        self.state, self._engaged_ns = ClutchState.ENGAGING, self._now()
        self._log.info("clutch_engaging", ramp_s=self.ramp_s, worst_rad=round(self.worst_distance_rad, 4))
        return True

    def disengage(self, reason: str) -> None:
        """Drop back to holding. The operator engages again, deliberately."""
        if self.state is not ClutchState.DISENGAGED:
            self._log.warning("clutch_disengaged", reason=reason, state=self.state.value)
        self.state, self._engaged_ns = ClutchState.DISENGAGED, None

    def alpha(self, now_ns: int) -> float:
        """The blend weight now: 0 while disengaged, 1 once engaged, linear in between."""
        if self.state is ClutchState.DISENGAGED:
            return 0.0
        if self.state is ClutchState.ENGAGED or self._engaged_ns is None or self.ramp_s <= 0:
            return 1.0
        return min(1.0, max(0.0, (now_ns - self._engaged_ns) / (self.ramp_s * 1e9)))

    def target(self, ik: np.ndarray, measured: np.ndarray, now_ns: int) -> np.ndarray:
        """The 8 joints to command this tick, and record the distance the operator has to close."""
        ik = np.asarray(ik, dtype=np.float64)
        measured = np.asarray(measured, dtype=np.float64)
        self.distance_rad = np.abs(ik - measured)
        weight = self.alpha(now_ns)
        if self.state is ClutchState.ENGAGING and weight >= 1.0:
            self.state = ClutchState.ENGAGED
            self._log.info("clutch_engaged")
        if self.state is ClutchState.ENGAGED:
            return ik
        return measured if weight <= 0.0 else measured + weight * (ik - measured)
