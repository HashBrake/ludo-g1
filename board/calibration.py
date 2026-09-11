"""Board-frame to Brio-pixel calibration from the four corner AprilTags.

Everything downstream that has to point at a board cell -- the ``top`` crop of CLAUDE.md 5.3, the two
goal heatmap channels, ``engine.cells.load_cells(top_px=...)``, the perception check after each
primitive -- needs one mapping between the board frame that ``config/board.yaml`` defines (origin at
the board centre, +x to the board's right edge, +y to its far edge, mm) and pixels in the fixed
top-down Brio frame. This module produces that mapping, as a plane-to-plane homography, from one
still of the empty board.

How it works
------------
Four AprilTags sit at the four corners of the board. ``config/board.yaml`` says which tag id is at
which corner and where each tag centre is in the board frame; from the tag's ``size_mm`` that gives
four known board-frame points per tag, sixteen in all. The detector (OpenCV's ``cv2.aruco`` with the
AprilTag 36h11 dictionary; ``pupil-apriltags`` is not needed and is not a dependency) returns the
same sixteen points in pixels, and ``cv2.findHomography(..., cv2.RANSAC)`` fits the 3x3 that maps one
onto the other. The fit's reprojection RMS in pixels is reported and stored: it is the only honest
statement of how good the calibration is, and a jump in it after the camera is bumped is the signal
to re-run this.

Tag orientation assumption
--------------------------
Each tag is assumed to be printed and mounted *upright in the board frame*: the tag's own "up" points
along board +y and its "right" along board +x. ``cv2.aruco`` returns a tag's four corners in its
canonical reading order (top-left, top-right, bottom-right, bottom-left of the marker as printed), so
under that assumption corner ``k`` of tag ``t`` has a known board-frame position. If a tag is mounted
rotated by a quarter turn, its four corners are still detected but paired with the wrong board points,
and the reprojection RMS jumps by roughly the tag size -- loudly, not silently. Re-mount the tag or
record the real centres; do not paper over it.

Units and direction
-------------------
``board_to_px`` takes millimetres in the board frame and returns pixels in the *full, uncropped* Brio
frame; ``px_to_board`` is its inverse. Nothing here knows about the 640x480 the policy sees: that is a
crop plus a resize applied later, and ``top_crop`` in the written yaml is the board's bounding box in
the full frame, offered for ``config/cameras.yaml``'s ``top.crop``.

CLI
---
``python -m board.calibration --image data/calib/board_empty.png`` prints the four tag ids, the
reprojection error and the board's pixel bounding box, and writes ``config/board_calib.yaml``. That
file is a produced artefact, not a hand-maintained config: it is not in ``runtime.config.REQUIRED_KEYS``
and is read back with :func:`load` rather than ``config.load``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from engine.cells import load_cells
from runtime import config

__all__ = [
    "CORNER_KEYS",
    "Calibration",
    "CalibrationError",
    "TagGeometry",
    "calibrate_image",
    "detect_tags",
    "load",
    "load_tag_geometry",
    "main",
]

#: The four corner keys of ``apriltags.ids``/``apriltags.centres_mm``, named in the board frame and
#: listed anticlockwise from the near-left corner. ``neg_x_neg_y`` is the corner at (-x, -y).
CORNER_KEYS: tuple[str, ...] = (
    "corner_neg_x_neg_y",
    "corner_pos_x_neg_y",
    "corner_pos_x_pos_y",
    "corner_neg_x_pos_y",
)

_CORNER_SIGNS: dict[str, tuple[float, float]] = {
    "corner_neg_x_neg_y": (-1.0, -1.0),
    "corner_pos_x_neg_y": (+1.0, -1.0),
    "corner_pos_x_pos_y": (+1.0, +1.0),
    "corner_neg_x_pos_y": (-1.0, +1.0),
}

#: AprilTag families this module can detect, mapped to the OpenCV predefined dictionary. The board
#: uses 36h11; the others are here so that a board printed with a different family fails at config
#: load with a list of what is supported rather than at detection with an empty result.
FAMILIES: dict[str, int] = {
    "tag36h11": cv2.aruco.DICT_APRILTAG_36h11,
    "tag25h9": cv2.aruco.DICT_APRILTAG_25h9,
    "tag16h5": cv2.aruco.DICT_APRILTAG_16h5,
}


class CalibrationError(ValueError):
    """The board could not be calibrated from this image, or its tag config is unusable."""


@dataclass(frozen=True)
class TagGeometry:
    """Where the four tags are, in the board frame, straight out of ``config/board.yaml``."""

    family: str
    size_mm: float
    #: corner key -> tag id
    ids: dict[str, int]
    #: corner key -> tag centre in the board frame, mm
    centres_mm: dict[str, tuple[float, float]]
    #: dotted paths under ``apriltags`` that are still UNMEASURED placeholders
    unmeasured: tuple[str, ...]

    @property
    def dictionary(self) -> int:
        return FAMILIES[self.family]

    def corners_mm(self, corner_key: str) -> np.ndarray:
        """The tag's four corners in the board frame, mm, in ``cv2.aruco`` corner order.

        Order is the marker's own reading order -- top-left, top-right, bottom-right, bottom-left --
        under the upright-mounting assumption documented at the top of this module, so board +y is
        the marker's up and board +x its right.
        """
        cx, cy = self.centres_mm[corner_key]
        h = self.size_mm / 2.0
        return np.array(
            [[cx - h, cy + h], [cx + h, cy + h], [cx + h, cy - h], [cx - h, cy - h]],
            dtype=np.float64,
        )


def _tag_centres(tags: dict, size_mm: float, board_size_mm: tuple[float, float]) -> dict[str, tuple[float, float]]:
    """Tag centres from ``apriltags.centres_mm``, or derived from ``tag_inset_mm`` while it is absent."""
    given = tags.get("centres_mm")
    if isinstance(given, dict):
        centres: dict[str, tuple[float, float]] = {}
        for key in CORNER_KEYS:
            if key not in given:
                raise CalibrationError(f"config/board.yaml: apriltags.centres_mm is missing {key!r}")
            xy = given[key]
            if not isinstance(xy, (list, tuple)) or len(xy) != 2 or not all(isinstance(v, (int, float)) for v in xy):
                raise CalibrationError(f"config/board.yaml: apriltags.centres_mm.{key} must be two numbers, got {xy!r}")
            centres[key] = (float(xy[0]), float(xy[1]))
        return centres
    if given != config.UNMEASURED:
        raise CalibrationError(
            f"config/board.yaml: apriltags.centres_mm must be a mapping of the four corner keys or the "
            f"literal {config.UNMEASURED}, got {given!r}"
        )
    inset = tags.get("tag_inset_mm")
    if not isinstance(inset, (int, float)):
        raise CalibrationError(
            "config/board.yaml: apriltags.centres_mm is UNMEASURED and apriltags.tag_inset_mm is missing, "
            "so the tag centres cannot be derived; measure one of the two off the printed board"
        )
    offsets = [half - float(inset) - size_mm / 2.0 for half in (board_size_mm[0] / 2.0, board_size_mm[1] / 2.0)]
    if min(offsets) <= 0.0:
        raise CalibrationError(
            f"config/board.yaml: apriltags.tag_inset_mm={inset} and size_mm={size_mm} put the tag centres "
            f"off the {board_size_mm[0]} x {board_size_mm[1]} mm board"
        )
    return {key: (sx * offsets[0], sy * offsets[1]) for key, (sx, sy) in _CORNER_SIGNS.items()}


def load_tag_geometry(root: Path | str | None = None) -> TagGeometry:
    """Read the ``apriltags`` block of ``config/board.yaml``.

    Raises :class:`CalibrationError` for an unsupported family, a non-positive tag size, ids that are
    not four distinct integers, or tag centres that can neither be read nor derived.
    """
    board = config.load("board", root)
    tags = board["apriltags"]

    family = tags.get("family")
    if family not in FAMILIES:
        raise CalibrationError(
            f"config/board.yaml: apriltags.family is {family!r}; supported: {', '.join(sorted(FAMILIES))}"
        )
    size_mm = tags.get("size_mm")
    if not isinstance(size_mm, (int, float)) or size_mm <= 0:
        raise CalibrationError(f"config/board.yaml: apriltags.size_mm must be a positive number, got {size_mm!r}")
    size_mm = float(size_mm)

    raw_ids = tags.get("ids")
    if not isinstance(raw_ids, dict) or set(raw_ids) != set(CORNER_KEYS):
        raise CalibrationError(
            f"config/board.yaml: apriltags.ids must hold exactly the keys {', '.join(CORNER_KEYS)}, "
            f"got {sorted(raw_ids) if isinstance(raw_ids, dict) else raw_ids!r}"
        )
    ids: dict[str, int] = {}
    for key in CORNER_KEYS:
        value = raw_ids[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise CalibrationError(
                f"config/board.yaml: apriltags.ids.{key} must be a non-negative integer, got {value!r}"
            )
        ids[key] = int(value)
    if len(set(ids.values())) != len(CORNER_KEYS):
        raise CalibrationError(f"config/board.yaml: apriltags.ids must be four distinct ids, got {ids}")

    size = board["size_mm"]
    board_size = (float(size[0]), float(size[1]))
    centres = _tag_centres(tags, size_mm, board_size)
    still_unmeasured = tuple(p for p in config.unmeasured("board", root) if p.startswith("apriltags."))
    return TagGeometry(family=family, size_mm=size_mm, ids=ids, centres_mm=centres, unmeasured=still_unmeasured)


def detect_tags(image: np.ndarray, geometry: TagGeometry) -> dict[int, np.ndarray]:
    """Detect every tag of ``geometry``'s family in ``image``.

    Returns tag id -> ``(4, 2)`` float array of corners in pixels, in ``cv2.aruco`` order. Corner
    refinement is on (``CORNER_REFINE_SUBPIX``): the homography is only as good as the corners are.
    """
    if image is None or image.ndim not in (2, 3):
        raise CalibrationError(f"expected a 2-D or 3-D image array, got {None if image is None else image.shape!r}")
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(geometry.dictionary), params)
    corners, ids, _ = detector.detectMarkers(gray)
    found: dict[int, np.ndarray] = {}
    if ids is None:
        return found
    for quad, tag_id in zip(corners, ids.flatten(), strict=True):
        found[int(tag_id)] = np.asarray(quad, dtype=np.float64).reshape(4, 2)
    return found


@dataclass(frozen=True)
class Calibration:
    """The board-frame to Brio-pixel homography and how well it fits.

    ``homography`` maps board millimetres to pixels in the full Brio frame. ``rms_px`` and ``max_px``
    are the reprojection error of the sixteen tag corners the fit was made from; they are the only
    claim this object makes about its own quality (R5).
    """

    homography: np.ndarray            # (3, 3), board mm -> px
    tag_ids: dict[str, int]           # corner key -> tag id actually used
    rms_px: float
    max_px: float
    image_path: str
    image_size_px: tuple[int, int]    # (width, height)
    family: str
    tag_size_mm: float
    board_config_hash: str
    created_at: str
    unmeasured: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        h = np.asarray(self.homography, dtype=np.float64)
        if h.shape != (3, 3):
            raise CalibrationError(f"homography must be 3x3, got {h.shape}")
        object.__setattr__(self, "homography", h)

    @property
    def homography_inv(self) -> np.ndarray:
        """Pixels to board millimetres."""
        return np.linalg.inv(self.homography)

    @staticmethod
    def _apply(h: np.ndarray, points: Any) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64)
        single = pts.ndim == 1
        pts = pts.reshape(-1, 2)
        homogeneous = np.concatenate([pts, np.ones((len(pts), 1))], axis=1) @ h.T
        w = homogeneous[:, 2:3]
        if np.any(np.abs(w) < 1e-12):
            raise CalibrationError("point maps to the horizon of the homography (w = 0)")
        out = homogeneous[:, :2] / w
        return out[0] if single else out

    def board_to_px(self, xy_mm: Any) -> np.ndarray:
        """Board millimetres -> Brio pixels. Takes one ``(x, y)`` or an ``(N, 2)`` array."""
        return self._apply(self.homography, xy_mm)

    def px_to_board(self, uv: Any) -> np.ndarray:
        """Brio pixels -> board millimetres. Takes one ``(u, v)`` or an ``(N, 2)`` array."""
        return self._apply(self.homography_inv, uv)

    def cell_px(self, cell_id: str, root: Path | str | None = None) -> tuple[float, float]:
        """Centre of ``cell_id`` in Brio pixels. Raises ``KeyError`` for an id the board does not define."""
        cell = load_cells(root)[cell_id]
        u, v = self.board_to_px(cell.board_xy_mm)
        return float(u), float(v)

    def cell_px_all(self, root: Path | str | None = None) -> dict[str, tuple[float, float]]:
        """Every cell in ``config/board.yaml`` mapped to Brio pixels, ready for ``load_cells(top_px=...)``."""
        cells = load_cells(root)
        pts = self.board_to_px([c.board_xy_mm for c in cells.values()])
        return {cid: (float(u), float(v)) for cid, (u, v) in zip(cells, pts, strict=True)}

    def board_bbox_px(self, root: Path | str | None = None) -> dict[str, int]:
        """Axis-aligned pixel bounding box of the board outline, clipped to the image.

        Offered as ``config/cameras.yaml``'s ``top.crop``: the region the ``top`` observation is cut
        from before the resize the policy sees (CLAUDE.md 5.3).
        """
        w_mm, h_mm = (float(v) for v in config.load("board", root)["size_mm"])
        corners = [(sx * w_mm / 2.0, sy * h_mm / 2.0) for sx, sy in _CORNER_SIGNS.values()]
        pts = self.board_to_px(corners)
        width, height = self.image_size_px
        x0 = max(0, int(np.floor(pts[:, 0].min())))
        y0 = max(0, int(np.floor(pts[:, 1].min())))
        x1 = min(width, int(np.ceil(pts[:, 0].max())))
        y1 = min(height, int(np.ceil(pts[:, 1].max())))
        return {"x": x0, "y": y0, "w": max(0, x1 - x0), "h": max(0, y1 - y0)}

    def to_dict(self, root: Path | str | None = None) -> dict:
        """The written form of ``config/board_calib.yaml``."""
        return {
            "created_at": self.created_at,
            "image": self.image_path,
            "image_size_px": list(self.image_size_px),
            "apriltags": {
                "family": self.family,
                "size_mm": self.tag_size_mm,
                "ids": {key: int(self.tag_ids[key]) for key in CORNER_KEYS},
            },
            "homography_board_mm_to_top_px": [[float(v) for v in row] for row in self.homography],
            "reprojection_rms_px": float(self.rms_px),
            "reprojection_max_px": float(self.max_px),
            "top_crop": self.board_bbox_px(root),
            "board_config_hash": self.board_config_hash,
            "unmeasured_board_keys": list(self.unmeasured),
        }

    def save(self, path: Path | str, root: Path | str | None = None) -> Path:
        """Write ``config/board_calib.yaml`` (or ``path``). Returns the path written."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# Board calibration produced by `python -m board.calibration` (T-008). GENERATED FILE:\n"
            "# do not hand-edit, re-run the CLI on a new still instead. It is not one of the six files\n"
            "# runtime/config.py validates; board.calibration.load() reads it back.\n"
            "# `homography_board_mm_to_top_px` maps board millimetres (config/board.yaml frame) to\n"
            "# pixels in the FULL Brio frame; `top_crop` is the board's bounding box in that frame,\n"
            "# offered for config/cameras.yaml top.crop.\n"
        )
        path.write_text(header + yaml.safe_dump(self.to_dict(root), sort_keys=False), encoding="utf-8")
        return path


