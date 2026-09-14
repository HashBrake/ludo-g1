# Clock, stream alignment and latency compensation

`runtime/clock.py` is the single time base for the project (CLAUDE.md 5.2). Every stream — arm and
hand state, glove, controller pose, Brio, Orbbec, palm camera — is stamped from it, buffered, and
read back at a common instant. `runtime/log.py` stamps every log event from the same clock.

No threads, no I/O, no device knowledge lives here. Latency constants are measured in Phase 1 and
stored in `config/robot.yaml`; this module only provides the mechanism to apply them.

## The clock

```python
from runtime.clock import now_ns
t = now_ns()   # int nanoseconds since a process-wide origin captured at import
```

`now_ns()` is `time.monotonic_ns()` minus one origin captured when the module is first imported.

- Monotonic non-decreasing; unaffected by wall-clock or NTP changes.
- Identical for every module in the process (one origin), so timestamps are directly comparable.
- **Not** comparable across processes or machines. A recorder that spans processes must record the
  offset explicitly; nothing in Phase 0 does.
- Repeated calls may return the same value (clock granularity); only ordering is guaranteed.

Wall-clock time for humans (episode names, session metadata) comes from `datetime`, never from here.

### Kernel timestamps are on the same clock (T-047)

Some samples are stamped by the kernel before user space ever sees them. The case that matters is a
V4L2 capture buffer: the kernel stamps it on `CLOCK_MONOTONIC` when the frame completes, and OpenCV's
V4L2 backend reports that stamp as `CAP_PROP_POS_MSEC` in milliseconds. `time.monotonic_ns()` reads
the *same* `CLOCK_MONOTONIC` on Linux, so the only difference between a kernel stamp and `now_ns()`
is the origin this module subtracts at import:

```python
from runtime.clock import from_monotonic_ns, to_monotonic_ns
ts = from_monotonic_ns(round(cap.get(cv2.CAP_PROP_POS_MSEC) * 1e6))   # kernel ns -> our ns
raw = to_monotonic_ns(ts)                                             # and back
```

The conversion is exact — one integer subtraction, no estimation, no drift model, nothing to
calibrate — and the result is directly comparable with every other stamp in the process. It is not
an interpolation between clocks; if it ever had to be, that would be a different function with an
error bar.

What the two stamps *mean* is not the same, which is the whole point (D-025): the kernel stamp is
when the frame was **captured**, `now_ns()` at the return of `read()` is when user space
**collected** it. On this host they sit 2–10 ms apart when it is quiet and tens of milliseconds
apart when it is busy. `drivers/cameras.py` stamps frames with the kernel timestamp and keeps the
arrival stamp as a fallback, so alignment and the skew statistic below are measured on capture time
and a busy host shows up as a late frame rather than as a mis-timed one.

## Stamped and StreamBuffer

```python
from runtime.clock import Stamped, StreamBuffer

buf: StreamBuffer[np.ndarray] = StreamBuffer("state", maxlen=4096)
buf.push(now_ns(), q)            # returns the Stamped(ts_ns, payload) it stored
buf.latest()                     # newest sample; IndexError when empty
buf.nearest(t)                   # sample whose ts is closest to t; ties -> the older sample
buf.timestamps(), buf.items()    # copies, oldest first
```

`Stamped` is a frozen dataclass of `(ts_ns, payload)`. `StreamBuffer` is a bounded ring buffer
(`collections.deque`); pushing past `maxlen` drops the oldest sample.

Invariant: **timestamps are pushed in non-decreasing order**. A push older than the newest sample
held raises `ValueError`. This is what a driver naturally produces (it stamps on arrival) and it is
what makes `nearest` a binary search. If a transport ever delivers out of order, sort before
pushing; do not relax the invariant.

Outside the buffer's span, `nearest` returns the first or last sample (it never extrapolates) — so
`align` is what rejects a stale stream, not `nearest`.

## align

```python
from runtime.clock import align, AlignmentError
samples = align({"top": cam, "state": arm}, target_ts_ns, tolerance_ns=10_000_000)
# -> {"top": Stamped(...), "state": Stamped(...)}
```

One sample per stream, each the nearest to `target_ts_ns`. Raises `AlignmentError` naming every
stream that is empty or whose nearest sample is further than `tolerance_ns` away — so a dropped or
stalled stream fails loudly instead of silently pairing an old frame with a fresh state.

`streams` may be a mapping `{name: buffer}` (the key names the output, useful when the observation
key differs from the driver's name) or any iterable of buffers (keyed by `buffer.name`; duplicate
names raise).

## Skew definition

For one alignment instant `t`, skew is

```
skew(t) = max over streams of |sample_ts(t) - t|
```

i.e. the worst-case per-stream offset for that aligned frame, not a pairwise difference between two
streams. `skew_stats(streams, instants=None, tolerance_ns=None)` returns `SkewStats(n, p50_ns,
p99_ns, max_ns)` over all instants (`p50_ms` / `p99_ms` properties for reading). Percentiles are
`numpy.percentile` defaults (linear interpolation).

`instants` defaults to the timestamps of the stream with the fewest samples — the usual case, where
every faster stream is resampled onto the slowest one (30 Hz cameras). Pass an explicit grid to
measure against nominal instants instead. With `tolerance_ns` set, each instant goes through `align`
and an unalignable frame raises instead of being counted.

This is the number the Phase 2 dataset card reports (`< 10 ms at p99`, CLAUDE.md 4.4).

## Latency compensation

```python
from runtime.clock import shift
compensated = shift(state_stream, -arm_latency_ns)
```

`shift` returns a **copy** of the buffer with `delta_ns` added to every timestamp; the input is
untouched and `name`/`maxlen` are preserved. Sign convention: a stream whose samples describe an
event that happened `L` ns before it was stamped is compensated with `delta_ns = -L`, which moves
its timestamps back onto the instants at which the event actually occurred. The latency constants
`arm_ms` / `hand_ms` come from `config/robot.yaml` (measured in Phase 1, `UNMEASURED` until then).

## Measured behaviour (T-004)

```
.venv/bin/python -m pytest -q tests/test_clock.py -s
```

Two synthetic streams, 30 Hz and 100 Hz, 2 ms gaussian timestamp jitter, 60 s, aligned on the
nominal 30 Hz grid (1800 frames), RNG seed 20260911, tolerance 10 ms:

```
p50 = 2.982 ms, p99 = 6.701 ms, max = 7.799 ms
```

The floor here is geometric, not a defect: a 100 Hz stream sampled at instants that are not its own
is on average 2.5 ms and at worst 5 ms away, before jitter. Against real hardware the number is
measured with the same function on recorded streams.
