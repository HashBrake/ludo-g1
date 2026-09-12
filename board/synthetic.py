"""Synthetic top-camera scenes: a board whose true homography is known, and pieces on it.

There is no photograph of the real board yet (H-001), so both board modules are verified against a
board this file *constructs*. Because the ground truth is built rather than measured, it is exact:
the board-millimetre to pixel homography is chosen here and handed back, so a test can ask "where
should this cell / this horse be" and compare against where the code under test put it.

Two users, two entry points:

``render_board`` / ``scene``
    A white board with the four AprilTags of ``config/board.yaml`` drawn at their configured
    board-frame positions, optionally warped by a view homography. This is what
    ``tests/test_calibration.py`` (T-008) detects tags in; it lives here so that
    ``tests/test_perception.py`` (T-038) can put horses on the very same image and run the two
    modules in composition.

``render_top_scene``
    A full table scene for ``board.perception``: grey table, white board, the die bowl, coloured
    horses at named cells in any of the states of CLAUDE.md 6.5, and the die in or out of the bowl.
    It carries **no** tags and returns an exact :class:`~board.calibration.Calibration` built from
    the homography it drew with, deliberately: perception is then tested against a calibration that
    is known-good, so a detection failure can never be mistaken for a calibration failure. The tag
    path has its own test.

**This is scaffolding, not a measurement (R5).** Colours here are flat, edges are hard, there is no
shadow, no specularity and no motion blur. Everything ``board/perception.py`` reports about this
scene is a statement about its *rules*, never about how it will do on the Brio. The first honest
number for that needs H-001.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from board import calibration
from engine.cells import load_cells
from runtime import config

__all__ = [
    "BOWL_BGR",
    "DIE_BGR",
    "HORSE_BGR",
    "MARGIN_PX",
    "PX_PER_MM",
    "STATES",
    "TABLE_BGR",
    "Piece",
    "apply_h",
    "calibration_from_homography",
    "identity_view",
    "render_board",
    "render_die",
    "render_pieces",
    "render_top_scene",
    "rotated_tilted_view",
    "scene",
    "warp",
]

#: Default rendering scale of the tag board and the size of the white margin around it.
PX_PER_MM = 2.0
MARGIN_PX = 100

#: Flat BGR fills. The four horse colours are chosen to land inside the HSV ranges that
#: ``config/board.yaml`` carries as placeholders; the arrow is dark so that it is never white, and
#: the die is the only white object anywhere near the bowl.
HORSE_BGR: dict[str, tuple[int, int, int]] = {
    "R": (40, 40, 225),
    "G": (60, 180, 60),
    "Y": (60, 220, 235),
    "B": (225, 120, 40),
}
ARROW_BGR: tuple[int, int, int] = (40, 40, 40)
TABLE_BGR: tuple[int, int, int] = (140, 140, 140)
BOARD_BGR: tuple[int, int, int] = (245, 245, 245)
BOWL_BGR: tuple[int, int, int] = (90, 90, 90)
DIE_BGR: tuple[int, int, int] = (250, 250, 250)

#: Piece states this renderer can draw, and what each looks like from above.
#:
#: ``standing``     the horse's footprint, a square with the arrow on top;
#: ``fallen_side``  on its side: the footprint by the horse's height, a rectangle;
#: ``fallen_back``  on its back: only the small arrow face is visible, a smaller square.
STATES: tuple[str, ...] = ("standing", "fallen_side", "fallen_back")

#: Fraction of the footprint the back face covers when the horse lies on its back.
BACK_FACE_FRACTION = 0.55


def apply_h(h: np.ndarray, pts: Any) -> np.ndarray:
    """Apply a 3x3 homography to one ``(x, y)`` or an ``(N, 2)`` array. Returns ``(N, 2)``."""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    out = np.concatenate([pts, np.ones((len(pts), 1))], axis=1) @ np.asarray(h, dtype=np.float64).T
    return out[:, :2] / out[:, 2:3]


# ----------------------------------------------------------------------------------------------
# the tag board (T-008)
# ----------------------------------------------------------------------------------------------


def render_board(
    geometry: calibration.TagGeometry,
    px_per_mm: float = PX_PER_MM,
    margin_px: int = MARGIN_PX,
) -> tuple[np.ndarray, np.ndarray]:
    """A white board image with the four tags drawn on it, plus the true board-mm -> px homography.

    The base mapping is a pure scale and flip: board +x to the right, board +y *up* the image, at
    ``px_per_mm`` pixels per millimetre with a white margin around the board outline.

    Two pixel conventions meet here and the half pixel between them is not noise. Numpy indexing
    puts the first row of the marker block at row index ``y``; OpenCV's continuous image coordinates
    put the *centre* of that pixel at ``y``, so the marker's physical top-left edge -- what the
    detector localises -- is at ``y - 0.5``. ``h_place`` is the integral mapping used to blit the
    marker blocks; the returned ground truth is that mapping shifted by half a pixel on both axes.
    """
    width_mm, height_mm = (float(v) for v in config.load("board")["size_mm"])
    width = int(round(width_mm * px_per_mm)) + 2 * margin_px
    height = int(round(height_mm * px_per_mm)) + 2 * margin_px
    h_place = np.array(
        [
            [px_per_mm, 0.0, margin_px + width_mm / 2.0 * px_per_mm],
            [0.0, -px_per_mm, margin_px + height_mm / 2.0 * px_per_mm],
            [0.0, 0.0, 1.0],
        ]
    )

    image = np.full((height, width), 255, dtype=np.uint8)
    side_px = int(round(geometry.size_mm * px_per_mm))
    dictionary = cv2.aruco.getPredefinedDictionary(geometry.dictionary)
    for key in calibration.CORNER_KEYS:
        marker = cv2.aruco.generateImageMarker(dictionary, geometry.ids[key], side_px)
        # Corner 0 of the aruco order is the marker's top-left, i.e. board (cx - s/2, cy + s/2).
        top_left = apply_h(h_place, geometry.corners_mm(key)[0])[0]
        x, y = np.round(top_left).astype(int)
        assert np.allclose(top_left, [x, y]), f"{key} does not land on a whole pixel: {top_left}"
        image[y : y + side_px, x : x + side_px] = marker
    half_pixel = np.array([[1.0, 0.0, -0.5], [0.0, 1.0, -0.5], [0.0, 0.0, 1.0]])
    return image, half_pixel @ h_place


def warp(image: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Warp ``image`` by ``h``, shifted so the result is fully in frame. Returns image and shifted h."""
    height, width = image.shape[:2]
    frame = apply_h(h, [[0, 0], [width, 0], [width, height], [0, height]])
    shift = np.array([[1.0, 0.0, -frame[:, 0].min()], [0.0, 1.0, -frame[:, 1].min()], [0.0, 0.0, 1.0]])
    h_shifted = shift @ h
    size = (
        int(np.ceil(frame[:, 0].max() - frame[:, 0].min())),
        int(np.ceil(frame[:, 1].max() - frame[:, 1].min())),
    )
    warped = cv2.warpPerspective(image, h_shifted, size, flags=cv2.INTER_LINEAR, borderValue=255)
    return warped, h_shifted


