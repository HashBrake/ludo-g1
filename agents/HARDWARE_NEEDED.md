# HARDWARE_NEEDED.md (actions a human must perform on physical hardware)

## H-001  Brio still image of the real board for calibration  (fable, 2026-09-11T18:35+07:00)  OPEN
Needed by T-008 (board calibration). Read-only, no session needed.
Steps:
1. Mount the Brio in its final top-down position over the table and plug it into this laptop.
2. Place the board in its play position with all four AprilTags visible and unobstructed; no horses on the board.
3. Run: `.venv/bin/python tools/hardware_checks/brio_still.py --out data/calib/board_empty.png` (script delivered by T-008;
   until then any 4K still saved to that path works).
4. Repeat with all horses in their base cells: `--out data/calib/board_start.png`.
Post-check the agent runs: `.venv/bin/python -m board.calibration --image data/calib/board_empty.png` prints four tag ids,
the homography reprojection error in px, and writes config/board_calib.yaml.

## H-002  Bring up the robot LAN so the DDS link can be verified  (opus, 2026-09-11T20:05+07:00)
Needed by T-002 follow-up and by all of Phase 1. Read-only, no session needed (no motion command is sent).
At the time of T-002 no interface on this laptop held a 192.168.123.x address; the USB Ethernet dongle
(`0b95:1790 ASIX AX88179`, interface `enx000ec6c10aa5`) was present but down, and `enp0s31f6` was down.
Steps:
1. Plug the Ethernet cable from the G1 into the laptop (built-in port `enp0s31f6` or the AX88179 dongle).
2. Power the robot and wait for the Orin to boot.
3. Bring the static profile up, e.g.
   `nmcli con add type ethernet ifname enp0s31f6 con-name robot-lan ipv4.method manual ipv4.addresses 192.168.123.2/24 connection.autoconnect yes`
   then `nmcli con up robot-lan` (profile already exists on this laptop for the prior teleop work; `nmcli con up robot-lan` may be enough).
4. Check: `ping -c 2 192.168.123.164` answers.
5. Put the interface name that holds 192.168.123.2 into `config/robot.yaml` `network.dds_interface`
   (it is the literal `UNMEASURED` today; the read-only arm driver refuses to run while it is).
Post-check the agent runs (updated by T-018, now that the read-only state reader exists):
`.venv/bin/python tools/hardware_checks/list_devices.py` prints the interface with `<-- G1 LAN`, then
`.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream arm --seconds 10` exits 0 and
reports a non-zero rate on `rt/lowstate` (the Phase 1 acceptance run is the same command with `--seconds 600`).
No motion command is involved in either check: the arm driver of T-018 creates no DDS writer at all.

## H-003  Plug in the Brio, the DexH15 and the PxCap Pro glove once, for enumeration only  (opus, 2026-09-11T20:05+07:00)
Needed to finish the UNMEASURED rows of docs/sdks.md (native resolutions, /dev node names, USB ids, serial
permissions). Read-only, no session needed; the hand must NOT be enabled.
Steps:
1. Plug in the Logitech Brio (USB 3 port), the DexH15 Modbus adapter and the PxCap Pro glove.
2. Do not power-enable the hand's motors; nothing in this check enables a motor.
3. Confirm serial permissions: `ls -l /dev/ttyUSB* /dev/ttyACM*`; if the nodes are not group `dialout`
   readable, run `sudo usermod -aG dialout $USER` and log out and in again.
Post-check the agent runs (T-019 adds the last three lines; all read-only, no session):
```
.venv/bin/python tools/hardware_checks/list_devices.py --json
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream hand --seconds 600 --json
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --camera palm --seconds 600 --json
.venv/bin/python -m pytest -m readonly tests/test_dexh15.py -q
```
and records the Brio's video node + USB id, the DexH15 palm-camera node, and the two serial nodes with
their USB ids into docs/sdks.md, replacing the UNMEASURED rows; the serial by-id path goes into
config/hand.yaml `device.port` and the palm node into config/cameras.yaml `palm.device` first, or the
last three commands exit 3 naming exactly those keys. The two stream runs are the 10-minute hand-state
and palm-camera statistics that T-019's first acceptance line asks for, and the achieved joint read
rate they report is the A3 verdict for docs/sdks.md 4.6 and section 9.

