"""Board calibration against a synthetic board whose true homography is known.

There is no real Brio still yet (H-001), so the acceptance for T-008 is synthetic: render the four
AprilTags of ``config/board.yaml`` at their configured board-frame positions into an image whose
board-to-pixel mapping we chose ourselves, warp that image by a second known homography, and check
that :func:`board.calibration.calibrate_image` recovers the composition. Because the ground truth is
constructed rather than measured, "recovered" is checked where it matters: at the centre of every one
of the 88 cells the policy will be asked to point at, not only at the tag corners the fit saw.

The rendering deliberately goes through the real ``config/board.yaml`` (tag ids, family, size, the
derived centres, and the cell table), so a change to any of those runs through this test.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from board import calibration
from engine.cells import load_cells
from runtime import config
from tools.hardware_checks import brio_still

PX_PER_MM = 2.0
MARGIN_PX = 100


def _apply(h: np.ndarray, pts) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    out = np.concatenate([pts, np.ones((len(pts), 1))], axis=1) @ np.asarray(h, dtype=np.float64).T
    return out[:, :2] / out[:, 2:3]


def _render_board(geometry: calibration.TagGeometry) -> tuple[np.ndarray, np.ndarray]:
    """A white board image with the four tags drawn on it, plus the true board-mm -> px homography.

    The base mapping is a pure scale and flip: board +x to the right, board +y *up* the image, at
    ``PX_PER_MM`` pixels per millimetre with a white margin around the board outline.

    Two pixel conventions meet here and the half pixel between them is not noise. Numpy indexing
    puts the first row of the marker block at row index ``y``; OpenCV's continuous image coordinates
    put the *centre* of that pixel at ``y``, so the marker's physical top-left edge -- what the
    detector localises -- is at ``y - 0.5``. ``h_place`` is the integral mapping used to blit the
    marker blocks; the returned ground truth is that mapping shifted by half a pixel on both axes.
    """
    width_mm, height_mm = (float(v) for v in config.load("board")["size_mm"])
    width = int(round(width_mm * PX_PER_MM)) + 2 * MARGIN_PX
    height = int(round(height_mm * PX_PER_MM)) + 2 * MARGIN_PX
    h_place = np.array(
        [
            [PX_PER_MM, 0.0, MARGIN_PX + width_mm / 2.0 * PX_PER_MM],
            [0.0, -PX_PER_MM, MARGIN_PX + height_mm / 2.0 * PX_PER_MM],
            [0.0, 0.0, 1.0],
        ]
    )

    image = np.full((height, width), 255, dtype=np.uint8)
    side_px = int(round(geometry.size_mm * PX_PER_MM))
    dictionary = cv2.aruco.getPredefinedDictionary(geometry.dictionary)
    for key in calibration.CORNER_KEYS:
        marker = cv2.aruco.generateImageMarker(dictionary, geometry.ids[key], side_px)
        # Corner 0 of the aruco order is the marker's top-left, i.e. board (cx - s/2, cy + s/2).
        top_left = _apply(h_place, geometry.corners_mm(key)[0])[0]
        x, y = np.round(top_left).astype(int)
        assert np.allclose(top_left, [x, y]), f"{key} does not land on a whole pixel: {top_left}"
        image[y : y + side_px, x : x + side_px] = marker
    half_pixel = np.array([[1.0, 0.0, -0.5], [0.0, 1.0, -0.5], [0.0, 0.0, 1.0]])
    return image, half_pixel @ h_place


def _warp(image: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Warp ``image`` by ``h``, shifted so the result is fully in frame. Returns image and shifted h."""
    height, width = image.shape[:2]
    frame = _apply(h, [[0, 0], [width, 0], [width, height], [0, height]])
    shift = np.array([[1.0, 0.0, -frame[:, 0].min()], [0.0, 1.0, -frame[:, 1].min()], [0.0, 0.0, 1.0]])
    h_shifted = shift @ h
    size = (
        int(np.ceil(frame[:, 0].max() - frame[:, 0].min())),
        int(np.ceil(frame[:, 1].max() - frame[:, 1].min())),
    )
    warped = cv2.warpPerspective(image, h_shifted, size, flags=cv2.INTER_LINEAR, borderValue=255)
    return warped, h_shifted