def scene(view: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Render the tag board, apply the view homography, and return the image and the true board -> px map."""
    geometry = calibration.load_tag_geometry()
    base, h_true = render_board(geometry)
    warped, h_view = warp(base, view)
    return warped, h_view @ h_true


def identity_view() -> np.ndarray:
    """A pure translation: the board seen straight on."""
    return np.array([[1.0, 0.0, 37.0], [0.0, 1.0, -19.0], [0.0, 0.0, 1.0]])


def rotated_tilted_view(image_shape: tuple[int, int] = (1400, 1400)) -> np.ndarray:
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


# ----------------------------------------------------------------------------------------------
# pieces and the table scene (T-038)
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Piece:
    """One horse to draw: which colour, where, and in which of the states of CLAUDE.md 6.5.

    ``cell_id`` places it at that cell's centre; ``offset_mm`` displaces it from there, which is how
    a "between cells" or "missed the magnet" horse is drawn. ``board_xy_mm`` overrides both and puts
    the piece at an absolute board position.
    """

    color: str
    cell_id: str | None = None
    state: str = "standing"
    offset_mm: tuple[float, float] = (0.0, 0.0)
    angle_deg: float = 0.0
    board_xy_mm: tuple[float, float] | None = None

    def centre_mm(self, cells: dict[str, Any]) -> tuple[float, float]:
        if self.board_xy_mm is not None:
            return (float(self.board_xy_mm[0]), float(self.board_xy_mm[1]))
        if self.cell_id is None:
            raise ValueError("a Piece needs either a cell_id or a board_xy_mm")
        cx, cy = cells[self.cell_id].board_xy_mm
        return (float(cx) + self.offset_mm[0], float(cy) + self.offset_mm[1])

    def size_mm(self, footprint_mm: float, height_mm: float) -> tuple[float, float]:
        """The rectangle this piece shows from above, in millimetres."""
        if self.state == "standing":
            return (footprint_mm, footprint_mm)
        if self.state == "fallen_side":
            return (footprint_mm, height_mm)
        if self.state == "fallen_back":
            side = footprint_mm * BACK_FACE_FRACTION
            return (side, side)
        raise ValueError(f"unknown piece state {self.state!r}; known: {', '.join(STATES)}")


def _rect_mm(centre: tuple[float, float], size: tuple[float, float], angle_deg: float) -> np.ndarray:
    """The four corners of a rectangle in board millimetres, rotated about its centre."""
    w, h = size[0] / 2.0, size[1] / 2.0
    corners = np.array([[-w, -h], [w, -h], [w, h], [-w, h]], dtype=np.float64)
    theta = np.deg2rad(angle_deg)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return corners @ rot.T + np.asarray(centre, dtype=np.float64)


def _fill(image: np.ndarray, h: np.ndarray, pts_mm: np.ndarray, color: tuple[int, int, int]) -> None:
    """Fill a board-millimetre polygon, projected through ``h``, with 1/16 px subpixel accuracy."""
    px = apply_h(h, pts_mm)
    cv2.fillConvexPoly(image, np.round(px * 16.0).astype(np.int32), color, lineType=cv2.LINE_8, shift=4)


def _circle_mm(centre: tuple[float, float], radius_mm: float, segments: int = 64) -> np.ndarray:
    angles = np.linspace(0.0, 2.0 * np.pi, segments, endpoint=False)
    return np.stack([centre[0] + radius_mm * np.cos(angles), centre[1] + radius_mm * np.sin(angles)], axis=1)


def render_pieces(
    image: np.ndarray,
    h: np.ndarray,
    pieces: list[Piece],
    root: Path | str | None = None,
) -> np.ndarray:
    """Draw ``pieces`` onto a BGR ``image`` whose board-mm -> px mapping is ``h``. Modifies in place.

    The arrow on top (Q-006: its orientation does not matter to the engine) is drawn as a dark
    triangle, so it takes a bite out of the colour blob exactly as the printed one will.
    """
    board = config.load("board", root)
    footprint = float(board["horse"]["footprint_mm"])
    height_mm = float(board["horse"]["height_mm"])
    cells = load_cells(root)
    for piece in pieces:
        if piece.color not in HORSE_BGR:
            raise ValueError(f"unknown horse colour {piece.color!r}; known: {', '.join(sorted(HORSE_BGR))}")
        centre = piece.centre_mm(cells)
        size = piece.size_mm(footprint, height_mm)
        _fill(image, h, _rect_mm(centre, size, piece.angle_deg), HORSE_BGR[piece.color])
        if piece.state != "fallen_back":       # on its back, the arrow face is what points at us
            arrow = min(size) * 0.42
            tri = _rect_mm(centre, (arrow, arrow), piece.angle_deg)[:3]
            _fill(image, h, tri, ARROW_BGR)
    return image


def render_die(
    image: np.ndarray,
    h: np.ndarray,
    xy_mm: tuple[float, float],
    size_mm: float,
    angle_deg: float = 0.0,
) -> np.ndarray:
    """Draw the die as a white square centred at ``xy_mm``. Modifies ``image`` in place."""
    _fill(image, h, _rect_mm(xy_mm, (size_mm, size_mm), angle_deg), DIE_BGR)
    return image


def calibration_from_homography(
    h: np.ndarray,
    image_size_px: tuple[int, int],
    root: Path | str | None = None,
    image_path: str = "<synthetic>",
) -> calibration.Calibration:
    """An exact :class:`~board.calibration.Calibration` for a homography we chose ourselves.

    ``rms_px`` and ``max_px`` are 0 because nothing was fitted: this is the ground truth, not a
    measurement of one. Only synthetic scenes may use it.
    """
    return calibration.Calibration(
        homography=np.asarray(h, dtype=np.float64),
        tag_ids=dict(calibration.load_tag_geometry(root).ids),
        rms_px=0.0,
        max_px=0.0,
        image_path=image_path,
        image_size_px=(int(image_size_px[0]), int(image_size_px[1])),
        family=str(config.load("board", root)["apriltags"]["family"]),
        tag_size_mm=float(config.load("board", root)["apriltags"]["size_mm"]),
        board_config_hash=config.config_hash("board", root),
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        unmeasured=("<synthetic scene: not a calibration>",),
    )


def render_top_scene(
    pieces: list[Piece] | None = None,
    die_xy_mm: tuple[float, float] | None = None,
    *,
    frame: tuple[int, int] = (640, 480),
    view: np.ndarray | None = None,
    fit: float = 0.94,
    root: Path | str | None = None,
) -> tuple[np.ndarray, calibration.Calibration]:
    """A whole table as the Brio would see it, with an exact calibration for it.

    ``frame`` is the output size in pixels; the scale is chosen so that the board *and* the bowl fit
    inside it with ``fit`` of the frame used. ``die_xy_mm`` is where the die lies in the board frame
    (``None`` for no die at all); ``view`` is an optional homography applied about the image centre,
    for testing under rotation and projective tilt.

    Returns the BGR image and the calibration that maps board millimetres onto it.
    """
    board = config.load("board", root)
    width_px, height_px = (int(frame[0]), int(frame[1]))
    half_x, half_y = (float(v) / 2.0 for v in board["size_mm"])
    bowl_xy, bowl_diameter = bowl_geometry(root)

    # The extent the scene has to cover: the board outline and the whole bowl.
    min_x = min(-half_x, bowl_xy[0] - bowl_diameter / 2.0)
    max_x = max(half_x, bowl_xy[0] + bowl_diameter / 2.0)
    min_y = min(-half_y, bowl_xy[1] - bowl_diameter / 2.0)
    max_y = max(half_y, bowl_xy[1] + bowl_diameter / 2.0)
    scale = fit * min(width_px / (max_x - min_x), height_px / (max_y - min_y))
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    h = np.array(
        [
            [scale, 0.0, width_px / 2.0 - scale * cx],
            [0.0, -scale, height_px / 2.0 + scale * cy],
            [0.0, 0.0, 1.0],
        ]
    )
    if view is not None:
        centre = np.array([[1.0, 0.0, width_px / 2.0], [0.0, 1.0, height_px / 2.0], [0.0, 0.0, 1.0]])
        h = centre @ np.asarray(view, dtype=np.float64) @ np.linalg.inv(centre) @ h

    image = np.full((height_px, width_px, 3), TABLE_BGR, dtype=np.uint8)
    _fill(image, h, _rect_mm((0.0, 0.0), (2 * half_x, 2 * half_y), 0.0), BOARD_BGR)
    cv2.fillPoly(
        image,
        [np.round(apply_h(h, _circle_mm(bowl_xy, bowl_diameter / 2.0)) * 16.0).astype(np.int32)],
        BOWL_BGR,
        lineType=cv2.LINE_8,
        shift=4,
    )
    render_pieces(image, h, list(pieces or []), root)
    if die_xy_mm is not None:
        render_die(image, h, die_xy_mm, float(board["die"]["size_mm"]))
    return image, calibration_from_homography(h, (width_px, height_px), root, image_path="<synthetic top scene>")


def bowl_geometry(root: Path | str | None = None) -> tuple[tuple[float, float], float]:
    """The bowl's centre (board mm) and diameter, measured if it has been, placeholder if not.

    Same precedence as ``board.perception``: ``die.bowl_centre_mm`` / ``die.bowl_diameter_mm`` when
    they hold numbers, ``perception.bowl.*`` while they are still the literal ``UNMEASURED``.
    """
    from board import perception  # local: perception imports this module's siblings, not this one

    bowl = perception.load_rules(root).bowl
    return bowl.centre_mm, bowl.diameter_mm
