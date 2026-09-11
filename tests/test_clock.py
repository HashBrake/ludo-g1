"""Clock, buffer, alignment and latency-compensation tests (T-004).

The synthetic streams imitate the real pair from CLAUDE.md 5.2: a 100 Hz robot-state stream and a
30 Hz camera stream, each with 2 ms gaussian timestamp jitter. The RNG seed is fixed so the printed
p99 is reproducible.
"""

from __future__ import annotations

import numpy as np
import pytest

from runtime.clock import AlignmentError, Stamped, StreamBuffer, align, now_ns, shift, skew_stats

MS = 1_000_000
SEED = 20260911
JITTER_NS = 2 * MS
DURATION_S = 60.0


def _jittered_stream(
    name: str, rate_hz: float, duration_s: float, rng: np.random.Generator, t0_ns: int = 0
) -> StreamBuffer[int]:
    """A stream at ``rate_hz`` over ``duration_s`` with gaussian jitter on every timestamp.

    Timestamps are sorted before pushing: a driver stamps a sample when it arrives, so the recorded
    sequence is non-decreasing even when transport jitter shuffles the underlying events.
    """
    n = int(round(rate_hz * duration_s)) + 1
    nominal = t0_ns + np.round(np.arange(n) * 1e9 / rate_hz).astype(np.int64)
    jitter = np.round(rng.normal(0.0, JITTER_NS, size=n)).astype(np.int64)
    ts = np.sort(nominal + jitter)
    buf: StreamBuffer[int] = StreamBuffer(name, maxlen=n)
    for i, t in enumerate(ts):
        buf.push(int(t), i)  # payload is the sample index, so pairings are checkable
    return buf


def test_now_ns_is_monotonic_over_10000_calls() -> None:
    samples = [now_ns() for _ in range(10_000)]
    assert all(b >= a for a, b in zip(samples, samples[1:], strict=False)), "now_ns() went backwards"
    assert samples[-1] > samples[0], "now_ns() never advanced over 10000 calls"
    assert samples[0] >= 0, "now_ns() must be relative to the process origin"


def test_stamped_holds_timestamp_and_payload() -> None:
    s = Stamped(123, {"q": 1})
    assert s.ts_ns == 123 and s.payload == {"q": 1}
    with pytest.raises(AttributeError):  # frozen
        s.ts_ns = 1  # type: ignore[misc]


def test_buffer_push_latest_nearest_and_bounds() -> None:
    buf: StreamBuffer[str] = StreamBuffer("s", maxlen=3)
    with pytest.raises(IndexError):
        buf.latest()
    with pytest.raises(IndexError):
        buf.nearest(0)
    for i, t in enumerate([0, 10, 20, 30]):
        buf.push(t, f"p{i}")
    assert len(buf) == 3, "maxlen must drop the oldest sample"
    assert buf.timestamps() == [10, 20, 30]
    assert buf.latest().payload == "p3"
    assert buf.nearest(-100).ts_ns == 10, "before the first sample -> first sample"
    assert buf.nearest(10_000).ts_ns == 30, "after the last sample -> last sample"
    assert buf.nearest(19).ts_ns == 20
    assert buf.nearest(16).ts_ns == 20
    assert buf.nearest(14).ts_ns == 10
    assert buf.nearest(15).ts_ns == 10, "ties resolve to the older sample"
    assert buf.nearest(20).ts_ns == 20


def test_buffer_rejects_out_of_order_push() -> None:
    buf: StreamBuffer[int] = StreamBuffer("s", maxlen=4)
    buf.push(100, 1)
    buf.push(100, 2)  # equal timestamps are allowed
    with pytest.raises(ValueError, match="out-of-order"):
        buf.push(99, 3)
    with pytest.raises(ValueError, match="maxlen"):
        StreamBuffer("bad", maxlen=0)


def test_align_returns_one_sample_per_stream_and_raises_outside_tolerance() -> None:
    a: StreamBuffer[str] = StreamBuffer("a", 8)
    b: StreamBuffer[str] = StreamBuffer("b", 8)
    for t in (0, 10 * MS, 20 * MS):
        a.push(t, f"a{t}")
    for t in (3 * MS, 13 * MS, 40 * MS):
        b.push(t, f"b{t}")

    got = align([a, b], 12 * MS, tolerance_ns=2 * MS)
    assert set(got) == {"a", "b"}
    assert got["a"].ts_ns == 10 * MS and got["b"].ts_ns == 13 * MS

    with pytest.raises(AlignmentError, match="b: nearest offset"):
        align([a, b], 25 * MS, tolerance_ns=2 * MS)

    empty: StreamBuffer[str] = StreamBuffer("c", 4)
    with pytest.raises(AlignmentError, match="c: empty"):
        align([a, empty], 0, tolerance_ns=MS)
    with pytest.raises(ValueError):
        align([], 0, tolerance_ns=MS)
    with pytest.raises(ValueError):
        align([a], 0, tolerance_ns=-1)
    with pytest.raises(ValueError, match="duplicate stream name"):
        align([a, StreamBuffer("a", 4)], 0, tolerance_ns=MS)
    # a mapping keyed independently of buffer.name is accepted too
    assert set(align({"top": a, "state": b}, 12 * MS, tolerance_ns=2 * MS)) == {"top", "state"}


