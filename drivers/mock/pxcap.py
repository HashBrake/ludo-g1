"""Mock PxCap Pro glove: a deterministic 17-channel encoder stream plus a pinch scalar (T-006).

Same interface as the real ``drivers/pxcap.py`` of Phase 1
(:class:`drivers.interfaces.GloveDriver`). The glove is an input device: there is no write call, no
guard, and nothing here can move anything.

Channel count, rate and waveform all come from config: ``config/training.yaml``
``observation.extra_recorded.glove_channels`` (17, docs/sdks.md 6.3), ``config/hand.yaml``
``glove.input_hz``, and the ``mock.glove_*`` shape parameters. Angles are in degrees, as the device
reports them. The scalar the real driver will report comes from the thumb-to-index tip distance
through ``glove.open_distance_mm`` / ``closed_distance_mm``; those are UNMEASURED, so the mock's
scalar is a triangle wave over ``pinch.scalar_range`` instead and carries no claim about the mapping.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

import numpy as np

from drivers.interfaces import GloveSample
from runtime import config
from runtime.clock import Stamped

from .ticker import Ticker

__all__ = ["MockGlove"]


class MockGlove:
    """A simulated glove. Every sample is a pure function of its grid timestamp."""

    def __init__(
        self,
        *,
        now_ns: Callable[[], int] | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        hand = config.load("hand", root=config_root)
        training = config.load("training", root=config_root)
        self.channels = int(training["observation"]["extra_recorded"]["glove_channels"])
        self._ticker = Ticker(float(hand["glove"]["input_hz"]), now_ns)
        self._cycle_s = float(hand["mock"]["glove_cycle_s"])
        self._amplitude_deg = float(hand["mock"]["glove_angle_amplitude_deg"])
        self._low, self._high = (float(v) for v in hand["pinch"]["scalar_range"])
        if not self._cycle_s > 0:
            raise config.ConfigError(f"config/hand.yaml: mock.glove_cycle_s must be positive, got {self._cycle_s}")

    def __repr__(self) -> str:
        return f"MockGlove(channels={self.channels}, hz={self._ticker.hz:g}, cycle_s={self._cycle_s:g})"

    def read(self) -> Stamped[GloveSample]:
        """The glove frame the stream is showing now, stamped with its grid time."""
        _index, ts_ns = self._ticker.sample()
        return Stamped(ts_ns, self.sample_at(ts_ns))

    def sample_at(self, ts_ns: int) -> GloveSample:
        """The sample this mock produces at ``ts_ns``: one sine per channel, one triangle scalar."""
        phase = (ts_ns / 1e9) / self._cycle_s
        channel_phase = np.arange(self.channels, dtype=np.float64) / self.channels
        angles = self._amplitude_deg * np.sin(2.0 * math.pi * (phase + channel_phase))
        # Triangle over the full scalar range: 0 -> 1 -> 0 once per cycle, continuous, no rate step.
        fraction = 2.0 * abs((phase % 1.0) - 0.5)
        pinch = self._low + fraction * (self._high - self._low)
        return GloveSample(angles_deg=angles, pinch=pinch)
