"""Mock Pico controller pose: a slowly moving 6-DoF wrist pose (T-006).

Same interface as the real ``drivers/pico.py`` of Phase 1 (:class:`drivers.interfaces.PoseDriver`),
which will read ``PicoBridge.wait_frame().controllers.left.pose`` (docs/sdks.md 7.1). Input device:
no write call, no guard, nothing here can move anything.

Conventions are pico_bridge's and are carried through unchanged: position in **metres**, rotation as
a unit quaternion in **xyzw** order, in the headset's own frame -- ``teleop/retarget.py`` owns the
transform into the robot frame (D-006), not the driver. The path is a circle of ``mock.pose_radius_m``
traversed once per ``mock.pose_cycle_s`` with the wrist turning about z at the same rate, centred on
``mock.pose_center_m`` and sampled on the ``mock.pose_hz`` grid (all in ``config/robot.yaml``). It
describes no real hand: it exists so that alignment, skew and replay tests have a smooth, bounded,
repeatable stream. The centre is configurable for one reason: a consumer that retargets this pose on
to the arm (``teleop/loop.py``) needs a path the arm can reach and one it cannot, and the difference
between those two is where the circle sits -- not a second mock.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

import numpy as np

from drivers.interfaces import WristPose
from runtime import config
from runtime.clock import Stamped

from .ticker import Ticker

__all__ = ["MockPose"]


class MockPose:
    """A simulated controller pose. Every sample is a pure function of its grid timestamp."""

    def __init__(
        self,
        *,
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        mock = config.load("robot", root=config_root)["mock"]
        self._ticker = Ticker(float(mock["pose_hz"]), now_ns)
        self._cycle_s = float(mock["pose_cycle_s"])
        self._radius_m = float(mock["pose_radius_m"])
        self._center_m = np.asarray(mock["pose_center_m"], dtype=np.float64).reshape(-1)
        if self._center_m.shape != (3,):
            raise config.ConfigError(
                f"config/robot.yaml: mock.pose_center_m must hold 3 metres, got {mock['pose_center_m']!r}"
            )
        if not self._cycle_s > 0:
            raise config.ConfigError(f"config/robot.yaml: mock.pose_cycle_s must be positive, got {self._cycle_s}")

    def __repr__(self) -> str:
        return (f"MockPose(hz={self._ticker.hz:g}, cycle_s={self._cycle_s:g}, radius_m={self._radius_m:g}, "
                f"center_m={self._center_m.tolist()})")

    def read(self) -> Stamped[WristPose]:
        """The pose the stream is showing now, stamped with its grid time."""
        _index, ts_ns = self._ticker.sample()
        return Stamped(ts_ns, self.pose_at(ts_ns))

    def pose_at(self, ts_ns: int) -> WristPose:
        """The pose this mock produces at ``ts_ns``."""
        theta = 2.0 * math.pi * (ts_ns / 1e9) / self._cycle_s
        offset = self._radius_m * np.array([math.cos(theta), math.sin(theta), 0.5 * math.sin(2.0 * theta)])
        position = self._center_m + offset
        quat = np.array([0.0, 0.0, math.sin(theta / 2.0), math.cos(theta / 2.0)])
        return WristPose(position_m=position, quat_xyzw=quat)
