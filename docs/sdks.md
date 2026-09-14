# SDK inventory (T-002)

Measured on this laptop, 2026-09-11, Ubuntu 22.04 / kernel 6.8.0-136, x86_64.
Every claim below is backed by a `path:line` reference that `tests/test_docs_sdks.py` checks.

Reference convention:

- repo-relative, backticked, e.g. `third_party/g1_pico_teleop/teleopit/constants.py:11` — also used for
  git-ignored paths that exist on disk inside the repo (`.venv/...`, the PyInstaller `_internal/` tree);
- absolute or `~`-prefixed for files outside the repo, e.g.
  `/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:160`.

**No device was connected during this task.** Packages were imported and their signatures read; no
Modbus port, no DDS participant and no robot connection was opened (R1).

---

## 0. Summary table

| Device | Package / route | State read | Target write | Status |
|---|---|---|---|---|
| G1 left arm (7) | `unitree_sdk2py` 1.0.1 (installed) or `g1_bridge_sdk` (C++ pybind, not built here) | `rt/lowstate` LowState_.motor_state[15..21].q | `rt/arm_sdk` (or `rt/lowcmd`) LowCmd_.motor_cmd[15..21].q | code-verified, link needs hardware |
| G1 waist yaw (1) | same | LowState_.motor_state[12].q | LowCmd_.motor_cmd[12].q | same; waist may be locked (see 3) |
| DexH15 (15 joints) | `pxdex` 3.2.1 (installed in venv) + system `dexhandsdk` 3.2.1 deb | `DexH15Control.getJointPositionsAngle` | `DexH15Control.setJointPositionsAngle` | import + `getSDKVersion()` verified |
| DexH15 palm camera | `pxdex.dh15.DexH15Camera`, or plain V4L2 | `getFrame()` | n/a | API verified, no hand connected |
| PxCap Pro glove | `pxhandsdk.pxcappro` (bundled only, no system deb) | `get_encoder_angles()` | n/a (input device) | route unresolved, see 6.1 |
| Pico controller pose | `pico_bridge` 0.2.1 (installed under the miniconda `teleopit` env, not in this venv) | `PicoBridge.wait_frame().controllers.left.pose` | n/a | **pose exists but Teleopit discards it — see 7** |
| Logitech Brio | OpenCV V4L2 | `cv2.VideoCapture.read()` | n/a | not connected at time of run |
| Orbbec Ego | UVC V4L2 works; `pyorbbecsdk` PyPI wheel is broken (see 8.2) | `cv2.VideoCapture.read()` | n/a | device present, enumerated |

---

## 1. Install attempts in `.venv` (Python 3.10.20, uv-managed)

| Attempt | Command | Outcome |
|---|---|---|
| pxdex (local wheel) | `uv pip install --python .venv/bin/python "third_party/dexh15_sdk/DexH15 SDK/pxdex-3.2.1-cp310-cp310-linux_x86_64.whl"` | **SUCCESS** — `pxdex==3.2.1`. `import pxdex.dh15` works and `DexH15Control().getSDKVersion()` returns `DexHandSDK_3.2.1` (no device touched). |
| unitree_sdk2py (GitHub, pinned) | `uv pip install --python .venv/bin/python "unitree_sdk2py @ git+https://github.com/unitreerobotics/unitree_sdk2_python@f7a55264759fe212b23911046a1a59cf13a8d5ea"` | **SUCCESS** — `unitree-sdk2py==1.0.1`, pulled `cyclonedds==0.10.2`, `opencv-python==5.0.0.93`, `rich`, `rich-click`, `click`, `markdown-it-py`, `mdurl`. Import of `unitree_sdk2py.idl.unitree_hg.msg.dds_.LowCmd_/LowState_` and `unitree_sdk2py.core.channel` verified. |
| unitree_sdk2py (GR00T tree commit) | same, `@1983e88888217f6c69283cf3a9d1af01e87f07af` | **FAILED**: `failed to find branch, tag, or commit 1983e888...` / `git rev-parse '1983e888...^0'` exit 128. That commit is local to `~/GR00T-WholeBodyControl/external_dependencies/unitree_sdk2_python` (a merge of a private `release/main`) and is not reachable on the public upstream. `f7a5526` (the commit that `~/meta-quest-teleoperate/unitree_sdk2_python` is checked out at, "Merge pull request #159", 2026-06-04) is public and was used instead. |
| pyorbbecsdk | `uv pip install --python .venv/bin/python pyorbbecsdk` | **INSTALLS BUT UNUSABLE.** `pyorbbecsdk==1.3.2` downloads and installs (65 MiB), then `import pyorbbecsdk` raises `ModuleNotFoundError: No module named 'pyorbbecsdk'`. Cause: the PyPI wheel is mis-tagged. Its `WHEEL` says `Tag: cp310-cp310-manylinux1_x86_64` but its `RECORD` contains only macOS artefacts: `pyorbbecsdk.cpython-311-darwin.so` and `libOrbbecSDK.*.dylib`. It was uninstalled again and is **not** in requirements.txt. |

Both cv2 distributions are now present (`opencv-python` pulled in by unitree_sdk2py, `opencv-python-headless`
pinned by T-001); they are the same upstream version 5.0.0.93 and `import cv2` resolves to 5.0.0. This is a
packaging smell, not a failure — flagged for Fable in `agents/BUILD_LOG.md`.

