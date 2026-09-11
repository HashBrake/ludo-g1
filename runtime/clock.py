"""One monotonic clock, stream buffers, alignment and latency compensation (CLAUDE.md 5.2).

Every stream in the system (arm state, hand state, glove, controller pose, cameras) is stamped with
:func:`now_ns` at the moment the sample is obtained, buffered in a :class:`StreamBuffer`, and read
back at a common instant with :func:`align`. :func:`shift` applies the per-path latency constants
(measured in Phase 1, stored in ``config/robot.yaml``) so that a recorded action lines up with the
robot's actual response. Definitions and the skew convention are documented in ``docs/clock.md``.

No threads and no I/O live here: this module is only the mechanism.
"""

from __future__ import annotations

import bisect
import time
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

import numpy as np

__all__ = [
    "AlignmentError",
    "SkewStats",
    "Stamped",
    "StreamBuffer",
    "align",
    "now_ns",
    "skew_stats",
    "shift",
]

T = TypeVar("T")

# One process-wide origin, captured at import. now_ns() is nanoseconds since this origin, so
# timestamps stay small and readable and are directly comparable across every module in the process.
_ORIGIN_NS: int = time.monotonic_ns()


def now_ns() -> int:
    """Nanoseconds since the process-wide monotonic origin.

    Monotonic non-decreasing, never affected by wall-clock changes, and identical for every caller
    in the process. It is not comparable across processes or machines.
    """
    return time.monotonic_ns() - _ORIGIN_NS


@dataclass(frozen=True, slots=True)
class Stamped(Generic[T]):
    """A payload with the monotonic timestamp at which it was obtained."""

    ts_ns: int
    payload: T


class AlignmentError(RuntimeError):
    """Raised by :func:`align` when a stream has no sample within the tolerance."""


class StreamBuffer(Generic[T]):
    """A bounded, time-ordered ring buffer of :class:`Stamped` samples for one stream.

    Timestamps must be pushed in non-decreasing order; that invariant is what makes
    :meth:`nearest` a binary search.
    """

    def __init__(self, name: str, maxlen: int = 4096) -> None:
        if maxlen < 1:
            raise ValueError(f"{name}: maxlen must be >= 1, got {maxlen}")
        self.name = name
        self.maxlen = maxlen
        self._items: deque[Stamped[T]] = deque(maxlen=maxlen)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        span = f"{self._items[0].ts_ns}..{self._items[-1].ts_ns}" if self._items else "empty"
        return f"StreamBuffer(name={self.name!r}, n={len(self._items)}, span={span})"

    def push(self, ts_ns: int, payload: T) -> Stamped[T]:
        """Append a sample. Raises ValueError if it is older than the newest sample held."""
        if self._items and ts_ns < self._items[-1].ts_ns:
            raise ValueError(
                f"{self.name}: out-of-order push, ts_ns={ts_ns} < last ts_ns={self._items[-1].ts_ns}"
            )
        item = Stamped(int(ts_ns), payload)
        self._items.append(item)
        return item

    def push_stamped(self, item: Stamped[T]) -> Stamped[T]:
        """Append an already stamped sample (same ordering rule as :meth:`push`)."""
        return self.push(item.ts_ns, item.payload)

    def latest(self) -> Stamped[T]:
        """The newest sample. Raises IndexError when the buffer is empty."""
        if not self._items:
            raise IndexError(f"{self.name}: buffer is empty")
        return self._items[-1]

    def nearest(self, ts_ns: int) -> Stamped[T]:
        """The sample whose timestamp is closest to ``ts_ns`` (ties resolve to the older sample)."""
        if not self._items:
            raise IndexError(f"{self.name}: buffer is empty")
        i = bisect.bisect_left(self._items, ts_ns, key=lambda s: s.ts_ns)
        if i == 0:
            return self._items[0]
        if i == len(self._items):
            return self._items[-1]
        before, after = self._items[i - 1], self._items[i]
        return after if (after.ts_ns - ts_ns) < (ts_ns - before.ts_ns) else before

    def timestamps(self) -> list[int]:
        """All timestamps held, oldest first."""
        return [s.ts_ns for s in self._items]

    def items(self) -> list[Stamped[T]]:
        """All samples held, oldest first."""
        return list(self._items)


Streams = Mapping[str, StreamBuffer] | Iterable[StreamBuffer]


