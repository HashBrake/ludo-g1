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
