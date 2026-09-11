# Drivers

`drivers/` is the only place in this project that talks to a device (CLAUDE.md 5.1). Everything
above it — `runtime/controller.py`, `teleop/`, `eval/` — is written against the protocols in
`drivers/interfaces.py` and never learns whether it is holding a real driver or a mock.

```python
from drivers import make

arm   = make("arm")                  # backend="mock" is the default
hand  = make("hand")
glove = make("glove")
pose  = make("pose")
top   = make("top")                  # cameras are named as in config/cameras.yaml
make("oblique", backend="real")      # a real V4L2 camera (T-010); read-only, no session
make("arm", backend="real")          # NotImplementedError until Phase 1
```

Three facts hold for every driver, real or mock:

1. **Every sample is stamped** from `runtime.clock.now_ns` (docs/clock.md) at the instant it was
   obtained, and is returned as a `Stamped(ts_ns, payload)`.
2. **Every write goes through the guard.** `ArmDriver.send_targets` and `HandDriver.send_pinch` call
   `runtime.safety.Guard.admit` and send only what it returns; a refused command raises
   `SafetyViolation` and nothing is sent (docs/safety.md, R1/R3).
3. **Reads are always allowed.** State, cameras, glove and controller pose need no session (R1).

## The protocols

| Protocol | Read | Write | Real driver (Phase 1) |
|---|---|---|---|
| `ArmDriver` | `read_state() -> Stamped[RobotState]` | `send_targets(cmd) -> MotionCommand` | `drivers/g1_arm.py`, `rt/arm_sdk` (D-007) |
| `HandDriver` | `read_state() -> Stamped[HandState]`, `palm_frame() -> Stamped[ndarray]` | `send_pinch(scalar) -> ndarray` (15 targets) | `drivers/dexh15.py`, Paxini SDK |
| `GloveDriver` | `read() -> Stamped[GloveSample]` | — | `drivers/pxcap.py`, PxCap Pro |
| `PoseDriver` | `read() -> Stamped[WristPose]` | — | `drivers/pico.py`, pico_bridge |
| `CameraDriver` | `grab() -> Stamped[ndarray]` | — | `drivers/cameras.py`, V4L2 **(exists, T-010)** |

The sample types are `RobotState` and `MotionCommand` from `runtime/types.py` (the 9 numbers of
CLAUDE.md 5.3) plus three small frozen dataclasses in `drivers/interfaces.py`:

- `HandState(joints_rad, pinch)` — the 15 DexH15 joints in `config/hand.yaml` `joint_order`, in real
  radians, plus the pinch scalar. The driver owns the SDK's normalised-angle conversion
  (docs/sdks.md 4.6).
- `GloveSample(angles_deg, pinch)` — the glove's 17 encoder channels in degrees (docs/sdks.md 6.3)
  and the same one-dimensional pinch intent the hand executes (5.4).
- `WristPose(position_m, quat_xyzw)` — pico_bridge's own conventions, metres and **xyzw**, carried
  through unchanged; `teleop/retarget.py` owns the frame transform, not the driver (D-006).

Frames are `(h, w, 3)` uint8 at the stream's `policy_resolution` (`top` and `oblique` 640x480,
`palm` 320x240): the size the policy sees. The real driver captures at `resolution` and downscales,
so nothing above a driver ever handles a capture-sized frame.

The pinch scalar is the hand's whole action space (5.4). `send_pinch` admits the **scalar** through
the guard and expands it through the synergy afterwards, so the hand envelope is one number and not
fifteen.

## The mocks

`drivers/mock/` implements all five protocols with no hardware. Every test in this project runs
against them by default, and they have three properties that make that useful:

**Deterministic.** A sample is a pure function of its timestamp (and, for the arm, of the commands
admitted so far). Two runs on the same clock sequence produce bit-identical streams.

**Faster than real time.** The clock is injected. Pass a callable returning nanoseconds and advance
it by hand; 10 s of stream costs a few milliseconds and no `sleep`:

```python
class FakeClock:
    def __init__(self): self.ns = 0
    def __call__(self): return self.ns
    def advance(self, s): self.ns += round(s * 1e9)

clk = FakeClock()
arm = make("arm", now_ns=clk)
clk.advance(10.0)
samples = arm.poll()          # 1000 samples at config/robot.yaml mock.state_hz
```

`drivers/mock/ticker.py` is the shared mechanism: a grid of timestamps `origin + k * period` on the
injected clock. `ticks()` hands out every grid point since the last call (the arm integrates one
step per point); `sample()` gives the newest grid point at or before now (everything else just
reports what the stream would currently show).

**Guarded.** `MockArm` and `MockHand` build their guard with `Guard.from_config(simulated=True)` and
still call `admit` on every command. `simulated=True` skips the *human session gate only* — R1
exempts simulated robots from it — and never the envelope: a mock refuses an out-of-box, too-fast,
too-frequent or non-finite command exactly as the robot will. `drivers/mock/` is the only place in
`drivers/` that may set that flag; a test asserts it.