## H-004  Plug in the PxCap Pro glove and put the PICO 4 on the network, for reading only  (opus, 2026-09-12T05:20+07:00)
Needed by T-020's first acceptance line (10-minute glove and controller-pose statistics, the A4 verdict) and by all of
Phase 2. Read-only, no session needed: both are input devices, neither driver has a write call, and no motor is touched.

### a) The glove
1. Plug the PxCap Pro into this laptop (it enumerates as a USB CDC serial node, `/dev/ttyACM*`).
2. Confirm the permissions: `ls -l /dev/ttyACM* /dev/ttyUSB*`. If the node is not group `dialout` readable, run
   `sudo usermod -aG dialout $USER` and log out and in again (same step as H-003; do it once for both devices).
3. Run `.venv/bin/python tools/hardware_checks/list_devices.py --json` and copy, for the glove's node:
   - the `/dev/serial/by-id/...` path into `config/hand.yaml` `glove.port` (never `/dev/ttyACMn`: node numbers move),
   - its `vendor:product` into `config/hand.yaml` `glove.usb_id`.
   Nothing in the delivery names the glove's VID:PID, so this run is the only way to learn it.
4. Do NOT run the glove's zeroing, static-magnet check or firmware upgrade. drivers/pxcap.py cannot: it never names those
   calls (a test greps for all seven).

### b) The headset
1. Free TCP 63901 on this laptop. Right now the systemd *user* unit `holosim-pcservice`
   (`/opt/apps/roboticsservice/RoboticsServiceProcess`, the XRoboToolkit PC service left over from the previous stack) holds
   it, and `PicoBridge.start()` fails with `address already in use`. Check and stop it:
   `ss -ltnp | grep 63901` then `systemctl --user stop holosim-pcservice`.
2. Power the headset and open the **PicoBridge** app (v0.2.1, `com.picobridge.app`; install steps in
   `third_party/g1_pico_teleop/README.md` 3.4). LUDO-G1 needs the left **controller** only: no ankle trackers, no body
   tracking, no calibration (D-006).
3. Choose ONE network path and tell the agents which, in `agents/QUESTIONS.md` or by filling the config key:
   - **Same Wi-Fi (simplest, and the default assumption).** Put the headset and this laptop on one ordinary network; the
     pico_bridge discovery broadcast reaches the headset unaided and nothing else is needed. Leave
     `config/robot.yaml` `teleop.pico.advertise_ip` as `UNMEASURED`.
   - **The lab's robot-NAT path.** Headset on the Orin's `g1-teleop` AP, with the discovery relay running on the Orin
     (`README.md` 3.3); then set `teleop.pico.advertise_ip` to `192.168.123.2` and bring the robot LAN up first (H-002).
     The laptop must NOT join `g1-teleop` itself.
4. Hold the controller in the glove jig with a clear view of the headset's cameras while the checks below run.

Post-check the agent runs (all read-only, no session):
```
.venv/bin/python tools/hardware_checks/list_devices.py --json
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream glove --seconds 600 --json
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream pose  --seconds 600 --json
.venv/bin/python -m pytest -m readonly tests/test_pxcap.py tests/test_pico.py -q
```
The glove command needs step (a3) done first or it exits 3 naming `config/hand.yaml glove.port`; the pose command exits 3
naming `PicoBridge` / the socket until (b1) and (b2) are done. The two 600 s runs are the 10-minute statistics T-020's first
acceptance line asks for: the achieved glove frame rate they report is the A4 verdict for docs/sdks.md 6.3, and the achieved
pose rate is the first real number for `config/robot.yaml` `teleop.pico.input_hz` (120 is the lab's configured value, not a
measurement). Neither number exists yet.