---

## 2. G1 left arm (7 DoF)

### 2.1 Package and install route

Two independent routes exist; both speak Unitree's `unitree_hg` DDS IDL on a wired Ethernet link.

**Route A (installed here): `unitree-sdk2py` 1.0.1**, from
`git+https://github.com/unitreerobotics/unitree_sdk2_python@f7a5526`, on `cyclonedds` 0.10.2.
Local reference checkouts (not installed): `~/meta-quest-teleoperate/unitree_sdk2_python` (at `f7a5526`)
and `~/GR00T-WholeBodyControl/external_dependencies/unitree_sdk2_python` (at `1983e88`, private merge).
The submodule slot inside the vendored teleop fork, `third_party/g1_pico_teleop/third_party/unitree_sdk2_python`,
is empty (D-003, Q-003).

**Route B (not built here): `g1_bridge_sdk`**, the C++ pybind DDS bridge the vendored teleop fork uses.
Source: `third_party/g1_pico_teleop/third_party/g1_bridge_sdk/src/g1_bridge.cpp:356`.
A **built** extension exists outside the repo, for two interpreters:
`/home/alois/Teleopit/third_party/g1_bridge_sdk/g1_bridge_sdk.cpython-311-x86_64-linux-gnu.so` and a cp310
build at `/home/alois/Teleopit/third_party/g1_bridge_sdk/build/lib.linux-x86_64-3.10/g1_bridge_sdk.cpython-310-x86_64-linux-gnu.so`
(so a 3.10 build is reproducible). It was **not** imported or copied in this task.

### 2.2 State read

- Subscribe: `ChannelSubscriber("rt/lowstate", LowState_)` —
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:111`
- Read call: `ChannelSubscriber.Read` — `.venv/lib/python3.10/site-packages/unitree_sdk2py/core/channel.py:292`
  (handler-based init at `.venv/lib/python3.10/site-packages/unitree_sdk2py/core/channel.py:283`)
- Field: `LowState_.motor_state[i].q` (and `.dq`, `.tau_est`) —
  `.venv/lib/python3.10/site-packages/unitree_sdk2py/idl/unitree_hg/msg/dds_/_LowState_.py:30`
- Bridge equivalent: `G1Bridge.get_state() -> (qpos[29], qvel[29], quat[4], ang_vel[3])` —
  `third_party/g1_pico_teleop/third_party/g1_bridge_sdk/src/g1_bridge.cpp:366`, filled from `motor_state()[i].q()`
  in the LowState callback at `third_party/g1_pico_teleop/third_party/g1_bridge_sdk/src/g1_bridge.cpp:254`;
  Python wrapper `UnitreeG1Robot.get_state` at `third_party/g1_pico_teleop/teleopit/sim2real/unitree_g1.py:63`.

### 2.3 Target write

- **Preferred for LUDO-G1: the `rt/arm_sdk` topic.** It commands only the arm and waist joints and leaves the
  robot's own controller in charge of everything else, which is exactly the arm-and-waist-only scope of this
  project. Publisher: `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:107`;
  the write itself (`crc` then `Write`) at
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:174`;
  per-joint target assignment `low_cmd.motor_cmd[joint].q/.dq/.kp/.kd` at
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:139`.
  **Enable/weight:** `motor_cmd[29].q = 1` (`kNotUsedJoint`) enables arm_sdk, `0` releases it —
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:135`.
  The joint set the example drives (both arms + the three waist joints) is listed at
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:91`.
- Full low-level alternative: `ChannelPublisher("rt/lowcmd", LowCmd_)` —
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/low_level/g1_low_level_example.py:101`.
  This commands all 29 joints and requires `mode_machine` echo
  (`/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/low_level/g1_low_level_example.py:138`).
  It is the more dangerous path (it owns the legs) and should not be the default for a rig-mounted robot.
- Write call: `ChannelPublisher.Write` — `.venv/lib/python3.10/site-packages/unitree_sdk2py/core/channel.py:271`
- Message field: `LowCmd_.motor_cmd` (35 slots) —
  `.venv/lib/python3.10/site-packages/unitree_sdk2py/idl/unitree_hg/msg/dds_/_LowCmd_.py:27`
- Mandatory checksum before every write: `CRC.Crc(msg)` —
  `.venv/lib/python3.10/site-packages/unitree_sdk2py/utils/crc.py:39`
- Bridge equivalent: `G1Bridge.set_target(target[29], kp[29], kd[29])` —
  `third_party/g1_pico_teleop/third_party/g1_bridge_sdk/src/g1_bridge.cpp:374`; Python wrapper
  `UnitreeG1Robot.send_positions` at `third_party/g1_pico_teleop/teleopit/sim2real/unitree_g1.py:112`.

### 2.4 Rates, units, joint order, partial commands

- **Rate.** The example's control loop runs at `control_dt_ = 0.02` s (50 Hz). The C++ bridge publishes LowCmd
  on its own thread at `DEFAULT_PUBLISH_HZ = 200` —
  `third_party/g1_pico_teleop/third_party/g1_bridge_sdk/src/g1_bridge.cpp:35`, loop at
  `third_party/g1_pico_teleop/third_party/g1_bridge_sdk/src/g1_bridge.cpp:273`. LowState arrives at the robot's
  own publish rate (nominally 500 Hz on the G1); **measured rate is UNMEASURED, Phase 1 (U1)**.
