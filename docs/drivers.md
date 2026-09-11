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
make("arm", backend="real")          # the real G1 state stream (T-018); read-only, no session
make("hand", backend="real")         # the real DexH15 (T-019); read-only, no session
make("palm", backend="real")         # the DexH15's own palm camera (T-019)
make("glove", backend="real")        # NotImplementedError until its Phase 1 driver exists
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
| `ArmDriver` | `read_state() -> Stamped[RobotState]` | `send_targets(cmd) -> MotionCommand` | `drivers/g1_arm.py`, `rt/lowstate` **(read half exists, T-018)**; the write path is T-021 (D-007) |
| `HandDriver` | `read_state() -> Stamped[HandState]`, `palm_frame() -> Stamped[ndarray]` | `send_pinch(scalar) -> ndarray` (15 targets) | `drivers/dexh15.py`, Paxini SDK **(read half exists, T-019)**; the write path is T-022 |
| `GloveDriver` | `read() -> Stamped[GloveSample]` | — | `drivers/pxcap.py`, PxCap Pro |
| `PoseDriver` | `read() -> Stamped[WristPose]` | — | `drivers/pico.py`, pico_bridge |
| `CameraDriver` | `grab() -> Stamped[ndarray]` | — | `drivers/cameras.py`, V4L2 **(exists, T-010)**; `palm` is `drivers/dexh15.py`'s `PalmCamera` **(T-019)** |

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
`UNMEASURED` until the Phase 1 bench test (T-020), because a wrong pose closes the hand on a finger.
The mock reads the stand-in poses under `mock:` in the same file and is the only thing that may; they
are not a proposal for the real synergy. The real `drivers/dexh15.py` never reads them: while the
real poses are `UNMEASURED` it simply cannot report a pinch scalar (`nan`, below), and it will refuse
to *command* one at T-022, when a wrong pose could do damage.

## The factory

`drivers.make(name, backend="mock", **kwargs)`.

- `name` is one of `drivers.DEVICES`: `arm`, `hand`, `glove`, `pose`, and the three camera streams
  `top`, `oblique`, `palm`. Camera names are checked against `config/cameras.yaml`.
- `backend="real"` builds a `V4L2Camera` for `top` and `oblique`, a `G1Arm` for `arm`, a `DexH15` for
  `hand` and a `PalmCamera` for `palm` (all below), and raises `NotImplementedError` naming the
  device for `glove` and `pose`. Those two names exist so that callers can be written against them
  before Phase 1 delivers the real drivers.
- `kwargs` reach the constructor: the mocks take `now_ns=` (the injectable clock) and `config_root=`
  (a different `config/` directory, for tests); `V4L2Camera` takes those plus `device=`; `G1Arm`
  takes those plus `subscriber_factory=` and `timeout_s=`; `DexH15` takes those plus
  `control_factory=`, `camera_factory=` and `port=`.

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

**The read-only stream check.** `tools/hardware_checks/stream_stats.py` streams one device for N
seconds and reports the achieved rate, drops (gaps longer than 1.5 nominal periods) and inter-sample
jitter p50/p99 — the Phase 1 "stream every device and report drop rates and jitter" check. `--stream`
picks the device: a camera name, `arm` for the G1 state stream, or `hand` for the DexH15's joint
angles (`--camera` still works for a camera):

```
.venv/bin/python tools/hardware_checks/stream_stats.py --backend mock --seconds 5
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream oblique --seconds 10 --warmup 15
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream arm --seconds 600
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream hand --seconds 600
```

Cameras are polled with `grab()` and the hand with `read_state()`, both de-duplicated by timestamp;
the arm is drained with `poll()`, so the timestamps are the ones the subscriber callback stamped and
a slow poll loop cannot invent a drop. The hand has no queue at all — one `read_state()` is one
synchronous Modbus round trip — so what `--stream hand` measures is the achieved rate of back-to-back
reads. `--backend mock` needs no hardware and exits 0 with nothing plugged in (`--stream arm` streams
`MockArm`, `--stream hand` streams `MockHand`); `--json` emits the same report as a dict. Exit 3
means there was no stream to read.

