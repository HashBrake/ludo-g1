# G1 PICO Teleop — full-body teleoperation of a Unitree G1 with a PICO 4

Drive a **Unitree G1 EDU (29 DoF)** humanoid with your own body: a **PICO 4
headset + two PICO Motion Trackers** on the ankles stream your skeleton to a
laptop, which retargets it to the robot and runs a whole-body tracking policy
at 50 Hz. Arms, waist, squats, steps and walking are all mirrored while the
robot balances itself.

This repository is the stack that actually runs the robot: a fork of
[BotRunner64/Teleopit](https://github.com/BotRunner64/Teleopit) (base commit
`f926386`, v0.5.0) plus our lab commits on top (waist unlock, stability tuning,
stick-driven walking mode, session launcher, robot-side networking). The
upstream README is kept as [`README.upstream.md`](README.upstream.md) and the
upstream docs (BVH playback, training, LinkerHand, OpenNeck, recording) live in
[`docs/docs/`](docs/docs/).

> Previous history: the project first ran on HoloMotion and briefly prepared a
> SONIC migration. That whole tree, with its audits and runbooks, is preserved
> on branch **`archive/holomotion-2026-08-18`** of this repo. The two
> hand-off documents that still matter are copied under
> [`lab/history/`](lab/history/).

This README is written as a hand-off: a new engineer with the hardware in
front of them should be able to go from an empty laptop to a live session
using only this file and the files it links.

---

## Contents