- **Units.** Joint positions `q` in radians, velocities `dq` in rad/s, torque `tau` in Nm, `kp`/`kd` are PD
  gains in the robot's units. IMU quaternion is `w,x,y,z`
  (`third_party/g1_pico_teleop/third_party/g1_bridge_sdk/src/g1_bridge.cpp:259`).
- **Joint order.** The canonical 29-joint order is
  `third_party/g1_pico_teleop/teleopit/constants.py:11`; the same order as integer indices is
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:19`.
  **The LUDO-G1 left arm is indices 15..21**: `left_shoulder_pitch, left_shoulder_roll, left_shoulder_yaw,
  left_elbow, left_wrist_roll, left_wrist_pitch, left_wrist_yaw`.
- **Limits** (from the G1 MJCF, radians):
  shoulder_pitch `[-3.0892, 2.6704]` (`/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:180`),
  shoulder_roll `[-1.5882, 2.2515]` (`/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:184`), shoulder_yaw `[-2.618, 2.618]` (`/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:188`),
  elbow `[-1.0472, 2.0944]` (`/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:193`),
  wrist_roll `[-1.97222, 1.97222]` (`/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:198`), wrist_pitch `[-1.61443, 1.61443]` (`/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:202`),
  wrist_yaw `[-1.61443, 1.61443]` (`/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:207`).
- **Partial commands: yes.** `rt/arm_sdk` is itself a partial-command channel — only the listed joints are
  written; the weight slot gates the whole thing. On `rt/lowcmd` every write carries all 35 motor slots, so
  "partial" there means writing the current measured `q` back into the joints you do not want to move
  (the pattern at
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:139`).

### 2.5 Network

Wired Ethernet, DDS domain bound to one interface name. The lab's documented addressing is laptop
`192.168.123.2/24`, robot `192.168.123.164` — `third_party/g1_pico_teleop/README.md:59`.
`ChannelFactoryInitialize(id, networkInterface)` at
`.venv/lib/python3.10/site-packages/unitree_sdk2py/core/channel.py:298` takes that interface name.
At the time of this run **no interface holds a 192.168.123.x address** (see the probe output in
`agents/BUILD_LOG.md`); the USB Ethernet dongle (`0b95:1790 ASIX AX88179`, `enx000ec6c10aa5`) is present but
down.

---

## 3. G1 waist

Same transport, same SDK, same message; only the index differs.

- **Index 12, `waist_yaw_joint`** — `third_party/g1_pico_teleop/teleopit/constants.py:11` (13th name) and
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:40`.
- State read: `LowState_.motor_state[12].q` via the subscriber in 2.2.
- Target write: `LowCmd_.motor_cmd[12].q` on `rt/arm_sdk` (the example includes `WaistYaw` in its driven set,
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:91`).
- Limit: `[-2.618, 2.618]` rad — `/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:160`.
  `waist_roll` (13) `[-0.52, 0.52]` (`/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:164`) and `waist_pitch` (14) `[-0.52, 0.52]`
  (`/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:168`) exist but LUDO-G1 uses yaw only (5.3).
- **Caveat carried over from the lab's history:** the waist can be *locked* in the robot's own configuration,
  in which case indices 13/14 (and sometimes 12) do not respond and it looks like a hardware fault. The SDK
  header itself marks 13/14 `INVALID for g1 23dof/29dof with waist locked` —
  `/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:40`.
  Check the lock state in the Unitree app before diagnosing hardware (Phase 1).

---

## 4. Paxini DexH15 (left hand, 15 joints)

### 4.1 Package and install route

- Python: `pxdex` **3.2.1**, cp310-only wheel at
  `third_party/dexh15_sdk/DexH15 SDK/pxdex-3.2.1-cp310-cp310-linux_x86_64.whl`. Installed into `.venv`
  in this task (section 1). Also present for the system interpreter at
  `/home/alois/.local/lib/python3.10/site-packages/pxdex/__init__.py:4`.
- Native: the `dexhandsdk` 3.2.1 Debian package is **already installed system-wide**
  (`dpkg -l | grep dexhandsdk` -> `ii dexhandsdk 3.2.1 amd64`; `/usr/lib/libdexhand_control_cpp.so.3.2.1`),
  which is why `import pxdex.dh15` resolves. Its source archive is
  `third_party/dexh15_sdk/DexH15 SDK/DexHandSDK-3.2.1-Linux.deb` (git-ignored, on disk).
- Install instructions (Chinese): `third_party/dexh15_sdk/DexH15 SDK/README_CN.md:58`.
- GUI: PaXini Hand Studio, an Electron app at `/opt/PaXini-Hand-Studio/pxdex-hand`. Not used programmatically.

### 4.2 Connection

Modbus RTU over a USB serial adapter at **4 000 000 baud**, slave address `0x78` by default:
`third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/position_control.py:27`
(port), `third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/position_control.py:29` (baud), `third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/position_control.py:31` (slave). Auto-connect variant:
`third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/connect_modbus_auto.py:20`.
The teleop bundle records the hand's USB id as `vid 0x067b pid 0x23a3`:
`third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:63`.
Mandatory bring-up order (open port -> `initModbusDevice` -> `initMotorPosition` -> `setMotorControlMode(POSITION_CONTROL_MODE)`
-> `enableMotor`) is spelled out at
`third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/position_control.py:8`.