## The real arm (`drivers/g1_arm.py`)

`G1Arm` is the **read half** of `ArmDriver`: it subscribes to the G1's `rt/lowstate` and gives the
same `Stamped[RobotState]` as `MockArm`. It creates **no DDS writer of any kind** — no publisher, no
enable weight, no guard — so nothing in this module can move the robot (T-018, R1/R2). The write
path, its weight ramp and `runtime.safety.Guard` are T-021 (D-007). A test asserts by grepping the
module that no publisher class and no command topic appear in it.

```python
from drivers.g1_arm import G1Arm, ArmUnavailable

with G1Arm() as arm:
    print(arm.probe())                  # measured state rate, mode_machine, interface
    state = arm.read_state()            # Stamped(ts_ns, RobotState) - 7 arm joints + waist yaw
    full  = arm.full_state()            # q/dq/tau_est for all 29 joints, for the dataset (5.6)
    batch = arm.poll()                  # every sample since the last poll, for a rate check
```

Reading state is not a motion command: no hardware session is needed (R1, CLAUDE.md 4.6).

- **Stamping.** The subscriber handler stamps with `runtime.clock.now_ns` the moment cyclonedds hands
  it the message, on the reader thread; `read_state()` returns the newest sample and never blocks
  once the stream is running. `poll()` drains everything that arrived since the last call (bounded at
  `BACKLOG` = 4096 samples, 8 s at 500 Hz) and is what a recorder or a stream check consumes.
- **`RobotState.pinch` is 0.0 here and means nothing**: the DexH15 is a separate device on a separate
  SDK, and `runtime/controller.py` and `teleop/recorder.py` take the pinch from `HandDriver`.
- **Unavailability.** An unconfigured interface, no message within `control.state_timeout_s`, a
  stream that went silent, or a closed driver all raise `ArmUnavailable` naming the interface, so a
  read-only check skips instead of failing. Tests that need the robot are marked `readonly`.

### DDS setup

Everything the transport needs is in `config/robot.yaml`: `network.dds_interface` (the Linux
interface name that holds the G1 LAN, `UNMEASURED` until H-002 is done), `network.dds_domain_id`
(0), `topics.state` (`rt/lowstate`), `control.state_hz` (nominally 500), `control.state_timeout_s`
and `control.motor_count` (29 of the message's 35 slots). `ChannelFactoryInitialize(domain_id,
interface)` binds the **whole process** to one domain and one interface, so `G1Arm` does it once, on
the first subscriber it builds, never at import time; a second driver asking for a different
interface raises rather than silently sharing the first one. `drivers.g1_arm.dds_binding()` reports
what the process bound to.

The link itself is the lab's documented addressing (docs/sdks.md 2.5): laptop `192.168.123.2/24`,
robot `192.168.123.164`, wired Ethernet on the built-in port `enp0s31f6` or the ASIX AX88179 USB
dongle (`enx000ec6c10aa5`). The one-time NetworkManager profile (H-002):

```
nmcli con add type ethernet ifname enp0s31f6 con-name robot-lan ipv4.method manual \
      ipv4.addresses 192.168.123.2/24 connection.autoconnect yes
