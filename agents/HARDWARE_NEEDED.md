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
