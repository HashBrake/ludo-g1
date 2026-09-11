"""Mock G1 left arm + waist yaw: a first-order lag behind the last admitted command (T-006).

Same interface as the real ``drivers/g1_arm.py`` of Phase 1 (:class:`drivers.interfaces.ArmDriver`),
same guard, no DDS. The simulated joints chase the commanded target with the time constant
``mock.arm_tau_s`` of ``config/robot.yaml``, sampled on the ``mock.state_hz`` grid, so a command has
an observable, monotone, non-instant response for the Phase 2 latency tooling to measure.

R1: the guard is built with ``simulated=True`` -- a simulated robot needs no human session
(CLAUDE.md R1) -- and every command still goes through :meth:`runtime.safety.Guard.admit`, so the
envelope, the rate limit and the velocity limit apply here exactly as they do on hardware.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from pathlib import Path

import numpy as np

from runtime import config
from runtime.clock import Stamped
from runtime.safety import Guard
from runtime.types import ARM_DOF, JOINT_DIM, MotionCommand, RobotState

from .ticker import Ticker

__all__ = ["MockArm"]

#: Samples kept for :meth:`MockArm.poll` when nobody polls: the default depth of
#: :class:`runtime.clock.StreamBuffer`, which is what a consumer would be buffering them into.
BACKLOG = 4096


class MockArm:
    """A simulated arm. Construct one per test; it holds its own guard, clock and state."""

    def __init__(
        self,
        *,
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        mock = config.load("robot", root=config_root)["mock"]
        self._ticker = Ticker(float(mock["state_hz"]), now_ns)
        self._tau_s = float(mock["arm_tau_s"])
        if not self._tau_s > 0:
            raise config.ConfigError(f"config/robot.yaml: mock.arm_tau_s must be positive, got {self._tau_s}")
        # One step of q += (target - q) * alpha over one state period.
        self._alpha = 1.0 - math.exp(-self._ticker.period_s / self._tau_s)
        self._guard = Guard.from_config(simulated=True, root=config_root)
        # The simulated arm starts at the model's rest pose, which is zero for all 8 commanded joints
        # (mujoco qpos0 of the vendored MJCF, runtime/fk.py), and the hand starts open at the bottom
        # of the pinch range in config/safety.yaml. Neither is a motion target (R2).
        self._q = np.zeros(JOINT_DIM, dtype=np.float64)
        self._pinch = float(self._guard.envelope.pinch_range[0])
        self._target = self._q.copy()
        self._target_pinch = self._pinch
        self._backlog: deque[Stamped[RobotState]] = deque(maxlen=BACKLOG)
        self._latest = self._stamp(self._ticker.origin_ns)

    def __repr__(self) -> str:
        return f"MockArm(state_hz={self._ticker.hz:g}, tau_s={self._tau_s:g}, guard={self._guard!r})"

    @property
    def guard(self) -> Guard:
        """The guard every command of this driver passes through (read-only inspection)."""
        return self._guard

    # -- the ArmDriver contract --------------------------------------------------------------------

    def read_state(self) -> Stamped[RobotState]:
        """The newest simulated state, after advancing the simulation to now."""
        self._advance()
        return self._latest

    def send_targets(self, cmd: MotionCommand) -> MotionCommand:
        """Admit ``cmd`` through the guard and make it the target the simulated joints chase."""
        state = self.read_state().payload
        admitted = self._guard.admit(cmd, state, self._ticker.now_ns())
        self._target = admitted.joints
        self._target_pinch = admitted.pinch
        return admitted

    # -- mock-only helpers -------------------------------------------------------------------------

    def poll(self) -> list[Stamped[RobotState]]:
        """Every state sample produced since the previous poll, oldest first.

        This is the stream a recorder consumes; :meth:`read_state` is the single latest sample. The
        backlog is bounded at :data:`BACKLOG` samples, so a consumer that never polls drops the
        oldest, exactly as a :class:`runtime.clock.StreamBuffer` would.
        """
        self._advance()
        out = list(self._backlog)
        self._backlog.clear()
        return out

    def _advance(self) -> None:
        """Integrate one first-order step per elapsed grid point."""
        for ts_ns in self._ticker.ticks():
            self._q = self._q + (self._target - self._q) * self._alpha
            self._pinch += (self._target_pinch - self._pinch) * self._alpha
            self._latest = self._stamp(ts_ns)
            self._backlog.append(self._latest)

    def _stamp(self, ts_ns: int) -> Stamped[RobotState]:
        state = RobotState(
            arm=self._q[:ARM_DOF],
            waist_yaw=float(self._q[ARM_DOF]),
            pinch=self._pinch,
            ts_ns=ts_ns,
        )
        return Stamped(ts_ns, state)