## H-005  The Orbbec Ego drops ~1.2% of frames today; re-seat it and re-run  (opus, 2026-09-14T12:45+07:00)  OPEN
Needed by T-016 (recorder skew budget) and by any recording that uses `oblique`. Read-only, no session needed.
T-046 streamed the Ego for 600 s twice: 30.00 Hz achieved, but 222 and 207 dropped frames (~1.2%) and jitter
p99 18.3 ms. On 2026-09-11 (T-010) the same device on the same command gave 0 drops and p99 1.4-2.9 ms; the bad
numbers reproduce today at 10 s and 30 s with the host idle (load 0.4, on AC), so it is neither run length nor
agent load. The Ego enumerates on a **USB 2.0 (480 Mbps)** link (`/sys/bus/usb/devices/3-1/speed`) and exposes no
exposure or frame-rate control over UVC, so neither bandwidth nor auto-exposure can be ruled out from software.
Steps (each is one variable; run the post-check after each and stop when the drops go away):
1. Note which physical port the Ego is in now, then move it to a **USB 3 port directly on the laptop** (no hub) and
   re-run the post-check. `cat /sys/bus/usb/devices/*/speed` should then show 5000 for the Ego's bus id.
2. If it still drops: swap the USB cable.
3. If it still drops: light the scene the camera sees (a dim scene makes a UVC sensor lengthen its exposure past
   33 ms, which produces exactly this pattern of 50 ms intervals), and re-run.
4. Whatever fixes it, write the port (and lighting, if that was it) into `agents/QUESTIONS.md` so the rig keeps it.
Post-check the agent runs (also re-checks that the by-path selector still names the LEFT node after a re-plug):
```
ls -l /dev/v4l/by-path/ /dev/v4l/by-id/
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream oblique --seconds 600 --json
```
Pass: 0 drops and jitter p99 < 10 ms, as T-046's acceptance asked for. If the port changed, `config/cameras.yaml`
`oblique.device` and `device_right` must be updated to the new by-path paths (the old ones stop existing, and the
driver then refuses to open the stream by name rather than opening the wrong one).

### H-005 amendment  (fable, 2026-09-14T13:10+07:00)
Fable's own checks (D-025) show the frame count is exact and the gaps come and go with host load: 12 gaps in 30 s right
after a test suite finished, 0 gaps six minutes later with the host quiet. So before step 1: (0) run the post-check with
nothing else running on the laptop; if it passes, the port and cable are fine. The post-check itself changes with T-047:
the stream_stats JSON will then report the kernel-timestamp jitter and a separate "frames lost" count (expected minus
received); the pass criterion becomes frames lost = 0 and kernel-stamp jitter p99 < 10 ms. Step 1 (USB 3 port) is still
worth one try. One more human-only variable, in this order after step 1: the CPU governor is `powersave`
(`cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`); `sudo cpupower frequency-set -g performance` for one run
tells us whether that is the cause. Agents do not change it (system state).

### H-005 post-check result under T-047  (opus, 2026-09-14T13:55+07:00)
Step (0) of the amendment -- run the post-check with nothing else on the laptop -- was run after
T-047 landed the kernel-timestamp change, on the same by-path node, host quiet (no pytest, 1-min load
0.49, governor still `powersave`):
```
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream oblique --seconds 600 --json
frames 17900 in 596.625 s at 30.00 Hz; frames_lost 0; drops 0; stamp_source {kernel: 17900}
kernel-stamp jitter p50 0.14 ms, p99 0.81 ms, max 9.37 ms
arrival    jitter p50 0.23 ms, p99 3.29 ms, max 16.17 ms; arrival - kernel p50 9.51, p99 12.07 ms
```
The amended pass criterion (frames lost 0 and kernel-stamp jitter p99 < 10 ms) is **met**, and the
by-path selector still opens 'Ego left' at 1600x1200 MJPG. No human action is required for the
timing: steps 1-3 (USB-3 port, cable, lighting) are not forced by any measurement now on record.
Left OPEN for Fable to close or to re-scope: D-025 judged the USB-3 re-seat "still worth one try",
and that is a scope call, not a measurement. Full numbers and the run's JSON: the T-047 entry in
agents/BUILD_LOG.md.
