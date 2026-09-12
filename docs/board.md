# Board: calibration and perception

Two modules look at the board. `board/calibration.py` ties the board to the camera; `board/perception.py`
reads the board state back out of a `top` frame and judges what a primitive did (CLAUDE.md 5.5, 6.5).

## Calibration

One mapping ties the board to the camera: a plane-to-plane homography from the board frame
(millimetres, defined by `config/board.yaml`) to pixels in the fixed top-down Brio frame. Everything
that has to point at a cell goes through it — the `top` crop of CLAUDE.md 5.3, the two goal heatmap
channels, `engine.cells.load_cells(top_px=...)`, and the perception check after each primitive.

```python
from board import calibration

calib = calibration.load("config/board_calib.yaml")   # or calibration.calibrate_file(png)
calib.board_to_px((40.0, 280.0))                      # board mm -> Brio px
calib.px_to_board((1832.0, 415.0))                    # Brio px  -> board mm
calib.cell_px("track-12")                             # one cell centre, in px
calib.cell_px_all()                                   # every cell, ready for load_cells(top_px=...)
calib.rms_px                                          # how good the fit actually is
```

## The two frames

**Board frame** (`config/board.yaml`, `frame: board_centre`): origin at the board centre, +x toward
the board's right edge, +y toward its far edge, millimetres. Every cell's `board_xy_mm` is in it.

**Brio frame**: pixels in the *full, uncropped* camera frame. Nothing in `board/calibration.py` knows
about the 640×480 the policy sees — that is a crop plus a resize applied later, and `top_crop` in the
written yaml is the board's bounding box in the full frame, offered for `config/cameras.yaml`'s
`top.crop`.

The homography is a plane-to-plane map, so it is exact only for points *on the playing surface*. A
horse is 30 mm tall; its top face does not project where its cell does. Ground the goal heatmaps on
cell centres (surface), never on the top of a piece.

## How the calibration is computed

1. Read the `apriltags` block of `config/board.yaml`: family, `size_mm`, the tag id at each of the
   four corners, and each tag centre in the board frame (or, while `centres_mm` is `UNMEASURED`,
   centres derived from `tag_inset_mm`).
2. Detect the tags with OpenCV's `cv2.aruco` — `getPredefinedDictionary(DICT_APRILTAG_36h11)` plus
   `ArucoDetector` with `CORNER_REFINE_SUBPIX`. **`pupil-apriltags` is not used and is not a
   dependency**: the pinned `opencv-python` 5.0 already carries the AprilTag 36h11 dictionary.
3. Pair the 4 corners of each of the 4 tags with their known board-frame positions (16 points) and
   fit with `cv2.findHomography(..., cv2.RANSAC)`. All 16 must be inliers; if they are not, the tags,
   the board geometry in the config, or a tag's mounting orientation disagree, and that is an error
   rather than a quiet best fit.
4. Report the reprojection RMS and max over those 16 corners. That number is the only claim the
   calibration makes about itself (R5). A jump in it after the camera is bumped is the signal to
   re-run.

Fewer than four tags is an error. Three tags do define a homography, but a board calibrated from
three corners is a board whose fourth corner nobody checked, and checking is the point.

### The tag orientation assumption

Each tag is assumed to be printed and mounted **upright in the board frame**: the tag's own "up"
along board +y, its "right" along board +x. `cv2.aruco` returns a tag's corners in its canonical
reading order (top-left, top-right, bottom-right, bottom-left as printed), so under that assumption
corner *k* of a tag has a known board-frame position. A tag mounted a quarter-turn off is still
detected, but its corners pair with the wrong board points and the RMS jumps by roughly the tag size
— loudly. Re-mount the tag; do not widen the tolerance.

## Running it

```
# 1. capture the still (read-only; no hardware session needed — H-001)
.venv/bin/python tools/hardware_checks/brio_still.py --out data/calib/board_empty.png

# 2. calibrate
.venv/bin/python -m board.calibration --image data/calib/board_empty.png
```

`brio_still.py` opens the V4L2 node from `config/cameras.yaml` `top.device` (or `--device`), asks for
3840×2160 MJPG, discards 10 frames so auto-exposure settles, turns autofocus off, and writes a
lossless PNG — a JPEG's ringing around the tag edges moves the detected corners. It exits **3** when
there is no usable camera (nothing configured, node absent, or no frame), which is distinct from the
**2** argparse uses for a usage error. It sends no motion command and needs no session.

`python -m board.calibration --image PATH` prints the four tag ids and where each landed, the
reprojection error, and the board bounding box, then writes `config/board_calib.yaml`. `--no-write`
reports only; `--out` redirects. It exits 1 if the image cannot be read or a tag is missing.

## `config/board_calib.yaml`

A **generated** artefact, not a hand-maintained config: it is not one of the six files
`runtime/config.py` validates, and it is read back with `board.calibration.load()`. Re-run the CLI
rather than editing it. It records `homography_board_mm_to_top_px`, the tag ids and family used, the
reprojection RMS and max, the image it came from and that image's size, `top_crop`, the
`board_config_hash` it was computed under, and `unmeasured_board_keys` — the placeholder tag geometry
that was in force. A calibration whose `board_config_hash` differs from today's `config_hash("board")`
was computed against different board geometry and has to be re-run.