### 4.3 State read

- **`DexH15Control.getJointPositionsAngle(slave_address) -> (ret, list[float])`** — all 15 normalised joint
  angles: `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:120`.
- Per-finger overload `getJointPositionsAngle(slave_address, finger_id)`:
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:122`.
- Raw motor positions: `getMotorPosition` — `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:130`;
  magnetic encoders: `getJointMagneticEncoder` — `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:118`;
  tactile: `getAllTactileData` — `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:106`.
- Chinese API table for the read calls: `third_party/dexh15_sdk/DexH15 SDK/README_CN.md:203`.

### 4.4 Target write

- **`DexH15Control.setJointPositionsAngle(slave_address, joint_angles) -> int`** (1 = ok) — all joints:
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:150`. Live example usage in the force-control loop:
  `third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/normal_force_control.py:106`.
- Alternative raw-motor path `setMotorTargetPosition(slave_address, Dex15MotorPosition)` (7 motors):
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:163`, example
  `third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/position_control.py:83`.

### 4.5 Partial joint commands (resolves U2 at the API level)

**Yes, the API has them.** Overloads that address a single finger or a single motor:

- `setJointPositionsAngle(slave_address, finger_id: FingerType, joint_angles)` —
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:152`
- `setMotorTargetPosition(slave_address, motor_id: FingerMotor, target_position)` —
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:165`
- `setMotorTargetVelocity(..., motor_id, ...)` — `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:169`;
  `setMotorTargetCurrent(..., motor_id, ...)` — `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:161`
- `FingerType` enum (thumb/index/middle/ring/pinky) —
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:261`; `FingerMotor` enum (8 motors incl. 3 thumb) —
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:227`
- Chinese doc confirming both a single-finger and an all-finger `setJointPositionsAngle`:
  `third_party/dexh15_sdk/DexH15 SDK/README_CN.md:192`

Whether the **firmware** accepts a partial write without disturbing the other fingers is **not** provable
without the hand: Phase 1 (B-risk noted in D-002 U2).

### 4.6 Rate, units, joint order

- **Rate.** UNMEASURED. The SDK's own force-control example sleeps 5 ms between writes
  (`third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/normal_force_control.py:114`), i.e. it is
  written as if ~200 Hz is attainable over the 4 Mbit/s Modbus link. The teleop bundle drives the hand from a
  50 Hz glove stream (`third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:8`).
  A3's ">= 30 Hz" is therefore plausible but needs a hardware measurement (Phase 1).
- **Units.** `getJointPositionsAngle` / `setJointPositionsAngle` work in *normalised* angle
  (归一角度); `DexH15Kinematic.calculateRealAngle` / `calculateNormalizeAngle` convert to and from real angles —
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:175`. Motor positions are raw integer counts
  (`third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/position_control.py:75`). Tactile force is in
  0.1 N units (`third_party/pxcap_pro_sdk.md:107`, same convention family).
- **Joint order (15 = 5 fingers x 3).** From the Paxini teleop bundle's own DexH15 description:
  per-finger joint names `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:80`,
  and the **control-interface finger order `[index, middle, ring, pinky, thumb]`** at
  `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:122`.
  Joint upper limits the bundle applies (thumb 1.91/1.51/2.09 rad, every other joint 1.39 rad) are at
  `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:197`.
  This ordering must be re-checked against the SDK's own slot order on hardware before `config/hand.yaml`
  is filled in (T-003 leaves it UNMEASURED); the bundle's own comment warns that the SDK slot order is not
  the URDF XML order —
  `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:3`.
  A DexH15 left URDF ships with the bundle at
  `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:70`
  (`resource/DexH15_left_urdf/urdf`).

---

## 5. DexH15 palm camera

### 5.1 Through the Paxini SDK

- Class `DexH15Camera` — `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:83`
- Connect: `connectCameraDevice(camera_port_num: str)` —
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:85`; example
  `third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/camera.py:19`
- Configure: `setCameraConfig(width, height, fps) -> bool` —
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:88`; the example uses **640x480 @ 30 fps**
  (`third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/camera.py:23`)
