"""Mock Paxini DexH15: one pinch scalar in, 15 synergy joint targets out, plus a palm camera (T-006).

Same interface as the real ``drivers/dexh15.py`` of Phase 1
(:class:`drivers.interfaces.HandDriver`), no Modbus. The synergy is the affine map of CLAUDE.md 5.4,
``q = open + s * (closed - open)``, read from ``config/hand.yaml``.

**Which poses.** The real ``pinch.open_pose`` / ``pinch.closed_pose`` are the literal UNMEASURED
until the Phase 1 bench test, because a wrong pose closes the hand on a finger; the real driver must
refuse to run while they are. This mock reads the stand-in poses under ``mock:`` in the same file
instead, and is the only thing that may.

R1/R3: the guard is built with ``simulated=True`` and ``send_pinch`` still calls
:meth:`runtime.safety.Guard.admit`, so the pinch range and the pinch slew limit of
``config/safety.yaml`` apply here exactly as they do on hardware. The command handed to the guard
holds the arm joints at the simulated hand's own rest pose (zero, the MJCF ``qpos0`` of
``runtime/fk.py``) and varies only the pinch: the arm is a different device with its own driver and
its own guard, and this one has no business moving it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

from drivers.interfaces import HandState
from runtime import config
from runtime.clock import Stamped
from runtime.safety import Guard
from runtime.types import ARM_DOF, JOINT_DIM, MotionCommand, RobotState

from .cameras import MockCamera
from .ticker import Ticker

__all__ = ["MockHand"]


class MockHand:
    """A simulated hand. Construct one per test; it holds its own guard, clock and state."""

    def __init__(
        self,
        *,
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        hand = config.load("hand", root=config_root)
        self.joint_names: tuple[str, ...] = tuple(str(n) for n in hand["joint_order"])
        self._open = self._pose(hand["mock"]["open_pose"], "mock.open_pose")
        self._closed = self._pose(hand["mock"]["closed_pose"], "mock.closed_pose")
        self._ticker = Ticker(float(hand["device"]["command_hz"]), now_ns)
        self._guard = Guard.from_config(simulated=True, root=config_root)
        self._pinch = float(self._guard.envelope.pinch_range[0])
        # Held constant in every command this driver builds; see the module docstring.
        self._rest = np.zeros(JOINT_DIM, dtype=np.float64)
        self._camera = MockCamera("palm", now_ns=now_ns, config_root=config_root)

    def __repr__(self) -> str:
        return f"MockHand(joints={len(self.joint_names)}, hz={self._ticker.hz:g}, guard={self._guard!r})"

    def _pose(self, value: object, what: str) -> np.ndarray:
        pose = np.array(value, dtype=np.float64).reshape(-1)
        if pose.shape != (len(self.joint_names),):
            raise config.ConfigError(
                f"config/hand.yaml: {what} holds {pose.size} values, joint_order holds {len(self.joint_names)}"
            )
        return pose

    @property
    def guard(self) -> Guard:
        """The guard every command of this driver passes through (read-only inspection)."""
        return self._guard

    def synergy(self, scalar: float) -> np.ndarray:
        """The 15 joint targets for a pinch scalar: ``open + s * (closed - open)`` (CLAUDE.md 5.4)."""
        return self._open + float(scalar) * (self._closed - self._open)

    # -- the HandDriver contract -------------------------------------------------------------------

    def read_state(self) -> Stamped[HandState]:
        """The hand pose the simulated hand is holding, on its own sample grid."""
        _index, ts_ns = self._ticker.sample()
        return Stamped(ts_ns, HandState(joints_rad=self.synergy(self._pinch), pinch=self._pinch))

    def send_pinch(self, scalar: float) -> np.ndarray:
        """Admit the scalar through the guard, then expand the admitted value through the synergy."""
        now_ns = self._ticker.now_ns()
        state = RobotState(arm=self._rest[:ARM_DOF], waist_yaw=self._rest[ARM_DOF], pinch=self._pinch, ts_ns=now_ns)
        cmd = MotionCommand(arm=self._rest[:ARM_DOF], waist_yaw=self._rest[ARM_DOF], pinch=scalar)
        admitted = self._guard.admit(cmd, state, now_ns)
        self._pinch = admitted.pinch
        return self.synergy(self._pinch)

    def palm_frame(self) -> Stamped[np.ndarray]:
        """The palm camera's current frame (``config/cameras.yaml`` ``palm``)."""
        return self._camera.grab()