nmcli con up robot-lan
ping -c 2 192.168.123.164
.venv/bin/python tools/hardware_checks/list_devices.py      # prints the interface with '<-- G1 LAN'
```

Then put that interface name in `config/robot.yaml` `network.dds_interface` (and flip nothing else:
it is a Form-1 `UNMEASURED` placeholder today). Until that is done, every real-arm call raises
`ArmUnavailable` naming the key, and the `readonly` tests skip.

## The real hand (`drivers/dexh15.py`)

`DexH15` is the **read half** of `HandDriver`: it queries the DexH15 over Modbus and gives the same
`Stamped[HandState]` as `MockHand`. It **writes nothing to the hand** — no motor is powered, no
control mode is chosen, no target is sent — so nothing in this module can move it (T-019, R1/R2).
The write path (the CLAUDE.md 5.4 synergy behind `runtime.safety.Guard`, and the rest of the
bring-up order of docs/sdks.md 4.2) is T-022. A test asserts by grepping the module that the SDK's
writing verbs — `enableMotor`, `setMotor*`, `setJoint*` — appear nowhere but inside the refusing
`send_pinch` stub, and that `initMotorPosition`, which rewrites the hand's zero, appears nowhere.

```python
from drivers.dexh15 import DexH15, HandUnavailable

with DexH15() as hand:
    print(hand.probe())               # SN, hardware/firmware/SDK versions, the link it answered on
    state = hand.read_state()         # Stamped(ts_ns, HandState) - 15 joints in real radians + pinch
    full  = hand.full_state()         # + raw motor counts and per-finger resultant force (5.6)
    frame = hand.palm_frame()         # Stamped(ts_ns, (240, 320, 3) uint8)
```

Reading the hand is not a motion command: no hardware session is needed (R1, CLAUDE.md 4.6).

- **One read is one round trip.** There is no stream and no queue: `read_state()` performs a single
  synchronous `getJointPositionsAngle` and is stamped when the reply lands. `full_state()` costs two
  further round trips, so it is for the dataset and the probe, not for the 30 Hz path.
- **Units.** The SDK returns *normalised* angles; `HandState.joints_rad` is real radians, converted
  with `calculateRealAngle` on the connected slave (docs/sdks.md 4.6). That conversion needs the
  hand's hardware version, so it cannot be done off-device — `DexH15Kinematic.calculateRealAngle`
  rejects every vector length without one.
- **`HandState.pinch` is `nan` until T-020.** The scalar is the projection of the measured joints
  onto the synergy line of 5.4, and `config/hand.yaml` `pinch.open_pose` / `pinch.closed_pose` are
  still `UNMEASURED`. `nan` rather than `0.0`, which would read as "the hand is open";
  `DexH15.pinch_measurable` says which regime you are in.
- **The joint count is checked, not assumed.** `config/hand.yaml` `joint_order` is a hypothesis
  (`joint_order_status: UNMEASURED`), so a live `getJointPositionsAngle` whose length disagrees with
  it raises rather than being reshaped.
- **Unavailability.** An unconfigured port, an adapter that is not there, a slave that does not
  answer, anything the SDK throws, or a closed driver all raise `HandUnavailable` naming the port, so
  a read-only check skips instead of failing. Tests that need the hand are marked `readonly`.

**Device discovery** mirrors the cameras', on serial nodes: an explicit `port=`, then
`config/hand.yaml` `device.port` (a `/dev/serial/by-id/...` path), then the lowest-numbered
`/dev/ttyUSB*` or `/dev/ttyACM*` node whose USB `vendor:product` matches `device.usb_id`
(`067b:23a3`). Everything else — baud (4 000 000), slave address (`0x78`) — comes from the same file
and is still a placeholder: the hand has never been plugged in (agents/HARDWARE_NEEDED.md H-003).

### The palm camera

`PalmCamera` is the hand's built-in camera through `pxdex.dh15.DexH15Camera`, and satisfies the same
`CameraDriver` protocol as everything else: `(h, w, 3)` uint8 at `config/cameras.yaml`
`palm.policy_resolution` (320x240), stamped when `getFrame` returns. It needs **no Modbus link** —
the palm camera is an ordinary V4L2 node the hand's USB adds (docs/sdks.md 5.2) — and it is found by
the same `drivers.cameras.resolve_device` as the other streams, so an absent or unconfigured one
raises `CameraUnavailable` naming `palm.device`. `DexH15.palm_frame()` builds one **lazily**, on the
first call, and closes it with the hand. `make("palm", backend="real")` and `stream_stats --camera
palm --backend real` both go through it.


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