- **State read: `getFrame() -> numpy.ndarray`** —
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:86`; example
  `third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/python/camera.py:29`
- Release: `releaseCameraDevice()` — `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:87`
- No target-write call: it is a sensor.

### 5.2 Node naming

The camera enumerates as an ordinary V4L2 device. The SDK's own note: plugging it in adds **two** nodes
(e.g. `/dev/video2` and `/dev/video3`) and the **first** is the stream —
`third_party/dexh15_sdk/DexH15 SDK/linux_x86/dexh15/Readme.md:26`. So `drivers/dexh15.py` can either call
`DexH15Camera.getFrame()` or open the node with OpenCV; the OpenCV route keeps the palm camera on the same
timestamping path as the Brio and the Orbbec (`runtime/clock.py`, T-004) and is the recommended default.
Format and native resolution: **UNMEASURED**, needs the hand (Phase 1).

---

## 6. PxCap Pro glove

### 6.1 Package and install route — unresolved (Q-005)

The manual describes a `pxhandsdk` **Debian package** that installs headers at `/usr/include/pxhandsdk/`, the
shared library at `/usr/lib/pxhandsdk/`, and a Python binding importable as `from pxhandsdk import pxcappro`
— `third_party/pxcap_pro_sdk.md:72`. **That deb is not in the repo and is not installed on this laptop**
(`/usr/lib/pxhandsdk` does not exist).

What *is* present is the PyInstaller teleop bundle, which carries its own CPython 3.10 and the same binding:

- Python shim: `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/sdk_bridge/_internal/python3/pxhandsdk/dist-packages/pxhandsdk/pxcappro.py:7`
- Native binding: `pxcappro.cpython-310-x86_64-linux-gnu.so` and `libpxcappro_sdk.so.1.0.8` in the same
  `_internal` tree (git-ignored, on disk).
- Bundle usage and its read-only diagnostic mode:
  `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/README.md:71` (`./pxcap_pro_local --once --diagnose`
  reads the glove and computes targets but sends nothing).

So there are three possible routes for `drivers/pxcap.py`, in order of preference:
(a) Alois supplies the `pxhandsdk` deb and we import `pxhandsdk.pxcappro` in our own venv (clean);
(b) we add the bundle's `dist-packages` to `sys.path` with its `LD_LIBRARY_PATH` and import the cp310 `.so`
directly (works on paper — same ABI as our venv — untested, and it reaches into a vendored binary tree);
(c) we shell out to `pxcap_pro_local --diagnose` and parse stdout (worst).
**Decision needed from Fable/Alois; nothing was installed or imported from the bundle in this task.**

### 6.2 State read

- Session: `PxCapPro()` -> `connect_device('/dev/ttyACM0')` — `third_party/pxcap_pro_sdk.md:210` and
  `third_party/pxcap_pro_sdk.md:211`
- **Joint angles: `get_encoder_angles()` — 17 channels, unit degrees** — `third_party/pxcap_pro_sdk.md:311`
- Raw encoders: `get_encoder_raw_data()`; tactile: `get_sensor_data()` (example at
  `third_party/pxcap_pro_sdk.md:213`); all symbols are present in the bundled binding (verified with
  `strings` on `pxcappro.cpython-310-x86_64-linux-gnu.so`: `pxcappro_get_encoder_angles`,
  `pxcappro_start_collection`, `pxcappro_connect`, ...).
- Continuous capture: `start_collection()` / `stop_collection()` with a callback that carries encoders,
  joint angles, tactile and **two host-side nanosecond timestamps** — `third_party/pxcap_pro_sdk.md:312`.
  The manual is explicit that the timestamp is host frame-availability time, **not** device sample time
  (`third_party/pxcap_pro_sdk.md:62`) — this matters for `runtime/clock.py` latency compensation.
- No target write: the glove is an input device.

### 6.3 Rate, units, channel order

- **Rate:** configurable; the bundle's default input frequency is **50 Hz**
  (`third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:8`,
  and `--frequency N` at `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/README.md:149`).
  A4's ">= 30 Hz" holds at the configured default; measured jitter is Phase 1.
- **Units:** degrees (`third_party/pxcap_pro_sdk.md:107`).
- **Channel order (17):** `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:10`
  — 10 tip/pulp channels then 7 palm magnets; the mapping onto the glove URDF's named joints is at
  `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:31`.
- **No absolute wrist pose.** The capability table lists device session, device info, tactile, hand pose
  (17 encoders + 17 joint angles), continuous capture, diagnostics and firmware upgrade — and nothing else
  (`third_party/pxcap_pro_sdk.md:55`). **A4's second half is confirmed**: the wrist 6-DoF must come from the
  Pico controller sitting in the glove jig.
- Thumb-index pinch: the glove URDF gives the thumb 5 joints and each finger 3
  (`third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:132`), and
  the bundle's cartesian retargeting already computes thumb-tip-to-finger-tip key vectors
  (`third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:158`) —
  useful input for the pinch scalar of CLAUDE.md 5.4.

---

## 7. Pico controller pose — and the A5 finding

### 7.1 Where the 6-DoF pose actually is

The receiver package is **`pico_bridge` 0.2.1**, a wheel fetched from GitHub releases, declared as the
`pico4` extra of the vendored fork: `third_party/g1_pico_teleop/pyproject.toml:54`.
It is **not** vendored in this repo and **not** installed in this venv; the only installed copy on this
laptop is in the miniconda env used by `~/Teleopit`:
`/home/alois/miniconda3/envs/teleopit/lib/python3.11/site-packages/pico_bridge/frames.py:107`.

It *does* carry the controller pose:

- `Pose(position, rotation)` — **meters, quaternion xyzw** —
  `/home/alois/miniconda3/envs/teleopit/lib/python3.11/site-packages/pico_bridge/frames.py:107`
- `ControllerState.pose: Pose | None` plus `axis`, `buttons`, `raw` —
  `/home/alois/miniconda3/envs/teleopit/lib/python3.11/site-packages/pico_bridge/frames.py:137`
- parsed from the headset payload's `pose` field —
  `/home/alois/miniconda3/envs/teleopit/lib/python3.11/site-packages/pico_bridge/frames.py:294`
- frame-level metadata `coordinate_space="pico_native"`, `quat_order="xyzw"`, `units="meters"` —
  `/home/alois/miniconda3/envs/teleopit/lib/python3.11/site-packages/pico_bridge/frames.py:179`
- **state read call: `PicoBridge.wait_frame(timeout, after_seq) -> PicoFrame`** —
  `/home/alois/miniconda3/envs/teleopit/lib/python3.11/site-packages/pico_bridge/bridge.py:129`, or
  `latest_frame()` at
  `/home/alois/miniconda3/envs/teleopit/lib/python3.11/site-packages/pico_bridge/bridge.py:132`
- transport: TCP 63901 from the headset to the laptop, with UDP discovery —
  `third_party/g1_pico_teleop/README.md:67`. Input rate in the lab configs: **`pico_input_hz: 120.0`**
  (`third_party/g1_pico_teleop/teleopit/configs/sim2real.yaml:53`), policy consumption at 50 Hz
  (`third_party/g1_pico_teleop/teleopit/configs/sim2real.yaml:7`).
- No target write: input device.

### 7.2 Where left-arm joint references are produced in the vendored pipeline

`Pico4InputProvider` -> `RetargetingModule.retarget` (GMR/mink IK) -> 36-D `qpos` (7 root + 29 joints) ->
`extract_mimic_obs` -> ONNX RL whole-body policy -> `UnitreeG1Robot.send_positions`.

- Left-arm joint references appear **only** as elements 15..21 of the GMR output:
  `third_party/g1_pico_teleop/teleopit/retargeting/core.py:143` (`RetargetingModule.retarget -> qpos`), solved by
  mink at `third_party/g1_pico_teleop/teleopit/retargeting/gmr/motion_retarget.py:303`.
- Even the "arms only" mode keeps that structure: it splices the retargeted arm indices into an otherwise
  standing reference and still hands the result to the RL policy —
  `third_party/g1_pico_teleop/teleopit/runtime/arm_mocap.py:31` and
  `third_party/g1_pico_teleop/teleopit/runtime/arm_mocap.py:18` (default controlled indices `range(15, 29)`).

### 7.3 What input that IK needs

A **full human skeleton**, not a controller pose. The IK task table for the Pico source requires
`Pelvis, Left_Hip, Left_Knee, Left_Foot, Right_Hip, Right_Knee, Right_Foot, Spine3, Left_Shoulder,
Left_Elbow, Left_Wrist, Right_Shoulder, Right_Elbow, Right_Wrist` —
`third_party/g1_pico_teleop/teleopit/retargeting/gmr/ik_configs/pico_bridge_to_g1.json:24`
(pelvis/feet/knees carry the highest task weights, `third_party/g1_pico_teleop/teleopit/retargeting/gmr/ik_configs/pico_bridge_to_g1.json:73` and `third_party/g1_pico_teleop/teleopit/retargeting/gmr/ik_configs/pico_bridge_to_g1.json:122`). The provider builds that frame from the
headset's 24-joint body-tracking array and **rejects the frame outright if body tracking is inactive** —
`third_party/g1_pico_teleop/teleopit/inputs/pico4_provider.py:462`; joint name list at
`third_party/g1_pico_teleop/teleopit/inputs/pico4_provider.py:40`.
Body tracking on the PICO 4 needs the headset **plus two ankle motion trackers**
(`third_party/g1_pico_teleop/README.md:4`).

**And the provider throws the controller pose away.** `_read_controller_state` copies only `raw`, `grip`,
`trigger`, `axis_x`, `axis_y` into `PicoControllerState` and never touches `controller.pose` —
`third_party/g1_pico_teleop/teleopit/inputs/pico4_provider.py:634`, dataclass at
`third_party/g1_pico_teleop/teleopit/inputs/pico4_provider.py:59`, accessor at
`third_party/g1_pico_teleop/teleopit/inputs/pico4_provider.py:377`.

### 7.4 Verdict and the smallest alternative

**A5 as written is refuted.** There is no existing controller-pose-to-arm-joint IK. What exists is
full-body skeleton -> GMR/mink -> 29-DoF qpos -> RL whole-body tracking policy, which needs ankle trackers,
needs the legs free, and outputs a whole-body reference, not an arm target. Running it for LUDO-G1 would mean
a headset-worn operator with two ankle trackers standing in front of the robot for every one of the hundreds
of episodes, and a balancing policy commanding legs that are bolted to a rig.

**Smallest alternative (proposal only — not built in this task, per the task note).**
`teleop/retarget.py` reads `PicoBridge.wait_frame().controllers.left.pose` directly (position m, quat xyzw)
and solves its own IK for **8 joints only** (left arm 15..21 + waist yaw 12) against the G1 MJCF, using
`mink` on a model whose legs are pinned — the same solver the vendored stack already depends on, minus GMR,
minus the skeleton, minus the RL policy. Output goes to `rt/arm_sdk` through `runtime/safety.py`.
This needs: `pico_bridge` 0.2.1 installed in our venv (it is a pure-Python wheel, and the pose path is the one
already exercised by the headset app), `mujoco` + `mink` added to requirements, and the G1 MJCF vendored
(T-011 decides; the files are at `/home/alois/Teleopit/assets/robots/unitree_g1/g1_29dof.xml:1`, plus
`g1_29dof_dex3.xml`, `g1_29dof_neck_o6.xml`, `LICENSE`, `README.md` and a 5-subdirectory `meshes/` tree, none
of which are vendored here — `third_party/g1_pico_teleop/teleopit/runtime/assets.py:9` is the path the fork
expects and that path is empty in this repo).
It also removes the ankle trackers, the body-tracking dependency and the RL checkpoint from the critical path.

---

## 8. Cameras

### 8.1 Logitech Brio (`top`)

- **Not connected at the time of this run** (no Logitech USB id in the probe; the only integrated camera is
  `174f:11b4 SunplusIT Integrated RGB Camera` on `/dev/video0`).
- Route: plain V4L2 through OpenCV, already in requirements.
  State read: `cv2.VideoCapture.read() -> (ok, frame)` —
  `.venv/lib/python3.10/site-packages/cv2/__init__.pyi:4809`; class at
  `.venv/lib/python3.10/site-packages/cv2/__init__.pyi:4771`; resolution/fps via
  `VideoCapture.set(propId, value)` at `.venv/lib/python3.10/site-packages/cv2/__init__.pyi:4813`
  (`CAP_PROP_FRAME_WIDTH` at `.venv/lib/python3.10/site-packages/cv2/__init__.pyi:1565`).
  Open by index or path with `cv2.CAP_V4L2` — `.venv/lib/python3.10/site-packages/cv2/__init__.pyi:4791`.
- No target write (sensor).
- Node discovery: `tools/hardware_checks/list_devices.py:96` (`list_v4l2_devices`, VIDIOC_QUERYCAP, read-only).
- Rate/units: 4K@30 claimed by the vendor; **UNMEASURED here**. `config/cameras.yaml` (T-003) holds the
  placeholders; the `top` crop comes from T-008.

### 8.2 Orbbec Ego (`oblique`)

- **Connected and enumerated** during this run: USB `2bc5:1201 ORBBEC EGO`, exposing four V4L2 nodes —
  `/dev/video4` `ORBBEC: Ego left` (VIDEO_CAPTURE), `/dev/video5` (metadata), `/dev/video6`
  `ORBBEC: Ego right` (VIDEO_CAPTURE), `/dev/video7` (metadata). Full probe output is in
  `agents/BUILD_LOG.md`; the probe itself is `tools/hardware_checks/list_devices.py:96`.
  It is a **stereo UVC** device here, i.e. two rectilinear streams, not an RGB+depth pair.
- **No Orbbec SDK is present**: nothing under `third_party/`, no `libOrbbecSDK*` under `/usr/lib` or
  `/usr/local/lib`, and the PyPI `pyorbbecsdk` wheel is broken (section 1).
- **Working state read today: `cv2.VideoCapture("/dev/video4").read()`** —
  `.venv/lib/python3.10/site-packages/cv2/__init__.pyi:4809`. This gives RGB/mono frames at whatever the UVC
  descriptor offers; it gives **no depth**.
- No target write (sensor).
- Options for depth, in order of cost, for Fable to pick from (none actioned here): (a) accept RGB-only from
  the Ego and drop the depth cue — CLAUDE.md 3.1 only asks the Orbbec for "depth cues" as a stand-in for the
  missing head camera, and an oblique RGB view already provides parallax; (b) build `pyorbbecsdk` from
  Orbbec's GitHub source against the matching OrbbecSDK release (C++ toolchain + pybind11, ~30 min, pins us to
  a specific SDK version); (c) compute stereo depth from the two Ego streams with OpenCV, no new dependency.
- **Nodes** (this unit, serial `AZER76400HV`): left `/dev/v4l/by-path/pci-0000:00:14.0-usb-0:1:1.0-video-index0`
  → `/dev/video4` `ORBBEC: Ego left`; right `...-usb-0:1:1.2-video-index0` → `/dev/video6` `ORBBEC: Ego right`.
  These are **by-path, not by-id, on purpose**: udev gives *both* UVC functions the one name
  `/dev/v4l/by-id/usb-ORBBEC_EGO_ORBBEC_AZER76400HV-video-index0`, and that single link pointed at the left
  node during T-010 (2026-09-11) and at the right node during T-046 (2026-09-14), so a config on the by-id
  path would silently swap the two cameras (T-046, D-024, `config/cameras.yaml`).
- Rate/units/resolution: **MEASURED (T-046)**. The device negotiates **1600x1200 @ 30 fps MJPG whatever is
  requested** (320x240, 640x480, 1280x720 and 1600x1200 all come back 1600x1200, T-010); frames are BGR uint8,
  downscaled 2.5x to the 640x480 the policy sees. Two 600 s read-only runs
  (`stream_stats.py --backend real --stream oblique --seconds 600 --json`) gave **30.00 Hz achieved** (0.0% off
  nominal) over 17948 and 17955 frames, with **222 and 207 dropped frames (~1.2%)**, interval p50 33.36 ms /
  p99 51.2 ms / max 67.8 ms, and **jitter p50 4.9 ms, p99 18.3 ms, max 34.4 ms**. The drops and the jitter are
  **worse than T-010 measured on 2026-09-11** (0 drops, p99 1.4-2.9 ms on 10 s) and reproduce today at 10 s and
  30 s on an idle host; cause unknown, H-005. The UVC interface exposes **no exposure or frame-rate control**
  (`v4l2-ctl --list-ctrls`: brightness, contrast, saturation, hue, auto white balance only), so the exposure
  time cannot be pinned to rule auto-exposure in or out from here. Full numbers: `agents/BUILD_LOG.md` T-046.

---

## 9. Verdicts on D-002 assumptions and unknowns

- **A1 (Python >= 3.10): CONFIRMED, and correctly narrowed to exactly 3.10.** The DexH15 wheel is cp310-only
  (`third_party/dexh15_sdk/DexH15 SDK/pxdex-3.2.1-cp310-cp310-linux_x86_64.whl`) and installed cleanly into
  the uv-managed CPython 3.10.20 venv; the PxCapPro manual independently requires CPython >= 3.10 and < 3.11
  (`third_party/pxcap_pro_sdk.md:122`). `pxdex` imports and answers `getSDKVersion()` in this venv.
- **A2 (Unitree SDK2 over DDS on wired Ethernet): CONFIRMED in code, LINK NEEDS HARDWARE.** The SDK is
  CycloneDDS bound to a named interface (`.venv/lib/python3.10/site-packages/unitree_sdk2py/core/channel.py:298`)
  publishing `rt/lowcmd`/`rt/arm_sdk` and subscribing `rt/lowstate`
  (`/home/alois/meta-quest-teleoperate/unitree_sdk2_python/example/g1/high_level/g1_arm7_sdk_dds_example.py:107`),
  with the documented 192.168.123.0/24 wired LAN (`third_party/g1_pico_teleop/README.md:59`). No interface
  currently holds a 192.168.123.x address, so the link itself is unverified (H-002).
- **A3 (Paxini SDK: DexH15 joint position control at >= 30 Hz, and the palm camera): PARTIALLY CONFIRMED.**
  Position control API and palm-camera API both exist and the module imports
  (`.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:150`,
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:86`). **The rate claim needs hardware** (section 4.6).
- **A4 (PxCap Pro: finger joint angles at >= 30 Hz, no absolute wrist position): CONFIRMED on both halves,
  rate at the configured default.** 17 joint angles in degrees via `get_encoder_angles`
  (`third_party/pxcap_pro_sdk.md:311`), continuous capture with host ns timestamps
  (`third_party/pxcap_pro_sdk.md:312`), default 50 Hz
  (`third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/config/pxcap_pro_left_dexh15.yaml:8`); the
  capability table contains no wrist pose (`third_party/pxcap_pro_sdk.md:55`). *Caveat:* the delivery route
  into our venv is unresolved (section 6.1, Q-005).
