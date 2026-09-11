"""A fixed-rate sample grid driven by an injectable clock: the one timing mechanism every mock uses.

A real device produces samples at its own rate and the driver stamps them on arrival. A mock has no
device, so it manufactures the same grid: timestamps ``origin + k * period``, where ``origin`` is the
clock reading at construction. Because the clock is injected, a test can advance 10 s of stream in
one call and the grid is identical to the one a 10 s wall-clock run would have produced.

Two ways to read the grid, and a driver uses exactly one:

* :meth:`ticks` -- every grid point since the previous call, for a mock that has to integrate state
  forward one step at a time (the arm's first-order lag).
* :meth:`sample` -- the newest grid point at or before now, for a mock that just reports whatever the
  stream would currently be showing (cameras, hand, glove, controller pose).

No threads and no sleeping: time only ever comes from the injected clock.
"""

from __future__ import annotations

from collections.abc import Callable

from runtime import clock

__all__ = ["Ticker"]


class Ticker:
    """The sample grid of one mock stream at ``hz``, on the clock ``now_ns``."""

    def __init__(self, hz: float, now_ns: Callable[[], int] | None = None) -> None:
        if not hz > 0:
            raise ValueError(f"Ticker needs a positive rate, got {hz!r} Hz")
        self.hz = float(hz)
        self.now_ns: Callable[[], int] = clock.now_ns if now_ns is None else now_ns
        self.period_ns = max(round(1e9 / self.hz), 1)
        self.origin_ns = int(self.now_ns())
        self._emitted = 0

    def __repr__(self) -> str:
        return f"Ticker(hz={self.hz:g}, period_ns={self.period_ns}, origin_ns={self.origin_ns})"

    @property
    def period_s(self) -> float:
        return self.period_ns / 1e9

    def _elapsed(self) -> int:
        """Index of the newest grid point at or before now; 0 before the first period is up."""
        now = int(self.now_ns())
        if now < self.origin_ns:  # a clock that went backwards is a bug in the caller, not a state
            raise ValueError(f"clock went backwards: now {now} < origin {self.origin_ns}")
        return (now - self.origin_ns) // self.period_ns

    def sample(self) -> tuple[int, int]:
        """``(index, ts_ns)`` of the newest grid point at or before now."""
        k = self._elapsed()
        return k, self.origin_ns + k * self.period_ns

    def ticks(self) -> list[int]:
        """Timestamps of the grid points reached since the previous call, oldest first.

        Never returns the same grid point twice, so a driver may call it as often as it likes and
        still integrate exactly once per period.
        """
        k = self._elapsed()
        out = [self.origin_ns + i * self.period_ns for i in range(self._emitted + 1, k + 1)]
        self._emitted = max(self._emitted, k)
        return out