def _as_mapping(streams: Streams) -> dict[str, StreamBuffer]:
    """Accept either {name: buffer} or an iterable of buffers (keyed by their own ``name``)."""
    if isinstance(streams, Mapping):
        return dict(streams)
    out: dict[str, StreamBuffer] = {}
    for buf in streams:
        if buf.name in out:
            raise ValueError(f"duplicate stream name {buf.name!r}")
        out[buf.name] = buf
    return out


def align(streams: Streams, ts_ns: int, tolerance_ns: int) -> dict[str, Stamped]:
    """Return one sample per stream, each the nearest to ``ts_ns``.

    Raises :class:`AlignmentError` if any stream is empty or its nearest sample is further than
    ``tolerance_ns`` from ``ts_ns``; the message names every offending stream and its offset.
    """
    if tolerance_ns < 0:
        raise ValueError(f"tolerance_ns must be >= 0, got {tolerance_ns}")
    bufs = _as_mapping(streams)
    if not bufs:
        raise ValueError("align() needs at least one stream")
    out: dict[str, Stamped] = {}
    bad: list[str] = []
    for name, buf in bufs.items():
        if len(buf) == 0:
            bad.append(f"{name}: empty")
            continue
        sample = buf.nearest(ts_ns)
        offset = sample.ts_ns - ts_ns
        if abs(offset) > tolerance_ns:
            bad.append(f"{name}: nearest offset {offset} ns > tolerance {tolerance_ns} ns")
        else:
            out[name] = sample
    if bad:
        raise AlignmentError(f"cannot align at ts_ns={ts_ns}: " + "; ".join(bad))
    return out


@dataclass(frozen=True, slots=True)
class SkewStats:
    """Per-aligned-frame skew statistics, in nanoseconds (see ``docs/clock.md``).

    Skew of one aligned frame is ``max`` over streams of ``|sample_ts - target_ts|``: the worst-case
    stream offset for that frame. ``p50``/``p99`` are linear-interpolated percentiles of that value
    over all alignment instants.
    """

    n: int
    p50_ns: float
    p99_ns: float
    max_ns: int

    @property
    def p99_ms(self) -> float:
        return self.p99_ns / 1e6

    @property
    def p50_ms(self) -> float:
        return self.p50_ns / 1e6


def shift(stream: StreamBuffer[T], delta_ns: int) -> StreamBuffer[T]:
    """A copy of ``stream`` with every timestamp moved by ``delta_ns`` (latency compensation).

    ``delta_ns`` is added to each timestamp: a stream whose samples describe an event that happened
    ``L`` ns before they were stamped is compensated with ``delta_ns = -L``. The input buffer is not
    modified; the copy keeps the same ``name`` and ``maxlen``.
    """
    out: StreamBuffer[T] = StreamBuffer(stream.name, stream.maxlen)
    for item in stream.items():
        out.push(item.ts_ns + int(delta_ns), item.payload)
    return out


def skew_stats(
    streams: Streams,
    instants: Sequence[int] | None = None,
    tolerance_ns: int | None = None,
) -> SkewStats:
    """p50/p99 of per-frame skew over ``instants``.

    ``instants`` defaults to the timestamps of the slowest stream (the one with the fewest samples),
    which is the usual case: every other stream is resampled onto the slowest one. When
    ``tolerance_ns`` is given, every instant must align within it or :class:`AlignmentError`
    propagates; otherwise the nearest sample is used whatever its offset.
    """
    bufs = _as_mapping(streams)
    if not bufs:
        raise ValueError("skew_stats() needs at least one stream")
    for name, buf in bufs.items():
        if len(buf) == 0:
            raise ValueError(f"skew_stats(): stream {name!r} is empty")
    if instants is None:
        slowest = min(bufs.values(), key=len)
        instants = slowest.timestamps()
    if len(instants) == 0:
        raise ValueError("skew_stats() needs at least one alignment instant")

    per_frame = np.empty(len(instants), dtype=np.int64)
    for k, target in enumerate(instants):
        if tolerance_ns is None:
            worst = max(abs(buf.nearest(target).ts_ns - target) for buf in bufs.values())
        else:
            samples = align(bufs, target, tolerance_ns)
            worst = max(abs(s.ts_ns - target) for s in samples.values())
        per_frame[k] = worst
    return SkewStats(
        n=len(instants),
        p50_ns=float(np.percentile(per_frame, 50)),
        p99_ns=float(np.percentile(per_frame, 99)),
        max_ns=int(per_frame.max()),
    )