- **A5 (the Pico pipeline already produces G1 arm joint targets from controller pose through IK): REFUTED.**
  See section 7. The pipeline is full-body skeleton -> GMR/mink -> 29-DoF qpos -> RL whole-body policy
  (`third_party/g1_pico_teleop/teleopit/retargeting/core.py:143`,
  `third_party/g1_pico_teleop/teleopit/retargeting/gmr/ik_configs/pico_bridge_to_g1.json:24`), it requires
  ankle trackers and active body tracking
  (`third_party/g1_pico_teleop/teleopit/inputs/pico4_provider.py:462`), and it discards the controller pose
  entirely (`third_party/g1_pico_teleop/teleopit/inputs/pico4_provider.py:634`). The 6-DoF controller pose
  does exist one layer below, in `pico_bridge`
  (`/home/alois/miniconda3/envs/teleopit/lib/python3.11/site-packages/pico_bridge/frames.py:137`), so the
  smallest fix is our own controller-pose -> 8-joint mink IK (section 7.4).
- **A6 (Greennode gives a Linux GPU VM over SSH with rsync): NOT VERIFIED — needs a human.** No credentials at
  `~/.config/ludo-g1/env` (Q-001). Nothing in this task could test it.
- **A7 (the engine exposes an interface later; `engine/stub.py` stands in): ADOPTED, nothing to verify.** No
  engine code exists in or under `third_party/`; T-007 builds the stub against the CLAUDE.md 5.5 contract.