## What is still a placeholder

The whole `apriltags` block of `config/board.yaml` is a Form-2 placeholder (`docs/config.md`): usable
numbers next to `_status: UNMEASURED`, because the board was never photographed and nobody has
measured the printed tags. Today they read `family: tag36h11`, `size_mm: 40.0`, ids `0..3`
anticlockwise from the (−x, −y) corner, and `tag_inset_mm: 10.0` (tag edge to board edge), which puts
the tag centres at (±270, ±270) mm.

**These numbers are not measurements.** The detector does not care whether `size_mm` is right — a
wrong tag size fits a homography with a perfectly good reprojection error and a wrongly scaled board.
So the CLI prints a `WARNING` line naming every `apriltags.*` key still `UNMEASURED`, and the same
list is written into `config/board_calib.yaml`. When the board is printed and mounted: measure the
tags, put the truth in `config/board.yaml` (including `centres_mm` as a mapping of the four corner
keys, which then overrides the `tag_inset_mm` derivation), flip the statuses to `MEASURED`, and re-run.

## Perception: board state from the `top` frame

`board/perception.py` answers the question `runtime/controller.py` asks after every primitive — *did
that actually happen?* — and the one the watchdog asks during it — *is anything happening at all?*
Both go through the `Perception` Protocol, which has exactly two methods and no state. The blob
measurement and the classification rules it applies (`Rules`, `load_rules`, `Pose`, `Placement`,
`Bowl`) live in `board/detect.py` since T-042; `perception.py` imports them back.

```python
from board import calibration, perception

calib = calibration.load("config/board_calib.yaml")
eye = perception.TopCameraPerception(calib)

view = eye.detect(frame)          # BoardView: horses, their pose and placement, the die
view.occupancy()                  # {"track-12": "R", "R-base-0": "G", ...}

before = eye.detect(frame).to_dict()      # ... the robot executes the primitive ...
outcome = eye.verify(command, before, eye.detect(frame).to_dict())
eye.progress(command, before, eye.detect(frame).to_dict())   # 0..1, for the watchdog
```

`verify` and `progress` take the **dict form of a `BoardView`**, not a frame: that is the signature
the controller already calls, and the watchdog samples `progress` ten to twenty times per primitive,
so whoever owns the camera decides how often a frame is really looked at. A dict that did not come
out of `BoardView.to_dict()` — the engine's own `board_state()`, say — is refused rather than
misread. The controller reads the engine's board today (`MockPerception`); wiring a camera into it
is a later task.

### Detection rules

Per frame, per player colour: `cv2.inRange` in HSV (one band per entry, red needs two because it
wraps the hue origin) → open → external contours → for each blob, its footprint area and its
minimum-area rectangle.

| question | rule | where the numbers live |
|---|---|---|
| is this a piece at all? | area ≥ `min_area_ratio` of the expected footprint area | `perception.min_area_ratio` |
| standing or fallen? | `standing` iff area is inside `standing_area_ratio` **and** the rectangle's long/short is ≤ `standing_max_aspect`; anything else is `fallen` | `perception.standing_*` |
| on a cell or between cells? | the centroid goes through the calibration into the board frame; the nearest cell centre within `cell_snap_radius_mm` claims it | `perception.cell_snap_radius_mm` |
| where is the die? | the one white blob of about die size inside the bowl's search region; `in_bowl` iff within the bowl radius | `perception.die_hsv_range`, `die_area_ratio`, `perception.bowl` |

Areas are **ratios against the expected footprint area at the local pixel scale** — the square root
of the homography's area Jacobian at that board point — and distances are **millimetres in the board
frame**. Neither is in pixels, so the same numbers hold at any camera distance and at both ends of a
tilted frame. A horse on its side shows a footprint-by-height rectangle (area ≈ 1.5×, aspect ≈ 1.5);
one on its back shows only the smaller arrow face (area ≈ 0.3×, still square) — which is why the
fallen test is "outside the standing band", not "bigger than it".

Two horses never share a cell: the nearer one claims it and the other is reported as between cells,
because an occupancy that quietly loses a piece is worse than one that shows it in the wrong state.

### From before/after to a failure mode (CLAUDE.md 6.5)

| primitive | what is seen | verdict |
|---|---|---|
| MOVE | our colour standing on `dst`, gone from `src` | success (a capture is the same thing: the captured colour is no longer on `dst`) |
| MOVE | our colour on `dst` but lying down | `horse_fell` |
| MOVE | our colour on `dst` **and** still on `src` | `wrong_horse` — a second horse of our colour was moved |
| MOVE | nothing on the table changed | `grasp_failed` |
| MOVE | our horse never left `src`, but something else moved | `wrong_horse` |
| MOVE | it left `src` and is lying down somewhere | `horse_fell` |
| MOVE | it left `src` and is anywhere else — another cell, off the magnet, not seen | `missed_cell` |
| ROLL | the die is in the bowl and further than `die_moved_min_mm` from where it started | success |
| ROLL | the die is in the bowl and has not moved | `die_grasp_failed` — it was never picked up |
| ROLL | the die is outside the bowl, or not seen | `die_out_of_bowl` |
| RECOVER | the target cell holds a standing horse | success |
| RECOVER | it holds a lying one, or one is lying beside the cell | `horse_fell` |
| RECOVER | the horse is beside the cell, upright | `missed_cell` |
| RECOVER | nothing changed at all | `timeout_no_progress` |
| RECOVER with no cell | the die is back in the bowl | success, else `die_out_of_bowl` |