1. [Hardware and network](#1-hardware-and-network)
2. [Safety rules](#2-safety-rules)
3. [One-time setup](#3-one-time-setup)
   — [3.1 laptop](#31-laptop-software) · [3.2 robot LAN](#32-laptop-robot-lan)
   · [3.3 Orin: Wi-Fi AP + relay](#33-robot-onboard-computer-orin-wi-fi-ap-and-discovery-relay)
   · [3.4 headset + trackers](#34-pico-4-headset-and-motion-trackers)
   · [3.5 Unitree remote + app](#35-unitree-remote-and-unitree-app-waist-lock)
   · [3.6 per-operator calibration](#36-per-operator-calibration)
4. [Practice in simulation](#4-practice-in-simulation-no-robot)
5. [Real robot session, end to end](#5-real-robot-session-end-to-end)
6. [Moving around: walking and joystick navigation](#6-moving-around-walking-and-joystick-navigation)
7. [Controls reference](#7-controls-reference) — Unitree remote, PICO controllers, keyboard
8. [Configuration and tuning](#8-configuration-and-tuning)
9. [What our commits change vs upstream](#9-what-our-commits-change-vs-upstream)
10. [Troubleshooting](#10-troubleshooting)
11. [Beyond teleop: onboard deployment, recording, training, docs, tests](#11-beyond-teleop)
12. [Repository layout](#12-repository-layout)

---

## 1. Hardware and network

| Item | Details |
|---|---|
| Robot | Unitree G1 EDU Ultimate, 29 DoF. **Remove the Inspire hands** for full-body sessions (policy trained without distal hand mass). |
| Robot onboard PC | Jetson Orin NX (JetPack 5.1), `ssh unitree@192.168.123.164`, Unitree factory credentials (password `123`). Hosts the `g1-teleop` Wi-Fi AP and the discovery relay. |
| Operator rig | PICO 4 headset (PicoBridge app v0.2.1, `com.picobridge.app`) + 2 PICO Motion Trackers on the ankles. A waist tracker exists but is deliberately unused. |
| Laptop | Ubuntu 22.04, Intel Core Ultra 5, 22 GB RAM, **no NVIDIA GPU**. Runs Teleopit on CPU (torch CPU, ONNX Runtime, MuJoCo). |
| Robot LAN | Ethernet, laptop `192.168.123.2/24`, robot `192.168.123.164`. NetworkManager profiles `robot-lan` (built-in port) and `robot-lan-usb` (USB dongle). |
| Unitree remote | The stock G1 handheld remote. Read by Teleopit through the robot's `LowState.wireless_remote` field. The spotter holds it at all times. |
| Safety rig | Gantry/tether + a human spotter holding the Unitree remote. **Mandatory.** |

```
                 Wi-Fi "g1-teleop" / teleop12345                Ethernet 192.168.123.0/24
 PICO 4 + trackers ───────────────────────► Orin 10.42.0.1 (NAT) ─────────────────────► Laptop 192.168.123.2
 PicoBridge app                             discovery_relay.py                          Teleopit (this repo)
 (auto-connects)  ◄──── UDP 29888 "192.168.123.2|63901" ◄──┘                            TCP 63901 receiver
                                                                                              │ DDS LowCmd 50 Hz
                                                                                              ▼
                                                                                    G1 motors (via g1_bridge_sdk)
```

Network rules that cost real days when broken:

- **The laptop never joins the `g1-teleop` Wi-Fi.** It has no internet and
  NetworkManager will flap between it and your internet SSID. Laptop Wi-Fi
  stays on the internet network; the headset reaches the laptop through the
  Orin's NAT.
- The headset must **forget every other Wi-Fi** (office, phone hotspot).
  `g1-teleop` has no internet, so the headset otherwise hops away mid-session
  and tracking dies. USB tethering to a phone does not carry the body stream.
- The XRoboToolkit PC service (`holosim-pcservice`, a systemd *user* unit left
  from the previous stack) squats TCP 63901. `go.sh` stops it; if you launch
  by hand run `systemctl --user stop holosim-pcservice` first. On a fresh
  laptop this unit does not exist and nothing needs doing.
- One commander at a time. If an onboard Teleopit session runs on the Orin,
  the laptop must not start another; `go.sh` checks and refuses.

## 2. Safety rules

1. **Gantry + spotter with the Unitree remote in hand for every powered
   session.** There is no software e-stop you should rely on.
2. **The remote is the e-stop.** Under Teleopit, **L1+R1 = DAMPING** from any
   mode. The robot goes limp immediately, which is a fall if untethered.
   SDK-side damping from the laptop has failed at real emergencies; do not
   build your plan around it.
3. The factory chord **L2+B does nothing while Teleopit commands the robot.**
   Only the Teleopit map in section 7 applies during a session.
4. Software fallback: `bash lab/teleopit_estop.sh` kills the command stream;
   the firmware releases the motors in about 2 s (proven). Tether required.
5. First response to weird motion that is not yet dangerous: **X** (back to
   the self-balancing STANDING pose). Escalate to L1+R1 only if needed.
6. Enter MOCAP only when you stand still in a neutral pose facing the same
   way as the robot. Start with small, slow movements. Never mash buttons.
7. Watch waist_pitch torque and temperature on the first sessions after any
   config change (it once hit a 130 °C fault under the old stack).
8. Nobody inside the robot's reach while it is powered. The operator wearing
   the headset cannot see well; the spotter watches the robot, not the operator.

## 3. One-time setup

### 3.1 Laptop software

```bash
sudo apt install -y git build-essential cmake adb            # adb is for the headset APK
git clone --recurse-submodules git@github.com:mizuharaa/g1-pico-teleop.git Teleopit
cd Teleopit

# Python env (conda; py3.11 is what we run, py3.10+ works)
conda create -n teleopit python=3.11 -y
conda activate teleopit
pip install --upgrade pip
pip install -e '.[pico4]'          # Teleopit + pico-bridge 0.2.1 + sim2real runtime
pip install modelscope
python scripts/setup/download_assets.py --only robots gmr ckpt bvh
#   -> assets/robots/unitree_g1/g1_29dof.xml, ckpt/track_g1.onnx, GMR assets, a sample BVH
#   (--source huggingface if ModelScope is unreachable)

# C++ DDS bridge to the G1 (needs Eigen headers; on this laptop conda's were used)
conda install -c conda-forge eigen -y
CPLUS_INCLUDE_PATH=$CONDA_PREFIX/include bash scripts/setup/setup_g1_bridge.sh

# checks
python -c "import teleopit; print('teleopit OK')"
python -c "from pico_bridge import PicoBridge; print('Pico OK')"
python -c "import g1_bridge_sdk; print('bridge OK')"
python scripts/run/run_sim.py controller.policy_path=ckpt/track_g1.onnx \
    input.bvh_file=data/sample_bvh/aiming1_subject1.bvh      # MuJoCo window, robot follows a BVH clip
```

Full upstream install matrix (uv/venv, training, review extras):
[`docs/docs/getting-started/installation.md`](docs/docs/getting-started/installation.md).

### 3.2 Laptop robot LAN

The G1 is a plain 192.168.123.0/24 network. Give the laptop a static address
on the port you plug into the robot (built-in port shown; for a USB dongle use
its `enx…` name from `ip -br link`):

```bash
nmcli con add type ethernet ifname enp0s31f6 con-name robot-lan \
    ipv4.method manual ipv4.addresses 192.168.123.2/24 connection.autoconnect yes
nmcli con up robot-lan
ping -c 2 192.168.123.164          # the Orin answers once the robot is booted
```

`go.sh` auto-detects whichever interface owns `192.168.123.x`; manual
commands take `real_robot.network_interface=<ifname>`. Dongle names change
with the adapter, so never hard-code them in scripts.

### 3.3 Robot onboard computer (Orin): Wi-Fi AP and discovery relay

Once per robot. Details and checks in [`lab/orin/README.md`](lab/orin/README.md).

```bash
ssh unitree@192.168.123.164        # password: Unitree default (123); answer "1" to the ROS-version prompt

# a) robot-hosted Wi-Fi for the headset (NetworkManager "shared" mode = DHCP + NAT for 10.42.0.0/24)
sudo nmcli device wifi hotspot ifname wlan0 ssid g1-teleop password teleop12345
sudo nmcli con modify Hotspot-1 connection.autoconnect yes connection.autoconnect-priority 10

# b) discovery relay so the headset finds the laptop through the NAT
exit
scp lab/orin/discovery_relay.py unitree@192.168.123.164:~/discovery_relay.py
ssh unitree@192.168.123.164 'chmod +x ~/discovery_relay.py; (crontab -l 2>/dev/null; echo "@reboot /usr/bin/python3 /home/unitree/discovery_relay.py >> /home/unitree/discovery_relay.log 2>&1") | crontab -; nohup python3 ~/discovery_relay.py >> ~/discovery_relay.log 2>&1 &'
```

Why the relay: pico-bridge's own discovery broadcast runs on the laptop and
cannot cross the Orin's NAT; its `--bridge-advertise-ip` option assumes the
advertised host shares a /24 with the headset. The relay just rebroadcasts
`192.168.123.2|63901` into `10.42.0.255:29888` every 2 s.

### 3.4 PICO 4 headset and motion trackers

1. **Headset system.** Update to the latest PICO OS (full-body tracking needs
   a recent one). Enable developer mode: Settings → General → About →
   tap the software version several times → Developer → enable USB debugging.
2. **Install PicoBridge.** USB-C to the laptop, accept the debugging prompt in
   the headset, then:
   ```bash
   curl -L -o PicoBridge_v0.2.1.apk \
     https://github.com/BotRunner64/pico-bridge/releases/download/v0.2.1/PicoBridge_v0.2.1_20260522_release.apk
   adb devices                         # must list the headset
   adb install PicoBridge_v0.2.1.apk
   ```
   The APK (60 MB) is not tracked in this repo because CI rejects files over
   2 MiB. The exact file we run is also recoverable from git history:
   `git show f29398a:PicoBridge_v0.2.1.apk > PicoBridge_v0.2.1.apk`
   (md5 `1537e2959b9f53e2feeed7957023b945`).
3. **Pair the trackers.** Headset Settings → Devices → Motion Tracker → pair
   both pucks. Strap them to the **ankles**, LED facing outward, not covered
   by trousers, in good light (the headset cameras must see them).
4. **Full-body tracking.** In the headset's motion-tracking settings enable
   full-body tracking and run the calibration when asked. Stand neutral and
   still. If you take the headset off and put it back on, the calibration is
   invalid: redo it.
5. **Wi-Fi.** Join **`g1-teleop`** (password `teleop12345`), then forget all
   other networks. A phone hotspot with internet was needed exactly once for
   the PicoBridge entitlement check; it is cached now. If PicoBridge ever
   refuses to start after a factory reset, give it internet once.
6. **Open PicoBridge.** The UI is status-only (one slider, nothing else to
   click). It auto-connects when it hears the discovery broadcast and flips to
   connected a few seconds after the laptop runtime is up. Headset video is
   optional and off by default; see section 8.

Trackers go to sleep when still for a while. If the runtime says
*"no body tracking data (wake the ankle trackers)"*, walk a couple of steps and
press Y again.

### 3.5 Unitree remote and Unitree app (waist lock)

- The remote is the stock G1 handheld. Charge it, power it on with the robot;
  it pairs automatically. Teleopit reads its buttons from the robot's low-level
  state, so the Teleopit button map only works while a Teleopit runtime is
  running; outside a session only the factory chords work.
- **Waist lock.** The G1 ships with a waist lock: a setting in the Unitree
  mobile app (device settings of the G1) plus physical fastener screws on the
  waist. With it engaged, waist_pitch cannot move and the policy fights the
  joint ("jam", 33 Nm stall, fault 512, heat). It was removed on 2026-08-17
  and `teleopit/configs/robot/g1.yaml` now uses the full waist action scale.
  On any new robot: check the app setting and the screws before the first
  session. If the waist ever stalls again, that is the first thing to check;
  the software rollback is `action_scale[14]: 0.0` in that file.

### 3.6 Per-operator calibration

- Operator height: `human_height` in `teleopit/configs/input/pico4.yaml`
  (1.74 m for the current pilot). Override without editing:
  `input.human_height=1.82` on any command line. Wrong height shows up as a
  robot that crouches or tiptoes in MOCAP.
- Standing pose: the reference is anchored to where the operator stands when
  **Y** is pressed. Each STANDING → MOCAP entry re-anchors, so you may turn to
  a new heading in STANDING and re-enter.
- Do the headset's own body calibration once per wearing (section 3.4).

## 4. Practice in simulation (no robot)

Do this until it is boring. Same headset, same network path, same code; only
the robot is MuJoCo. The laptop must still be on the robot LAN through the
Orin for the headset to reach it (or put the headset and laptop on any common
Wi-Fi and let pico-bridge's own discovery work, which is how upstream runs it).

```bash
conda activate teleopit && cd ~/Teleopit
systemctl --user stop holosim-pcservice        # only on the original laptop

# (optional) prove the headset stream arrives
python scripts/dev/test_pico_bridge.py --no-video

python scripts/run/run_sim.py --config-name pico4_sim controller.policy_path=ckpt/track_g1.onnx
```

The sim starts in `STANDING`. With the MuJoCo window focused, keyboard **Y**
enters MOCAP (stand still, neutral pose first), **X** returns to STANDING,
**Q** quits. PICO controller **A** pauses/resumes, **B** toggles ARMS mode.
`viewers=sim2sim` shows only the physics view; `viewers=none` is headless.
The joystick walking mode of section 6 is implemented for the real robot only;
in sim you walk with your body.

Robot-side rehearsals that read real state but send **no** motor commands:

```bash
python scripts/run/standalone_standing.py --policy ckpt/track_g1.onnx --network-interface <ifname> --dry-run
```

## 5. Real robot session, end to end

### Pre-flight (spotter + operator)

- [ ] Robot hanging on the gantry, feet just touching the floor, hands removed.
- [ ] Ethernet from laptop to robot; `ip -br -4 addr` shows `192.168.123.2`; `ping 192.168.123.164` answers.
- [ ] Laptop Wi-Fi on the internet SSID, **not** `g1-teleop`.
- [ ] Unitree remote on and in the spotter's hand. Spotter knows: **X** then **L1+R1**.
- [ ] Headset charged, trackers on ankles and awake, headset on `g1-teleop`, body calibration done.
- [ ] Clear floor around the robot; nobody in the swing radius.

### Step 1 — Power the robot and release factory control

Boot the robot as usual (it comes up in the factory controller, damped/zero
torque, hanging). Wait for the boot to finish, then on the Unitree remote press
**L2+R2** (debug/dev mode). This stands the factory controller down so
Teleopit's low-level commands are accepted. Do not press any factory stand-up
chord.

### Step 2 — Start the runtime on the laptop

```bash
conda activate teleopit
bash ~/Teleopit/go.sh
```

`go.sh` stops the port squatter, kills stale Teleopit processes, refuses to run
if an onboard session is active, detects the robot interface, launches
`run_sim2real.py` with `pico4_sim2real` and waits for `robot control ready`.
Log: `/tmp/session_monitor/sim2real.log` (`tail -f` it in a second
terminal). Equivalent manual command:

```bash
python scripts/run/run_sim2real.py --config-name pico4_sim2real \
    controller.policy_path=ckpt/track_g1.onnx real_robot.network_interface=<ifname>
```

The robot is now in `IDLE`: state is read, nothing is commanded.

### Step 3 — Headset connects

Put the headset on, open PicoBridge if it is not open. Within ~5 s the app
shows connected and the laptop log starts printing body frames. Operator stands
where they will teleop, facing the same direction as the robot.

### Step 4 — Stand (Unitree remote **Start**)

Press **Start**. Log: `Start -> STANDING`. Teleopit locks the current joints,
then ramps PD gains to the standing policy over 2 s. The robot balances itself
in `STANDING`. Lower the gantry so the feet carry the weight while the tether
stays slack-but-ready. Let it settle.

### Step 5 — Enter teleop (Unitree remote **Y**)

Operator stands still, neutral pose, aligned with the robot. Spotter presses
**Y**. Teleopit verifies 10 consecutive valid PICO frames, then blends the joint
targets from the standing pose into the operator's pose over 1 s. Log:
`Y -> MOCAP`. If it says `no body tracking data`, wake the trackers and press
Y again.

Begin with small, slow movements: arms, then torso, then weight shifts, then
steps in place, then walking. One new thing at a time.

### Step 6 — During the session

| Want to | Do |
|---|---|
| Freeze the robot in its current pose (re-strap a tracker, rest) | Unitree remote **B** or PICO **A** = pause. Same button resumes. Resume standing still and close to the held pose. |
| Only arms follow, legs/waist hold the standing pose | PICO **B** toggles `ARMS` mode. |
| Move the robot further than the tracking space allows | PICO **right-stick click** → `JOYSTICK` mode, section 6. |
| Unexpected motion, not yet dangerous | Remote **X** → STANDING (fast 0.5 s gain ramp back to the standing policy). |
| Emergency | Remote **L1+R1** → DAMPING, robot goes limp on the tether. Log: `EMERGENCY STOP (L1+R1)`. |

If the PICO stream stops, the robot holds the last reference; it does not
change mode on its own. Use **X**, or **L1+R1** if needed.

### Step 7 — End the session

1. Remote **X** → STANDING. Let it settle.
2. Take the robot's weight on the gantry.
3. Remote **L1+R1** → DAMPING (robot limp on the tether), or `Ctrl+C` /
   `bash lab/teleopit_estop.sh` on the laptop. From DAMPING, **Start** stands
   it up again if you want another round.
4. Stop the laptop runtime (`pkill -f run_sim2real.py`), power the robot down
   per the Unitree procedure. Charge headset, trackers and remote.

## 6. Moving around: walking and joystick navigation

There are two ways to move the robot through space.

**A. Body walking (MOCAP).** Walk in the tracking space; the robot walks the
same displacement, scaled by `root_xy_gain` (1.25: it overshoots you by about a
quarter, on purpose). Foot lifts are scaled by `foot_z_gain` (1.6) so light
steps are not absorbed by the policy. Limits: your room, the tether, and the
headset's tracking volume. Turning is real turning.

**B. Joystick navigation (JOYSTICK mode, real robot only).** Added in our
commits for walking beyond the room. The robot follows a velocity command
synthesised at 50 Hz from the PICO controller sticks while the operator
stands still.

| Action | Control |
|---|---|
| Enter | PICO **right-stick click** from `STANDING` or `MOCAP`. From MOCAP the robot first goes to STANDING, settles 1.5 s, then enters JOYSTICK. |
| Forward / back | Left stick up / down, capped at `vx_max` 0.9 m/s |
| Turn | Right stick left / right, capped at `wz_max` 1.1 rad/s |
| Strafe | **Squeeze right grip** = strafe right, **left grip** = strafe left, capped at `vy_max` 0.55 m/s. (The app never sends left-stick X or right-stick Y, hence the grips.) |
| Stop moving | Release everything; deadzone 0.12 |
| Back to standing | Remote **X** or stick-click |
| Back to body teleop | Remote **Y** (or stick-click): robot goes to STANDING, settles 1.5 s, log says `ALIGN YOUR BODY to the robot, then press Y for teleop`. Operator aligns, spotter presses **Y** again. Auto-entry was removed after it caused falls (pilot still in driving posture). |

Tune under `joystick:` in `teleopit/configs/pico4_sim2real.yaml`. Start low
when the floor or the tether changes. The log prints
`joystick axes L(..) R(..) -> v(..) w(..)` every 2 s so you can confirm the
sticks are read. Alignment and accumulated yaw are reset on every JOYSTICK
exit; this fixed a collapse caused by a stale yaw on mode swap.

## 7. Controls reference

### Unitree remote (Teleopit state machine, real robot)

| Button | From | Effect |
|---|---|---|
| **L2+R2** | factory controller | Debug/dev mode: releases factory control so Teleopit can command. Once, after boot. |
| **Start** | IDLE, DAMPING | → STANDING (joint lock, then 2 s Kp ramp into the standing policy). |
| **Y** | STANDING | → MOCAP after 10 valid PICO frames (1 s joint blend-in). Refused with a log message if tracking is not valid. |
| **Y** | JOYSTICK | → STANDING, 1.5 s settle, then waits for an explicit second **Y**. |
| **X** | MOCAP, ARMS, JOYSTICK | → STANDING (0.5 s fast ramp). Also cancels a pending settle. |
| **B** | MOCAP, ARMS | Pause / resume (reference held). |
| **L1+R1** | any | **EMERGENCY DAMPING.** Motors go to damping (kd 8), robot collapses onto the tether. |
| L2+B, Select, A | — | Not used by Teleopit. L2+B (factory damp) is dead while Teleopit runs. |

State machine diagram: [`docs/static/img/diagrams/pico-g1-state-machine.svg`](docs/static/img/diagrams/pico-g1-state-machine.svg).

### PICO 4 controllers (`teleopit/configs/input/pico4.yaml`)

| Control | Effect |
|---|---|
| **A** | Pause / resume the current MOCAP or ARMS session (sim and real). |
| **B** | Toggle ARMS mode (arms follow, body holds standing pose) (sim and real). |
| **Right stick click** | Toggle JOYSTICK walking mode (real robot only). |
| Left stick up/down | JOYSTICK: forward / back. |
| Right stick left/right | JOYSTICK: turn. |
| Right grip / left grip | JOYSTICK: strafe right / left. |
| Index triggers | Only used when LinkerHand `hands.mode=gripper` is enabled (not in our setup). |

### Keyboard (simulation only, MuJoCo window focused)

**Y** MOCAP · **X** STANDING · **A** pause/resume · **Q** quit. The Unitree
remote is not read in simulation.

### Factory controller (no Teleopit running)

**L2+B** = damp (never plain B) · **L2+R2** = debug/dev mode. Other factory
chords are in the Unitree manual. Under the previous HoloMotion stack the map
was different (Select = damp, L1 = free fall); that stack is archived and those
rules no longer apply.

## 8. Configuration and tuning

Teleopit uses Hydra: every key in a config file can be overridden on the
command line as `section.key=value`, and `--config-name` picks the file.

| File | Purpose |
|---|---|
| `teleopit/configs/pico4_sim2real.yaml` | Real-robot PICO session: smoothing, deadbands, gains, joystick caps, PD gains, joint limits, MOCAP entry checks, hands/neck/recording (off). |
| `teleopit/configs/pico4_sim.yaml` | Same tuning for MuJoCo rehearsal. Keep the two in sync. |
| `teleopit/configs/input/pico4.yaml` | Headset input: operator height, buttons, network (`bridge_port` 63901, discovery, `bridge_advertise_ip`), `foot_z_gain`, headset video. |
| `teleopit/configs/robot/g1.yaml` | Robot model, default standing angles, `action_scale` per joint (index 14 = waist_pitch). |
| `teleopit/configs/controller/rl_policy.yaml` | Policy loader; `controller.policy_path=ckpt/track_g1.onnx`. |

Knobs we actually turn, with current values:

| Key | Value | Meaning |
|---|---|---|
| `root_xy_gain` | 1.25 | Multiplier on pilot root XY displacement. |
| `anchor_lin_vel_deadband` / `anchor_ang_vel_deadband` | 0.05 / 0.08 | Soft-knee deadbands that kill jitter without eating small steps. |
| `reference_velocity_smoothing_alpha` / `..._anchor_...` | 0.6 / 0.5 | Higher = less lag, more jitter. |
| `mocap_entry_blend_s` | 1.0 | Joint blend from standing pose into the pilot pose on Y. |
| `mocap_switch.check_frames` | 10 | Valid PICO frames required before MOCAP. |
| `joystick.vx_max / vy_max / wz_max / stick_deadzone / settle_s` | 0.9 / 0.55 / 1.1 / 0.12 / 1.5 | Walking-mode caps and mode-swap dwell. |
| `input.foot_z_gain` | 1.6 | Amplifies pilot foot lift height. |
| `input.human_height` | 1.74 | Pilot height, metres. Change per operator. |
| `robot.action_scale[14]` | 0.4386 | waist_pitch. 0.0 re-freezes the waist. |
| `runtime.stale_reference_hold_s` / `max_reference_age_s` | 0.08 / 0.25 | Hold last command on stale input; refuse MOCAP entry on an old reference. |
| `startup_ramp_duration` / `standing_return_ramp_duration` | 2.0 / 0.5 | Kp ramps on Start and on X. |
| `joint_vel_limit` | 10 rad/s | Runtime trips to DAMPING above this. |

Optional headset video (sim camera or a RealSense on the laptop) is
`input.video.enabled=true`; `input.video.source=test-pattern` checks only the
link. Video failure never stops tracking.

Test any tuning change in `pico4_sim` first, then in a tethered STANDING
session before free walking.

## 9. What our commits change vs upstream

`git log f926386..main` (oldest first).

| Commit | Change | Why |
|---|---|---|
| a16e176 | Stale-resume reset, anchor velocity deadbands (soft-knee 0.05 m/s, 0.08 rad/s), smoothing alphas 0.35/0.25 → 0.6/0.5, pilot height 1.74, **waist unlock restore** (`action_scale[14]` 0 → 0.4386) | Sim A/B: no fall, same tracking error, 40 → 20 ms lag; waist lock root-caused as a factory lock, not a fault. |
| f29398a, ed81b2b | `root_xy_gain` 1.143 → 1.25 | Pilot displacement was under-reproduced; overshoot ~9% by request. |
| 46e5cc4, dce4933, 6b174be | **JOYSTICK walking mode** (right-stick click), enterable from STANDING/MOCAP, round-trip toggle | Walk beyond the tracking space. |
| 13fd4f8 | Knee position weight 0/10 → 50 in the PICO→G1 IK; joystick caps raised | Forward kicks were reaching foot targets via hip abduction. |
| 11bbed3 | Reset alignment/velocity on joystick exit; 1.5 s STANDING dwell between mode swaps; knee IK weight 50 → 35 | Stale-yaw lunge collapsed the robot when swapping modes. |
| 9f09761, 3bb24c2 | Strafe moved to grip analogs; clearer "tracker asleep" message; **1 s joint blend-in on MOCAP entry** (`mocap_entry_blend_s`) | Left-stick X / right-stick Y never arrive from the app; entry was a hard pose snap. |
| 8a01ae5 | `foot_z_gain` 1.6; joystick → teleop swap lands in STANDING and requires an explicit Y | Light steps were absorbed by the policy; auto-entry caused falls while the pilot was still in driving posture. |
| 9ebecc9 + this commit | `go.sh` interface auto-detect, `lab/` (Orin relay, e-stop, history), this README | Repo now matches the stack that is actually used. |

Upstreaming these to BotRunner64/Teleopit is still open. Pulling upstream
updates: `git remote add upstream https://github.com/BotRunner64/Teleopit.git`,
then rebase `main` onto `upstream/master` and re-test in sim.

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `go.sh`: "no interface has a 192.168.123.x address" | Cable, or the NM profile is down/missing: section 3.2. |
| `go.sh`: "an ONBOARD Teleopit session is running" | Someone started Teleopit on the Orin. Kill it there (`pkill -9 -f miniforge3/envs/teleopit`) before running from the laptop. |
| `ping 192.168.123.164` fails | Robot not booted yet, cable, or laptop on the wrong port/profile. |
| Runtime up, headset never connects | Headset not on `g1-teleop`; relay not running on the Orin (`pgrep -af discovery_relay`); hotspot down (`nmcli con up Hotspot-1` on the Orin); something else holding 63901 (`ss -ltnp \| grep 63901`). Direct test: `python scripts/dev/test_pico_bridge.py --no-video`. |
| Headset connects then drops after a minute | Headset auto-hopped to another Wi-Fi. Forget every other network on it. |
| PicoBridge will not open / entitlement error | Give the headset internet once (phone hotspot), open the app, then go back to `g1-teleop`. |
| Y refused: "no body tracking data" | Ankle trackers asleep or full-body tracking off. Walk two steps, check the app status, press Y again. |
| Robot pose crooked or crouched in MOCAP | Bad headset body calibration (redo it, do not re-wear the headset after) or wrong `input.human_height`. |
| Robot snaps or lunges when entering MOCAP | Pilot was not aligned/still. Use X, align, Y again. The 1 s blend-in only softens joints; the root is anchored at entry. |
| Trackers drift or drop | LED outward, ankles visible to the headset cameras, tight trousers, good light. Re-pair in Settings → Devices → Motion Tracker. |
| Steps are absorbed, feet barely lift | Raise `input.foot_z_gain` (1.6 now). |
| Too jittery / too laggy | Deadbands vs smoothing alphas in `pico4_sim2real.yaml`; test in `pico4_sim` first. |
| No LowState from the robot / robot ignores Start | Wrong interface, DDS on the wrong NIC, or factory control not released (L2+R2). Try `standalone_standing.py --dry-run`. |
| `import g1_bridge_sdk` fails | Rebuild: `git submodule update --init --recursive` then `CPLUS_INCLUDE_PATH=$CONDA_PREFIX/include bash scripts/setup/setup_g1_bridge.sh`. |
| Waist "jams", gets hot | Check the waist lock (app setting + screws) before anything else. Rollback: `action_scale[14]: 0.0`. |
| Runtime tripped to DAMPING by itself | `joint_vel_limit` exceeded or a safety check; read the log line just before `DAMPING`. Start stands it back up. |
| `pkill`/`pgrep -f` kills the wrong thing or nothing | Self-match trap: the shell command itself contains the pattern. Use the `[t]` bracket trick or a separate shell. |

## 11. Beyond teleop

- **Onboard deployment (Teleopit running on the Orin).** Required for
  LinkerHand, OpenNeck, RealSense preview and data recording; not needed for
  body teleop. The Orin already has an env at `miniforge3/envs/teleopit` and a
  pre-waist-unlock snapshot `~/teleopit_rollback_2026-08-17.tgz`. Launch with
  `real_robot.network_interface=eth0` per
  [`docs/docs/tutorials/pico-sim2real.md`](docs/docs/tutorials/pico-sim2real.md).
  Keep the onboard checkout in sync with `main` of this repo. Never run onboard
  and laptop sessions at the same time.
- **Recording teleop episodes** (HDF5 + MP4, for imitation learning):
  `--config-name sim2real_record`, onboard with a RealSense, see the same
  tutorial and [`docs/docs/reference/resources/`](docs/docs/reference/resources/).
- **Running a learned high-level policy on the robot:**
  [`docs/docs/tutorials/high-level-policy-sim2real.md`](docs/docs/tutorials/high-level-policy-sim2real.md).
- **BVH playback** (no headset): [`docs/docs/tutorials/bvh-sim2real.md`](docs/docs/tutorials/bvh-sim2real.md).
- **Training the tracking policy:** [`docs/docs/tutorials/training.md`](docs/docs/tutorials/training.md)
  (needs a GPU machine; the laptop cannot).
- **Architecture:** [`docs/docs/reference/architecture.md`](docs/docs/reference/architecture.md).
- **Docs site locally:** `cd docs && npm install && npm start`.
- **Tests:** `pip install -e '.[dev]' && pytest`.

## 12. Repository layout

```
README.md                  this file (end-to-end operating manual)
README.upstream.md         original Teleopit README (quick start, changelog)
go.sh                      one-command laptop session launcher
lab/
  teleopit_estop.sh        software e-stop fallback (kills the command stream)
  orin/discovery_relay.py  headset auto-discovery relay, runs on the Orin
  orin/README.md           Orin hotspot + relay setup
  history/                 PROJECT_STATE / DEVELOPER-HANDOFF from 2026-08-18 (verbatim)
teleopit/                  runtime: inputs (pico4), retargeting (GMR), sim, sim2real (mp runtime, remote parser, safety)
teleopit/configs/          pico4_sim.yaml, pico4_sim2real.yaml, input/pico4.yaml, robot/g1.yaml
scripts/run/               run_sim.py, run_sim2real.py, standalone_standing.py, record_pico_motion.py
scripts/dev/               test_pico_bridge.py, test_g1_bridge.py, benches
scripts/setup/             download_assets.py, setup_g1_bridge.sh
third_party/               g1_bridge_sdk (C++ DDS bridge), unitree_sdk2_python, linkerhand, somehand (submodules)
docs/                      upstream Docusaurus docs (tutorials, configuration reference, architecture)
train_mimic/               upstream training code (not used here; no GPU on the laptop)
```

Not committed (downloaded or generated): `assets/robots/`, `ckpt/`,
`teleopit/retargeting/gmr/assets/`, `data/`, `*.log`, `outputs/`, `*.apk`
(the headset app, section 3.4). CI (`.github/workflows/repo-hygiene.yml`)
fails on any tracked file over 2 MiB; run
`python scripts/dev/check_large_tracked_files.py` before pushing.

Branches on GitHub: `main` (this stack), `archive/holomotion-2026-08-18`
(previous HoloMotion/SONIC workspace with all audits and runbooks).

## License

Apache 2.0 (upstream Teleopit), see [`LICENSE`](LICENSE).