def test_shift_then_align_recovers_the_original_pairing() -> None:
    """A stream delayed by a known latency is re-paired exactly after compensating it."""
    rng = np.random.default_rng(SEED)
    cam = _jittered_stream("top", 30.0, 5.0, rng)
    state = _jittered_stream("state", 100.0, 5.0, rng)

    # Truth: for each camera sample, the state sample nearest to it.
    truth = {c.ts_ns: state.nearest(c.ts_ns).payload for c in cam.items()}

    delay_ns = 37 * MS  # the state path reports 37 ms late
    delayed: StreamBuffer[int] = StreamBuffer("state", state.maxlen)
    for item in state.items():
        delayed.push(item.ts_ns + delay_ns, item.payload)

    # Without compensation the pairing is wrong for essentially every frame.
    wrong = sum(1 for c in cam.items() if delayed.nearest(c.ts_ns).payload != truth[c.ts_ns])
    assert wrong > 0.9 * len(cam.items())

    compensated = shift(delayed, -delay_ns)
    assert compensated.name == "state" and len(compensated) == len(delayed)
    assert compensated.timestamps() == state.timestamps()
    for c in cam.items():
        got = align([cam, compensated], c.ts_ns, tolerance_ns=10 * MS)
        assert got["state"].payload == truth[c.ts_ns]
        assert got["top"].ts_ns == c.ts_ns
    # shift leaves the input untouched
    assert delayed.timestamps()[0] == state.timestamps()[0] + delay_ns


def test_skew_p99_under_10ms_for_30hz_and_100hz_streams_over_60s() -> None:
    """Acceptance: 30 Hz + 100 Hz with 2 ms jitter, aligned on the nominal 30 Hz grid for 60 s."""
    rng = np.random.default_rng(SEED)
    # Both streams start slightly before the grid and end slightly after it, as real capture does.
    cam = _jittered_stream("top", 30.0, DURATION_S + 0.2, rng, t0_ns=-100 * MS)
    state = _jittered_stream("state", 100.0, DURATION_S + 0.2, rng, t0_ns=-100 * MS)

    n_instants = int(30.0 * DURATION_S)
    instants = [int(round(k * 1e9 / 30.0)) for k in range(n_instants)]
    tolerance_ns = 10 * MS

    stats = skew_stats({"top": cam, "state": state}, instants, tolerance_ns=tolerance_ns)

    print(
        f"\nskew over {stats.n} aligned frames (60 s @ 30 Hz, seed {SEED}): "
        f"p50 = {stats.p50_ms:.3f} ms, p99 = {stats.p99_ms:.3f} ms, max = {stats.max_ns / 1e6:.3f} ms"
    )
    assert stats.n == n_instants
    assert stats.p99_ns < 10 * MS, f"p99 skew {stats.p99_ms:.3f} ms >= 10 ms"


def test_skew_stats_defaults_to_the_slowest_stream_and_validates_input() -> None:
    rng = np.random.default_rng(SEED)
    cam = _jittered_stream("top", 30.0, 2.0, rng)
    state = _jittered_stream("state", 100.0, 2.0, rng)
    stats = skew_stats([cam, state])  # instants default to the 30 Hz stream's own timestamps
    assert stats.n == len(cam)
    assert stats.p99_ns < 10 * MS
    # a stream aligned against its own timestamps has zero skew
    assert skew_stats([cam]).max_ns == 0
    with pytest.raises(ValueError, match="empty"):
        skew_stats([cam, StreamBuffer("dead", 4)])
    with pytest.raises(ValueError):
        skew_stats([])
    with pytest.raises(ValueError, match="alignment instant"):
        skew_stats([cam], instants=[])
    with pytest.raises(AlignmentError):
        skew_stats([cam, state], instants=[10**12], tolerance_ns=MS)


def test_logger_stamps_every_event(capsys: pytest.CaptureFixture[str]) -> None:
    import json as json_mod

    from runtime import log as log_mod

    log_mod.configure(json=True)
    try:
        before = now_ns()
        log_mod.get_logger("test.clock", stream="top").info("frame", index=7)
        after = now_ns()
        line = capsys.readouterr().out.strip().splitlines()[-1]
        event = json_mod.loads(line)
        assert event["event"] == "frame" and event["index"] == 7 and event["stream"] == "top"
        assert before <= event["ts_ns"] <= after
        assert event["ts_s"] == pytest.approx(event["ts_ns"] / 1e9, abs=1e-6)
    finally:
        log_mod.configure()  # back to the console renderer for other tests
