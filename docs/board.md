# Board calibration

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

## Verification

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

**No real still has been calibrated yet.** No Brio was attached when T-008 ran
(`tools/hardware_checks/list_devices.py`: four SunplusIT integrated-webcam nodes and the two Orbbec
Ego nodes, no `046d` device), so H-001 stays open and the numbers above are synthetic.