def load(path: Path | str) -> Calibration:
    """Read back a ``config/board_calib.yaml`` written by :meth:`Calibration.save`."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CalibrationError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CalibrationError(f"{path}: not valid yaml: {exc}") from exc
    if not isinstance(data, dict):
        raise CalibrationError(f"{path}: top level must be a mapping")
    try:
        return Calibration(
            homography=np.asarray(data["homography_board_mm_to_top_px"], dtype=np.float64),
            tag_ids=dict(data["apriltags"]["ids"]),
            rms_px=float(data["reprojection_rms_px"]),
            max_px=float(data["reprojection_max_px"]),
            image_path=str(data["image"]),
            image_size_px=tuple(int(v) for v in data["image_size_px"]),  # type: ignore[arg-type]
            family=str(data["apriltags"]["family"]),
            tag_size_mm=float(data["apriltags"]["size_mm"]),
            board_config_hash=str(data["board_config_hash"]),
            created_at=str(data["created_at"]),
            unmeasured=tuple(data.get("unmeasured_board_keys", ())),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationError(f"{path}: not a board calibration: {exc}") from exc


def calibrate_image(
    image: np.ndarray,
    image_path: str = "<array>",
    root: Path | str | None = None,
    geometry: TagGeometry | None = None,
) -> Calibration:
    """Calibrate from one already-loaded top-down image of the empty board.

    Raises :class:`CalibrationError` naming the missing corner(s) when any of the four tags is not
    detected: three tags do define a homography, but a board calibrated from three corners is a board
    whose fourth corner nobody checked, and the whole point of this measurement is that it is checked.
    """
    geometry = geometry or load_tag_geometry(root)
    detected = detect_tags(image, geometry)
    missing = [f"{key} (id {geometry.ids[key]})" for key in CORNER_KEYS if geometry.ids[key] not in detected]
    if missing:
        raise CalibrationError(
            f"detected {sorted(detected)} of the four board tags {sorted(geometry.ids.values())}; "
            f"missing: {', '.join(missing)}. Check that every tag is unobstructed and in frame."
        )

    board_pts = np.vstack([geometry.corners_mm(key) for key in CORNER_KEYS])
    px_pts = np.vstack([detected[geometry.ids[key]] for key in CORNER_KEYS])
    h, mask = cv2.findHomography(board_pts, px_pts, cv2.RANSAC, ransacReprojThreshold=3.0)
    if h is None:
        raise CalibrationError("cv2.findHomography failed on the sixteen tag corners (degenerate tag layout?)")
    inliers = int(mask.sum()) if mask is not None else len(board_pts)
    if inliers < len(board_pts):
        raise CalibrationError(
            f"only {inliers} of {len(board_pts)} tag corners fit one homography; the tags, the board "
            f"geometry in config/board.yaml, or the tag mounting orientation disagree"
        )

    height, width = image.shape[:2]
    calib = Calibration(
        homography=h,
        tag_ids=dict(geometry.ids),
        rms_px=0.0,
        max_px=0.0,
        image_path=image_path,
        image_size_px=(int(width), int(height)),
        family=geometry.family,
        tag_size_mm=geometry.size_mm,
        board_config_hash=config.config_hash("board", root),
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        unmeasured=geometry.unmeasured,
    )
    residuals = np.linalg.norm(calib.board_to_px(board_pts) - px_pts, axis=1)
    object.__setattr__(calib, "rms_px", float(np.sqrt(np.mean(residuals**2))))
    object.__setattr__(calib, "max_px", float(residuals.max()))
    return calib


def calibrate_file(image_path: Path | str, root: Path | str | None = None) -> Calibration:
    """Calibrate from a still on disk."""
    path = Path(image_path)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise CalibrationError(f"cannot read an image from {path} (missing file, or an unsupported format)")
    return calibrate_image(image, image_path=str(path), root=root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m board.calibration",
        description="Calibrate the board-frame to Brio-pixel homography from the four corner AprilTags.",
    )
    parser.add_argument("--image", required=True, type=Path, help="top-down still of the empty board (H-001)")
    parser.add_argument(
        "--out",
        type=Path,
        default=config.CONFIG_DIR / "board_calib.yaml",
        help="where to write the calibration (default: config/board_calib.yaml)",
    )
    parser.add_argument("--no-write", action="store_true", help="report only, write nothing")
    args = parser.parse_args(argv)

    try:
        geometry = load_tag_geometry()
        calib = calibrate_file(args.image)
    except CalibrationError as exc:
        print(f"calibration failed: {exc}", file=sys.stderr)
        return 1

    print(f"image            {calib.image_path}  {calib.image_size_px[0]}x{calib.image_size_px[1]}")
    print(f"family           {calib.family}  tag size {calib.tag_size_mm} mm")
    for key in CORNER_KEYS:
        cx, cy = calib.board_to_px(geometry.centres_mm[key])
        print(f"tag {calib.tag_ids[key]:<4}         {key:<20} at ({cx:8.2f}, {cy:8.2f}) px")
    print(f"reprojection     rms {calib.rms_px:.3f} px, max {calib.max_px:.3f} px  (16 tag corners)")
    crop = calib.board_bbox_px()
    print(f"board bbox       x={crop['x']} y={crop['y']} w={crop['w']} h={crop['h']}  (for cameras.yaml top.crop)")
    if calib.unmeasured:
        print(
            "WARNING          config/board.yaml still has placeholder tag geometry: "
            + ", ".join(calib.unmeasured)
            + "\n                 the homography is only as true as those numbers; measure them off the printed board."
        )
    if not args.no_write:
        print(f"wrote            {calib.save(args.out)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