def _scene(view: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Render the board, apply the view homography, and return the image and the true board -> px map."""
    geometry = calibration.load_tag_geometry()
    base, h_true = _render_board(geometry)
    warped, h_view = _warp(base, view)
    return warped, h_view @ h_true


def _identity_view() -> np.ndarray:
    return np.array([[1.0, 0.0, 37.0], [0.0, 1.0, -19.0], [0.0, 0.0, 1.0]])


def _rotated_tilted_view(image_shape: tuple[int, int] = (1400, 1400)) -> np.ndarray:
    """15 degrees in plane about the image centre, plus a mild projective tilt (about 7% across)."""
    cx, cy = image_shape[1] / 2.0, image_shape[0] / 2.0
    to_centre = np.array([[1.0, 0.0, -cx], [0.0, 1.0, -cy], [0.0, 0.0, 1.0]])
    from_centre = np.array([[1.0, 0.0, cx], [0.0, 1.0, cy], [0.0, 0.0, 1.0]])
    theta = np.deg2rad(15.0)
    rot = np.array(
        [[np.cos(theta), -np.sin(theta), 0.0], [np.sin(theta), np.cos(theta), 0.0], [0.0, 0.0, 1.0]]
    )
    tilt = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0e-4, -0.6e-4, 1.0]])
    return from_centre @ rot @ tilt @ to_centre


def _cell_errors(calib: calibration.Calibration, h_true: np.ndarray) -> np.ndarray:
    cells = load_cells()
    board_xy = np.array([c.board_xy_mm for c in cells.values()])
    return np.linalg.norm(calib.board_to_px(board_xy) - _apply(h_true, board_xy), axis=1)


# --------------------------------------------------------------------------------------------
# tag geometry from config/board.yaml
# --------------------------------------------------------------------------------------------


def test_tag_geometry_comes_from_the_board_config():
    geometry = calibration.load_tag_geometry()
    board = config.load("board")
    assert geometry.family in calibration.FAMILIES
    assert geometry.ids == dict(board["apriltags"]["ids"])
    assert set(geometry.centres_mm) == set(calibration.CORNER_KEYS)
    # Derived centres sit one signed quadrant each, inside the board outline.
    half_x, half_y = (float(v) / 2.0 for v in board["size_mm"])
    for key, (x, y) in geometry.centres_mm.items():
        sx, sy = calibration._CORNER_SIGNS[key]
        assert np.sign(x) == sx and np.sign(y) == sy, key
        assert abs(x) + geometry.size_mm / 2.0 <= half_x and abs(y) + geometry.size_mm / 2.0 <= half_y, key
    # Placeholder tag geometry is still flagged, so the CLI can warn about it.
    assert geometry.unmeasured


def test_tag_corners_are_in_aruco_reading_order():
    geometry = calibration.load_tag_geometry()
    corners = geometry.corners_mm("corner_pos_x_pos_y")
    cx, cy = geometry.centres_mm["corner_pos_x_pos_y"]
    h = geometry.size_mm / 2.0
    assert np.allclose(corners, [[cx - h, cy + h], [cx + h, cy + h], [cx + h, cy - h], [cx - h, cy - h]])


def test_unsupported_family_is_named_at_load(tmp_path):
    board = config.load("board")
    board["apriltags"]["family"] = "tag99h42"
    (tmp_path / "board.yaml").write_text(__import__("yaml").safe_dump(board), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError, match="tag99h42"):
        calibration.load_tag_geometry(root=tmp_path)


# --------------------------------------------------------------------------------------------
# the two acceptance cases: a synthetic board seen straight on, and rotated + tilted
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "view"),
    [("translation", _identity_view()), ("rot15_tilt", _rotated_tilted_view())],
)
def test_synthetic_board_recovers_every_cell_centre(name, view):
    image, h_true = _scene(view)
    calib = calibration.calibrate_image(image, image_path=f"<synthetic {name}>")

    assert calib.rms_px < 0.5, f"{name}: reprojection rms {calib.rms_px:.3f} px"
    errors = _cell_errors(calib, h_true)
    assert len(errors) == len(load_cells())
    assert errors.max() < 1.0, f"{name}: worst cell off by {errors.max():.3f} px"
    # The acceptance bound is 1.0 px; measured is 0.008 px (translation) and 0.054 px (rot15_tilt).
    # This tighter guard is what catches a systematic bias creeping back in: the half-pixel
    # convention slip _render_board documents costs exactly 0.707 px and would pass the 1.0 px bound.
    assert errors.max() < 0.25, f"{name}: systematic bias? worst cell off by {errors.max():.3f} px"


def test_synthetic_board_reports_the_four_configured_tag_ids():
    image, _ = _scene(_identity_view())
    calib = calibration.calibrate_image(image)
    assert calib.tag_ids == calibration.load_tag_geometry().ids
    assert sorted(calibration.detect_tags(image, calibration.load_tag_geometry())) == sorted(calib.tag_ids.values())


def test_px_to_board_inverts_board_to_px():
    image, _ = _scene(_rotated_tilted_view())
    calib = calibration.calibrate_image(image)
    board_xy = np.array([c.board_xy_mm for c in load_cells().values()])
    assert np.allclose(calib.px_to_board(calib.board_to_px(board_xy)), board_xy, atol=1e-6)


def test_cell_px_matches_the_ground_truth_and_the_bulk_form():
    image, h_true = _scene(_identity_view())
    calib = calibration.calibrate_image(image)
    every = calib.cell_px_all()
    assert set(every) == set(load_cells())
    for cell_id in ("track-0", "track-17", "R-base-2", "Y-home-5"):
        u, v = calib.cell_px(cell_id)
        assert (u, v) == pytest.approx(every[cell_id])
        truth = _apply(h_true, load_cells()[cell_id].board_xy_mm)[0]
        assert np.linalg.norm([u - truth[0], v - truth[1]]) < 1.0, cell_id
    with pytest.raises(KeyError):
        calib.cell_px("track-999")


def test_board_bbox_covers_the_board_and_stays_inside_the_image():
    image, _ = _scene(_rotated_tilted_view())
    calib = calibration.calibrate_image(image)
    crop = calib.board_bbox_px()
    assert 0 <= crop["x"] and 0 <= crop["y"]
    assert crop["x"] + crop["w"] <= image.shape[1] and crop["y"] + crop["h"] <= image.shape[0]
    corners = calib.board_to_px([(-300.0, -300.0), (300.0, -300.0), (300.0, 300.0), (-300.0, 300.0)])
    assert crop["x"] <= corners[:, 0].min() + 1 and corners[:, 0].max() <= crop["x"] + crop["w"] + 1
    assert crop["y"] <= corners[:, 1].min() + 1 and corners[:, 1].max() <= crop["y"] + crop["h"] + 1


# --------------------------------------------------------------------------------------------
# failure modes and the written artefact
# --------------------------------------------------------------------------------------------


def test_a_covered_tag_names_the_missing_corner():
    geometry = calibration.load_tag_geometry()
    image, _ = _scene(_identity_view())
    # Paint over the tag at the (-x, -y) corner, i.e. the bottom-left of this view.
    centre = np.round(calibration.calibrate_image(image).board_to_px(geometry.centres_mm["corner_neg_x_neg_y"]))
    half = int(geometry.size_mm * PX_PER_MM)
    x, y = int(centre[0]), int(centre[1])
    image[y - half : y + half, x - half : x + half] = 255
    with pytest.raises(calibration.CalibrationError, match="corner_neg_x_neg_y"):
        calibration.calibrate_image(image)


def test_a_blank_image_is_a_calibration_error():
    with pytest.raises(calibration.CalibrationError, match="missing"):
        calibration.calibrate_image(np.full((400, 400), 255, dtype=np.uint8))


def test_calibrate_file_reports_an_unreadable_image(tmp_path):
    with pytest.raises(calibration.CalibrationError, match="cannot read an image"):
        calibration.calibrate_file(tmp_path / "nope.png")


def test_saved_calibration_round_trips_and_records_the_board_hash(tmp_path):
    image, _ = _scene(_identity_view())
    calib = calibration.calibrate_image(image, image_path="data/calib/synthetic.png")
    path = calib.save(tmp_path / "board_calib.yaml")
    back = calibration.load(path)

    assert np.allclose(back.homography, calib.homography)
    assert back.tag_ids == calib.tag_ids
    assert back.rms_px == pytest.approx(calib.rms_px)
    assert back.image_path == "data/calib/synthetic.png"
    assert back.image_size_px == (image.shape[1], image.shape[0])
    assert back.board_config_hash == config.config_hash("board")
    assert back.unmeasured == calib.unmeasured
    text = path.read_text(encoding="utf-8")
    assert "GENERATED FILE" in text and "top_crop" in text


def test_load_rejects_a_file_that_is_not_a_calibration(tmp_path):
    path = tmp_path / "board_calib.yaml"
    path.write_text("image: x\n", encoding="utf-8")
    with pytest.raises(calibration.CalibrationError, match="not a board calibration"):
        calibration.load(path)


def test_cli_prints_the_tag_ids_and_the_error_and_writes_the_yaml(tmp_path, capsys):
    image, _ = _scene(_identity_view())
    image_path = tmp_path / "board_empty.png"
    assert cv2.imwrite(str(image_path), image)
    out_path = tmp_path / "board_calib.yaml"

    assert calibration.main(["--image", str(image_path), "--out", str(out_path)]) == 0
    printed = capsys.readouterr().out
    for tag_id in calibration.load_tag_geometry().ids.values():
        assert f"tag {tag_id}" in printed
    assert "reprojection" in printed and "rms" in printed
    assert "WARNING" in printed, "placeholder tag geometry must be announced, not assumed"
    assert out_path.exists()
    assert calibration.load(out_path).rms_px < 0.5


def test_cli_exits_nonzero_on_a_bad_image(tmp_path, capsys):
    assert calibration.main(["--image", str(tmp_path / "missing.png")]) == 1
    assert "calibration failed" in capsys.readouterr().err


# --------------------------------------------------------------------------------------------
# tools/hardware_checks/brio_still.py -- the still that feeds all of the above (H-001)
#
# There is no Brio on this laptop (tools/hardware_checks/list_devices.py, 2026-09-11: four
# SunplusIT integrated-webcam nodes and the two Orbbec Ego nodes, no 046d device), so what is
# tested here is everything up to the camera: the device it would open, and that a missing one is
# reported as exit 3 rather than as a stack trace or a guessed /dev/video0.
# --------------------------------------------------------------------------------------------


def test_still_refuses_to_guess_a_device_while_the_config_is_unmeasured():
    assert config.load("cameras")["top"]["device"] == config.UNMEASURED, "this test guards the UNMEASURED case"
    with pytest.raises(brio_still.NoCamera, match="UNMEASURED"):
        brio_still.resolve_device()
    assert brio_still.resolve_device("/dev/video9") == "/dev/video9"


def test_still_exits_three_when_the_device_is_absent(tmp_path, capsys):
    code = brio_still.main(["--device", "/dev/video-does-not-exist", "--out", str(tmp_path / "board.png")])
    assert code == brio_still.NO_CAMERA == 3
    assert "does not exist" in capsys.readouterr().err


def test_still_exits_three_when_nothing_is_configured(tmp_path, capsys):
    assert brio_still.main(["--out", str(tmp_path / "board.png")]) == 3
    assert "no still captured" in capsys.readouterr().err


def test_still_insists_on_a_lossless_png(tmp_path, capsys):
    assert brio_still.main(["--device", "/dev/video9", "--out", str(tmp_path / "board.jpg")]) == 2
    assert "lossless PNG" in capsys.readouterr().err


def test_still_asks_for_4k():
    assert (brio_still.DEFAULT_WIDTH, brio_still.DEFAULT_HEIGHT) == (3840, 2160)
    assert brio_still.DEFAULT_WARMUP == 10