- **U2 (DexH15 partial joint commands): CONFIRMED AT THE API LEVEL, FIRMWARE NEEDS HARDWARE.** Single-finger
  and single-motor overloads exist (`.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:152`,
  `.venv/lib/python3.10/site-packages/pxdex/dh15.pyi:165`) and the Chinese API table lists both variants
  (`third_party/dexh15_sdk/DexH15 SDK/README_CN.md:192`). Whether the firmware honours a partial write without
  moving the other fingers is a Phase 1 bench test.

---

## 10. What still blocks or needs a human

- **H-002 (new):** connect the robot LAN and confirm `192.168.123.x` (see `agents/HARDWARE_NEEDED.md`).
- **Q-005 (open, sharpened):** the `pxhandsdk` deb for the glove, or permission to import the bundle's
  cp310 binding from `third_party/` (section 6.1).
- **Q-003 (answerable now):** `unitree_sdk2py` was installed from upstream GitHub at a pinned commit and
  works; the `g1_bridge_sdk` route stays as the fallback and does not need building for Phase 0.
- **New question for Fable (Q-008 suggested):** the Orbbec has no usable Python SDK on PyPI; pick (a) RGB-only
  over UVC, (b) build `pyorbbecsdk` from source, or (c) OpenCV stereo depth (section 8.2).