| Mock | Behaviour | Config it reads |
|---|---|---|
| `MockArm` | first-order lag towards the last admitted target, sampled on a fixed grid; `poll()` drains the stream, `read_state()` returns the latest | `robot.yaml` `mock.arm_tau_s`, `mock.state_hz` |
| `MockHand` | pinch scalar in, synergy out, palm camera included | `hand.yaml` `mock.open_pose` / `mock.closed_pose`, `device.command_hz` |
| `MockCamera` | gradient frame carrying the frame counter in row 0 (`frame_index()` reads it back) | `cameras.yaml` `<name>.policy_resolution`, `fps` |
| `MockGlove` | one sine per channel, triangle-wave pinch scalar | `hand.yaml` `glove.input_hz`, `mock.glove_*`, `training.yaml` `observation.extra_recorded.glove_channels` |
| `MockPose` | a slow circle with the wrist turning about z | `robot.yaml` `mock.pose_hz`, `mock.pose_cycle_s`, `mock.pose_radius_m`, `mock.pose_center_m` |

The arm's lag is a **stand-in**, not a model: `mock.arm_tau_s` is UNMEASURED and exists so that the
Phase 2 recorder and latency tooling have a response to measure against mocks. The real number is
`latency.arm_ms` plus the joint's own dynamics, measured in Phase 1.

Likewise the mock's synergy poses. The real `pinch.open_pose` / `pinch.closed_pose` stay the literal
`UNMEASURED` until the Phase 1 bench test, because a wrong pose closes the hand on a finger, and
`drivers/dexh15.py` must refuse to run while they are. The mock reads the stand-in poses under
`mock:` in the same file and is the only thing that may; they are not a proposal for the real
synergy.

## The factory

`drivers.make(name, backend="mock", **kwargs)`.

- `name` is one of `drivers.DEVICES`: `arm`, `hand`, `glove`, `pose`, and the three camera streams
  `top`, `oblique`, `palm`. Camera names are checked against `config/cameras.yaml`.
- `backend="real"` builds a `V4L2Camera` for a camera name (see below) and raises
  `NotImplementedError` naming the device for `arm`, `hand`, `glove` and `pose`. Those names exist
  so that callers can be written against them before Phase 1 delivers the real drivers.
- `kwargs` reach the constructor: the mocks take `now_ns=` (the injectable clock) and `config_root=`
  (a different `config/` directory, for tests); `V4L2Camera` takes those plus `device=`.

## Real cameras (`drivers/cameras.py`)

One class, `V4L2Camera(name)`, serves `top`, `oblique` and `palm`, satisfies the same
`CameraDriver` protocol as `MockCamera`, and emits frames at the same `policy_resolution` — the
capture size never leaves the module, so a recorder cannot tell the two backends apart. Frames are
BGR, OpenCV's order, the same order `cv2.imread` gives `board/calibration.py`.

```python
from drivers.cameras import V4L2Camera, CameraUnavailable

with V4L2Camera("oblique") as cam:
    print(cam.probe())            # what the device negotiated, not what we asked for
    frame = cam.grab()            # Stamped(ts_ns, (480, 640, 3) uint8)
```

Reading a camera is not a motion command, so R1 does not apply and no session is needed. There is
no write call on this protocol at all — a test asserts the class has none.

**Device discovery**, in order:

1. an explicit `device=` (a `/dev/...` path or a numeric index);
2. `config/cameras.yaml` `<name>.device`, when it is not the `UNMEASURED` placeholder. Put a
   `/dev/v4l/by-id/...` path there, never `/dev/videoN`: node numbers move when devices are
   replugged, and a policy trained on `top` must never be fed `oblique`;
3. `<name>.usb_id`: the lowest-numbered `VIDEO_CAPTURE` node whose USB `vendor:product` matches,
   reported through its by-id link when udev made one. Enumeration is sysfs plus one read-only
   `VIDIOC_QUERYCAP`; it never streams. Because the id comes from the config, discovery can only
   match the device the config already declares. For the Ego's stereo pair the first capture node
   is the **left** stream, which is what `oblique` is (D-009).

Everything that means "there is no camera to read" — unconfigured, absent, busy, silent, closed —
raises `CameraUnavailable`, never a bare `OSError`, so a read-only check can skip instead of fail.
Tests that want a real device are marked `readonly` and skip with a reason naming the device or the
config key that would supply one.

**No depth.** `V4L2Camera(name, depth=True)` raises `NotImplementedError` naming `pyorbbecsdk`
(D-009, docs/sdks.md 8.2): the Ego is a UVC stereo pair here and `oblique` is its left RGB stream.

**The read-only stream check.** `tools/hardware_checks/stream_stats.py` streams one camera for N
seconds and reports achieved fps, drops (gaps longer than 1.5 nominal periods) and inter-frame
jitter p50/p99 — the Phase 1 "stream every device and report drop rates and jitter" check:

```
.venv/bin/python tools/hardware_checks/stream_stats.py --backend mock --seconds 5
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --camera oblique --seconds 10 --warmup 15
```

`--backend mock` needs no hardware and exits 0 with nothing plugged in; `--json` emits the same
report as a dict. Exit 3 means there was no camera to read.

## Phase 1: writing a real driver

Implement the protocol, nothing more. In particular:

- Stamp every sample from `runtime.clock.now_ns` at the moment the sample arrives, not when it is
  returned.
- Build the guard with `Guard.from_config()` — `simulated` stays `False` on hardware — and pass every
  command through `admit`. Never construct an `Envelope` or a `SessionGate` by hand.
- Keep scripted motion out: bring-up sequences and calibration moves live in
  `tools/hardware_checks/`, which `drivers/`, `runtime/` and `policy/` never import (R2).
- Give the new driver the same mock-backed tests, and mark any test that moves hardware `motion` so
  it is skipped without a session (CLAUDE.md 4.6).