`policy_stalled` is not in this table on purpose: it is the watchdog's verdict, made *during* the
primitive by `runtime/controller.py`, never perception's.

`progress()` is the same expectations counted rather than judged — a MOVE reads 0.5 once the horse
has left its source and 1.0 once it stands on the target — so the watchdog sees a signal that rises
as the primitive gets done. Only its direction is read, never its magnitude.

### What it cannot do

Stated rather than papered over, because each of these will look like a policy failure if it is not
remembered:

- **Two horses of the same colour are interchangeable.** A MOVE that takes the wrong horse of the
  same colour is caught only because the cell it came from emptied.
- **A white die on the white board is invisible** to a white-blob rule. It is reported as *not seen*,
  which still comes out as `die_out_of_bowl` and still asks a human for the die back (6.5) — but the
  detector does not pretend to have found it.
- **A horse lifted off the table is "not seen"**, not "in the gripper".
- **The arrow on top is ignored** (Q-006, `horse.arrow_orientation_matters: false`). It is drawn dark
  so it takes a bite out of the colour blob exactly as the printed one will; the area rule is sized
  for that.
- **The frame must be the calibrated one.** `detect()` refuses a frame of a different size, because a
  crop or a resize would silently map every blob to the wrong board point.

### Placeholder status

The whole `perception` block of `config/board.yaml` is `UNMEASURED`, and so is the bowl. The bowl
follows one precedence rule: a measured `die.bowl_centre_mm` / `die.bowl_diameter_mm` wins, and
`perception.bowl` stands in only while those are the literal `UNMEASURED`; `Bowl.measured` says which
one you got. Nothing here has seen a photograph of the real board or the real horses — the colours
are not even printed yet — and the engine team's own perception is expected to replace this module
behind the same Protocol (CLAUDE.md 5.1, T-038 notes).

## Verification

### Calibration (T-008)

`tests/test_calibration.py` is the acceptance for T-008 and runs on a synthetic board: the four tags
of `config/board.yaml` rendered at their configured board positions with `generateImageMarker`, warped
by a known homography, detected, and compared against ground truth at the centre of all 88 cells —
not only at the tag corners the fit saw.

| case | reprojection RMS | worst cell centre |
|---|---|---|
| translation only | 0.120 px | 0.008 px |
| 15° rotation + mild projective tilt | 0.231 px | 0.054 px |

against acceptance bounds of RMS < 0.5 px and cell error < 1.0 px, at 2 px/mm (a 1400×1400 board
image, 80 px tags). The test also guards at 0.25 px, which is what would catch a systematic bias
creeping back in: the half-pixel convention slip between numpy's row index and OpenCV's pixel centre
costs exactly 0.707 px and would otherwise slide under the 1.0 px bound.

### Perception (T-038)

`tests/test_perception.py` runs the detector over scenes from `board/synthetic.py`: a grey table, the
white board, the bowl, coloured horses at named cells in each of the 6.5 states, and the die. The
calibration used is the homography the scene was drawn with, so the truth is exact.

| check | result |
|---|---|
| occupancy on 20 random 10-horse boards | recovered exactly, 20/20 |
| occupancy under 15° rotation + tilt, 12 horses | recovered exactly |
| each 6.5 variant (fallen on side, fallen on back, off the magnet, missing) | classified as specified |
| each 6.5 failure mode out of `verify()` | one test each, all nine verdicts |
| `detect()` on 640×480, 16 horses + die | **4.4 ms mean, 4.6 ms max** per frame (bound: 30 ms) |

Composition with T-008 is tested too: horses drawn on the *tag* board image, the calibration fitted
from the tags rather than handed over, and the occupancy still exact.

**No real still has been calibrated or detected in yet.** No Brio was attached when T-008 ran
(`tools/hardware_checks/list_devices.py`: four SunplusIT integrated-webcam nodes and the two Orbbec
Ego nodes, no `046d` device), so H-001 stays open, the numbers above are synthetic, and **every
number in this section is a statement about the rules, not a detection rate** (R5). The real-still
check is the H-001 post-check plus one more step once the board is photographed with horses on it:

```
.venv/bin/python tools/hardware_checks/brio_still.py --out data/calib/board_start.png   # H-001 step 4
.venv/bin/python -m board.calibration --image data/calib/board_empty.png                # H-001 post-check
# then: detect on board_start.png and compare against where the horses actually are
```

Until that runs, the HSV bands and the blob thresholds are guesses, and a failure of this module on
the real table will look like a failure of the policy unless this is checked first.
