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
