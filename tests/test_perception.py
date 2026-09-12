"""Board perception from the top camera on synthetic scenes (T-038; CLAUDE.md 5.5, 6.5, R5).

No Brio still of the real board exists yet (H-001), so everything here runs against scenes
``board/synthetic.py`` renders from the very same ``config/board.yaml`` the detector reads: the
horses are at cells the config defines, the calibration is the homography the scene was drawn with,
and the truth each test compares against is therefore exact rather than eyeballed.

What that does and does not buy, stated plainly (R5): it pins the *rules* -- which blob is a
standing horse, which is a fallen one, which before/after difference is which failure mode of 6.5 --
and it says nothing at all about how the detector will do on a photograph, where the colours are not
flat, the light is not even and the pieces cast shadows. The numbers below are not a detection rate.
"""

from __future__ import annotations

import copy
import random
import time

import numpy as np
import pytest
import yaml

from board import perception, synthetic
from board.perception import (
    BoardView,
    FailureMode,
    Perception,
    PerceptionError,
    Placement,
    Pose,
    TopCameraPerception,
)
from engine.cells import load_cells
from engine.interface import Cell, Command, Primitive
from runtime import config

Piece = synthetic.Piece
FRAME = (640, 480)


def cell(cell_id: str) -> Cell:
    c = load_cells()[cell_id]
    return Cell(id=c.id, board_xy_mm=c.board_xy_mm, top_px=None)


def eye_on(pieces: list[Piece], die_xy_mm=None, *, frame=FRAME, view=None, fit=0.94):
    """Render a scene and return the detector for it plus the frame."""
    image, calib = synthetic.render_top_scene(pieces, die_xy_mm, frame=frame, view=view, fit=fit)
    return TopCameraPerception(calib), image


def view_of(pieces: list[Piece], die_xy_mm=None, **kwargs) -> BoardView:
    eye, image = eye_on(pieces, die_xy_mm, **kwargs)
    return eye.detect(image)


def move(src: str, dst: str, horse_id: str | None = "R0") -> Command:
    return Command(primitive=Primitive.MOVE, src=cell(src), dst=cell(dst), horse_id=horse_id)


def bowl_xy() -> tuple[float, float]:
    return perception.load_rules().bowl.centre_mm


# --------------------------------------------------------------------------------------------
# the rules come from config/board.yaml and say they are placeholders
# --------------------------------------------------------------------------------------------


def test_rules_are_read_from_the_board_config_and_flagged_unmeasured():
    rules = perception.load_rules()
    board = config.load("board")
    assert rules.colors == tuple(board["layout"]["colors"])
    assert rules.footprint_mm == board["horse"]["footprint_mm"]
    assert rules.die_size_mm == board["die"]["size_mm"]
    assert len(rules.hsv_ranges["R"]) == 2, "red wraps the hue origin and needs two bands"
    # Every threshold the detector uses is still a guess, and says so (docs/config.md).
    assert "perception" in config.unmeasured("board")
    assert "perception" in rules.unmeasured


def test_the_bowl_falls_back_to_the_placeholder_while_the_measured_one_is_absent(tmp_path):
    board = config.load("board")
    assert board["die"]["bowl_centre_mm"] == config.UNMEASURED, "this test guards the UNMEASURED case"
    placeholder = perception.load_rules()
    assert placeholder.bowl.measured is False
    assert placeholder.bowl.centre_mm == tuple(board["perception"]["bowl"]["centre_mm"])

    board["die"]["bowl_centre_mm"] = [10.0, -350.0]
    board["die"]["bowl_diameter_mm"] = 140.0
    (tmp_path / "board.yaml").write_text(yaml.safe_dump(board), encoding="utf-8")
    measured = perception.load_rules(root=tmp_path)
    assert measured.bowl.measured is True
    assert measured.bowl.centre_mm == (10.0, -350.0) and measured.bowl.diameter_mm == 140.0


def test_a_snap_radius_two_cells_could_claim_is_refused(tmp_path):
    board = config.load("board")
    board["perception"]["cell_snap_radius_mm"] = board["layout"]["cell_pitch_mm"] / 2.0
    (tmp_path / "board.yaml").write_text(yaml.safe_dump(board), encoding="utf-8")
    with pytest.raises(PerceptionError, match="half the cell"):
        perception.load_rules(root=tmp_path)


def test_a_malformed_hsv_band_is_named(tmp_path):
    board = config.load("board")
    board["perception"]["hsv_ranges"]["G"] = [[[200, 0, 0], [10, 255, 255]]]
    (tmp_path / "board.yaml").write_text(yaml.safe_dump(board), encoding="utf-8")
    with pytest.raises(PerceptionError, match="hsv_ranges.G"):
        perception.load_rules(root=tmp_path)


def test_a_colour_with_no_band_is_named(tmp_path):
    board = config.load("board")
    del board["perception"]["hsv_ranges"]["Y"]
    (tmp_path / "board.yaml").write_text(yaml.safe_dump(board), encoding="utf-8")
    with pytest.raises(PerceptionError, match="no band for colour"):
        perception.load_rules(root=tmp_path)


# --------------------------------------------------------------------------------------------
# occupancy on random boards
# --------------------------------------------------------------------------------------------


def _random_board(rng: random.Random, n: int = 10) -> tuple[list[Piece], dict[str, str]]:
    ids = list(load_cells())
    colors = perception.load_rules().colors
    chosen = rng.sample(ids, n)
    pieces = [Piece(color=rng.choice(colors), cell_id=cell_id, angle_deg=rng.uniform(-20.0, 20.0))
              for cell_id in chosen]
    return pieces, {p.cell_id: p.color for p in pieces}


@pytest.mark.parametrize("seed", range(20))
def test_occupancy_is_recovered_exactly_on_twenty_random_boards(seed):
    pieces, expected = _random_board(random.Random(seed))
    board = view_of(pieces, bowl_xy())
    assert board.occupancy() == expected, f"seed {seed}"
    assert all(horse.pose is Pose.STANDING for horse in board.horses)
    assert board.loose == ()
    assert board.die.seen and board.die.in_bowl


def test_occupancy_survives_a_rotated_and_tilted_view():
    pieces, expected = _random_board(random.Random(101), n=12)
    board = view_of(pieces, bowl_xy(), view=synthetic.rotated_tilted_view(FRAME[::-1]), fit=0.62)
    assert board.occupancy() == expected
    assert all(horse.placement is Placement.AT_CELL for horse in board.horses)


def test_detection_composes_with_a_calibration_fitted_to_the_real_tags():
    """The tag path (T-008) and the detection path (T-038) on one image, end to end."""
    import cv2

    from board import calibration

    gray, h_true = synthetic.scene(synthetic.identity_view())
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    pieces = [Piece("R", "track-12"), Piece("G", "Y-home-2"), Piece("B", "B-base-1")]
    synthetic.render_pieces(image, h_true, pieces)
    calib = calibration.calibrate_image(image, image_path="<synthetic tags + horses>")
    assert calib.rms_px < 0.5
    assert TopCameraPerception(calib).detect(image).occupancy() == {
        "track-12": "R", "Y-home-2": "G", "B-base-1": "B",
    }


def test_a_frame_that_is_not_the_calibrated_one_is_refused():
    eye, image = eye_on([Piece("R", "track-12")])
    with pytest.raises(PerceptionError, match="calibration is for"):
        eye.detect(np.zeros((240, 320, 3), dtype=np.uint8))
    with pytest.raises(PerceptionError, match="HxWx3"):
        eye.detect(np.zeros((480, 640), dtype=np.uint8))


# --------------------------------------------------------------------------------------------
# the variants of CLAUDE.md 6.5, one by one
# --------------------------------------------------------------------------------------------


def test_a_horse_on_its_side_is_fallen_not_standing():
    board = view_of([Piece("G", "track-0", state="fallen_side", angle_deg=23.0)])
    (horse,) = board.horses
    assert horse.pose is Pose.FALLEN and horse.placement is Placement.AT_CELL
    assert horse.cell_id == "track-0" and horse.aspect > perception.load_rules().standing_max_aspect


def test_a_horse_on_its_back_is_fallen_by_area_not_by_shape():
    board = view_of([Piece("Y", "R-base-0", state="fallen_back")])
    (horse,) = board.horses
    assert horse.pose is Pose.FALLEN
    assert horse.area_ratio < perception.load_rules().standing_area_ratio[0]
    assert horse.aspect < perception.load_rules().standing_max_aspect, "on its back it is still square"


def test_a_horse_off_the_magnet_is_between_cells():
    rules = perception.load_rules()
    board = view_of([Piece("B", "track-24", offset_mm=(rules.cell_snap_radius_mm + 6.0, 0.0))])
    (horse,) = board.horses
    assert horse.placement is Placement.BETWEEN_CELLS and horse.cell_id is None
    assert horse.nearest_cell == "track-24" and horse.nearest_mm > rules.cell_snap_radius_mm
    assert board.occupancy() == {}, "a horse that missed the magnet occupies no cell"


def test_a_horse_just_inside_the_snap_radius_still_holds_its_cell():
    rules = perception.load_rules()
    board = view_of([Piece("B", "track-24", offset_mm=(rules.cell_snap_radius_mm - 5.0, 0.0))])
    (horse,) = board.horses
    assert horse.placement is Placement.AT_CELL and horse.cell_id == "track-24"


def test_a_missing_horse_is_simply_absent():
    assert view_of([]).occupancy() == {}
    assert view_of([]).horses == ()


def test_two_blobs_claiming_one_cell_leave_only_the_nearer_on_it():
    board = view_of([Piece("R", "track-12"), Piece("G", "track-12", offset_mm=(9.0, 9.0))])
    assert board.occupancy() == {"track-12": "R"}
    assert [h.color for h in board.loose] == ["G"]


def test_the_die_is_found_in_the_bowl_and_out_of_it():
    rules = perception.load_rules()
    cx, cy = rules.bowl.centre_mm
    inside = view_of([], (cx + 20.0, cy)).die
    assert inside.seen and inside.in_bowl
    assert inside.board_xy_mm == pytest.approx((cx + 20.0, cy), abs=2.0)

    outside = view_of([], (cx + rules.bowl.diameter_mm, cy)).die
    assert outside.seen and not outside.in_bowl

    assert view_of([]).die.seen is False, "no die anywhere near the bowl"


def test_a_die_that_bounced_onto_the_white_board_is_reported_as_not_seen():
    """A placeholder rule honestly stated: white-on-white is not detectable, and it is not guessed.

    Either way the verdict is ``die_out_of_bowl`` and a human is asked for the die back (6.5), so
    the failure is safe -- but the detector must not claim to have found a die it cannot see.
    """
    cx, cy = bowl_xy()
    assert view_of([], (cx, cy + 130.0)).die.seen is False


def test_the_board_itself_is_never_mistaken_for_the_die():
    """The board is white too; only a blob of about die size inside the bowl region counts."""
    board = view_of([Piece("R", "track-12"), Piece("G", "track-36")])
    assert board.die.seen is False


# --------------------------------------------------------------------------------------------
# verify(): a MOVE that happened, and every way it can fail
# --------------------------------------------------------------------------------------------


def verdict(command: Command, before: list[Piece], after: list[Piece], die=(None, None)):
    eye, _ = eye_on(before, die[0])
    before_view = view_of(before, die[0]).to_dict()
    after_view = view_of(after, die[1]).to_dict()
    return eye.verify(command, before_view, after_view)


def test_a_move_that_happened_is_a_success():
    out = verdict(move("track-12", "track-17"), [Piece("R", "track-12")], [Piece("R", "track-17")])
    assert out.success and out.failure_mode is None
    assert set(out.observed_state_delta["cells"]) == {"track-12", "track-17"}


def test_a_move_that_did_not_happen_is_a_failed_grasp():
    out = verdict(move("track-12", "track-17"), [Piece("R", "track-12")], [Piece("R", "track-12")])
    assert not out.success and out.failure_mode == FailureMode.GRASP_FAILED.value
    assert out.observed_state_delta == {}


def test_a_capture_is_a_move_onto_an_occupied_cell():
    out = verdict(
        move("track-12", "track-17"),
        [Piece("R", "track-12"), Piece("G", "track-17")],
        [Piece("R", "track-17")],
    )
    assert out.success, "the captured colour is gone from dst and ours is standing on it"


def test_a_horse_that_fell_at_the_destination_is_horse_fell():
    out = verdict(
        move("track-12", "track-17"),
        [Piece("R", "track-12")],
        [Piece("R", "track-17", state="fallen_side", angle_deg=15.0)],
    )
    assert out.failure_mode == FailureMode.HORSE_FELL.value


def test_a_horse_that_fell_at_the_source_is_horse_fell():
    out = verdict(
        move("track-12", "track-17"),
        [Piece("R", "track-12")],
        [Piece("R", "track-12", state="fallen_side", angle_deg=70.0)],
    )
    assert out.failure_mode == FailureMode.HORSE_FELL.value


def test_a_horse_left_between_cells_is_missed_cell():
    out = verdict(
        move("track-12", "track-17"),
        [Piece("R", "track-12")],
        [Piece("R", "track-17", offset_mm=(19.0, 0.0))],
    )
    assert out.failure_mode == FailureMode.MISSED_CELL.value


def test_a_horse_left_on_the_wrong_cell_is_missed_cell():
    out = verdict(move("track-12", "track-17"), [Piece("R", "track-12")], [Piece("R", "track-16")])
    assert out.failure_mode == FailureMode.MISSED_CELL.value


def test_another_colour_moved_instead_is_wrong_horse():
    out = verdict(
        move("track-12", "track-17"),
        [Piece("R", "track-12"), Piece("G", "track-16")],
        [Piece("R", "track-12"), Piece("G", "track-17")],
    )
    assert out.failure_mode == FailureMode.WRONG_HORSE.value


def test_the_same_colour_from_another_cell_is_wrong_horse():
    """Our colour reached the target -- but our horse never left, so it was the neighbour's turn."""
    out = verdict(
        move("track-12", "track-17"),
        [Piece("R", "track-12"), Piece("R", "track-16")],
        [Piece("R", "track-12"), Piece("R", "track-17")],
    )
    assert out.failure_mode == FailureMode.WRONG_HORSE.value


def test_a_move_whose_colour_is_unknown_is_never_reported_as_a_success():
    """No horse id and an empty source: perception cannot tell whose horse arrived, and says so."""
    out = verdict(move("track-12", "track-17", horse_id=None), [], [Piece("R", "track-17")])
    assert not out.success


def test_a_move_command_without_cells_is_rejected():
    eye, _ = eye_on([])
    empty = BoardView().to_dict()
    with pytest.raises(PerceptionError, match="needs both src and dst"):
        eye.verify(Command(Primitive.MOVE, None, None, "R0"), empty, empty)


# --------------------------------------------------------------------------------------------
# verify(): ROLL and RECOVER
# --------------------------------------------------------------------------------------------


def roll() -> Command:
    return Command(primitive=Primitive.ROLL, src=None, dst=None, horse_id=None)


def test_a_roll_that_landed_back_in_the_bowl_is_a_success():
    cx, cy = bowl_xy()
    out = verdict(roll(), [], [], die=((cx - 25.0, cy), (cx + 25.0, cy)))
    assert out.success and "die" in out.observed_state_delta


def test_a_die_outside_the_bowl_is_die_out_of_bowl():
    cx, cy = bowl_xy()
    diameter = perception.load_rules().bowl.diameter_mm
    out = verdict(roll(), [], [], die=((cx, cy), (cx + diameter, cy)))
    assert out.failure_mode == FailureMode.DIE_OUT_OF_BOWL.value


def test_a_die_that_never_moved_is_a_failed_die_grasp():
    cx, cy = bowl_xy()
    out = verdict(roll(), [], [], die=((cx + 10.0, cy), (cx + 10.0, cy)))
    assert out.failure_mode == FailureMode.DIE_GRASP_FAILED.value


def recover(cell_id: str | None, horse_id: str | None = None) -> Command:
    target = None if cell_id is None else cell(cell_id)
    return Command(primitive=Primitive.RECOVER, src=target, dst=target, horse_id=horse_id)


def test_a_recover_that_stood_the_horse_up_is_a_success():
    out = verdict(
        recover("track-12"),
        [Piece("R", "track-12", state="fallen_side", angle_deg=30.0)],
        [Piece("R", "track-12")],
    )
    assert out.success


def test_a_recover_that_left_it_lying_is_horse_fell():
    fallen = [Piece("R", "track-12", state="fallen_side", angle_deg=30.0)]
    out = verdict(recover("track-12"), fallen, [Piece("R", "track-12", state="fallen_back")])
    assert out.failure_mode == FailureMode.HORSE_FELL.value


def test_a_recover_that_left_it_off_the_magnet_is_missed_cell():
    out = verdict(
        recover("track-12"),
        [Piece("R", "track-12", state="fallen_side")],
        [Piece("R", "track-12", offset_mm=(16.0, 0.0))],
    )
    assert out.failure_mode == FailureMode.MISSED_CELL.value


def test_a_recover_that_changed_nothing_at_all_is_timeout_no_progress():
    out = verdict(recover("track-12"), [], [])
    assert out.failure_mode == FailureMode.TIMEOUT_NO_PROGRESS.value


def test_a_recover_of_the_die_addresses_the_bowl():
    cx, cy = bowl_xy()
    assert verdict(recover(None), [], [], die=(None, (cx, cy))).success
    away = (cx + perception.load_rules().bowl.diameter_mm, cy)
    assert verdict(recover(None), [], [], die=(None, away)).failure_mode == FailureMode.DIE_OUT_OF_BOWL.value


def _nudged(view: dict, millimetres: float) -> dict:
    """The same view with every horse displaced, as a jittery detector would report it."""
    moved = copy.deepcopy(view)
    for horse in list(moved["cells"].values()) + moved["loose"]:
        horse["board_xy_mm"] = [horse["board_xy_mm"][0] + millimetres, horse["board_xy_mm"][1]]
    return moved


def test_detector_jitter_is_not_mistaken_for_something_happening():
    """Sub-millimetre wobble between two frames must not turn a failed grasp into a missed cell."""
    eye, _ = eye_on([])
    before = view_of([Piece("R", "track-12"), Piece("B", "track-24", offset_mm=(19.0, 0.0))]).to_dict()
    out = eye.verify(move("track-12", "track-17"), before, _nudged(before, 0.4))
    assert out.failure_mode == FailureMode.GRASP_FAILED.value
    assert out.observed_state_delta, "the record still shows the difference; the verdict ignores it"


def test_a_loose_horse_that_really_moved_is_something_happening():
    eye, _ = eye_on([])
    before = view_of([Piece("R", "track-12"), Piece("B", "track-24", offset_mm=(19.0, 0.0))]).to_dict()
    out = eye.verify(move("track-12", "track-17"), before, _nudged(before, 12.0))
    assert out.failure_mode == FailureMode.WRONG_HORSE.value, "our horse stayed put, another piece moved"


# --------------------------------------------------------------------------------------------
# progress() for the watchdog
# --------------------------------------------------------------------------------------------


def test_progress_rises_as_the_move_gets_done():
    eye, _ = eye_on([])
    command = move("track-12", "track-17")
    start = view_of([Piece("R", "track-12")]).to_dict()
    lifted = view_of([]).to_dict()
    dropped = view_of([Piece("R", "track-17", offset_mm=(19.0, 0.0))]).to_dict()
    done = view_of([Piece("R", "track-17")]).to_dict()

    assert eye.progress(command, start, start) == 0.0
    assert eye.progress(command, start, lifted) == 0.5
    assert eye.progress(command, start, dropped) == 0.5
    assert eye.progress(command, start, done) == 1.0


def test_progress_on_a_roll_is_zero_until_the_die_has_moved():
    cx, cy = bowl_xy()
    eye, _ = eye_on([])
    start = view_of([], (cx - 25.0, cy)).to_dict()
    assert eye.progress(roll(), start, start) == 0.0
    assert eye.progress(roll(), start, view_of([], (cx + 25.0, cy)).to_dict()) == 1.0


def test_progress_and_verify_refuse_an_engine_board_state():
    """The engine's own view is not a camera view; mixing the two would judge the wrong thing."""
    eye, _ = eye_on([])
    engine_state = {"horses": {"R0": "track-12"}, "die": 3}
    with pytest.raises(PerceptionError, match="did not produce"):
        eye.progress(move("track-12", "track-17"), engine_state, engine_state)


def test_top_camera_perception_satisfies_the_protocol():
    eye, _ = eye_on([])
    assert isinstance(eye, Perception)
    assert "640x480" in repr(eye) and "placeholder" in repr(eye)


def test_a_view_round_trips_through_its_dict_form():
    before = view_of([Piece("R", "track-12"), Piece("G", "track-0", state="fallen_side"),
                      Piece("B", "track-24", offset_mm=(19.0, 0.0))], bowl_xy())
    back = BoardView.from_dict(before.to_dict())
    assert back.occupancy() == before.occupancy()
    assert back.die.to_dict() == before.die.to_dict()
    assert [h.color for h in back.loose] == [h.color for h in before.loose]
    assert back.cells["track-0"].pose is Pose.FALLEN


# --------------------------------------------------------------------------------------------
# timing (acceptance: under 30 ms per 640x480 frame)
# --------------------------------------------------------------------------------------------


def test_detection_runs_under_thirty_milliseconds_per_frame(capsys):
    rng = random.Random(7)
    pieces, _ = _random_board(rng, n=16)           # a full game: four colours, four horses each
    eye, image = eye_on(pieces, bowl_xy())
    eye.detect(image)                              # warm up: caches the die search region
    samples = []
    for _ in range(30):
        start = time.perf_counter()
        eye.detect(image)
        samples.append((time.perf_counter() - start) * 1e3)
    mean, worst = float(np.mean(samples)), float(np.max(samples))
    with capsys.disabled():
        print(f"\nTopCameraPerception.detect on {FRAME[0]}x{FRAME[1]}, 16 horses + die, 30 frames: "
              f"mean {mean:.2f} ms, max {worst:.2f} ms, p50 {float(np.median(samples)):.2f} ms")
    assert mean < 30.0, f"mean {mean:.2f} ms per frame"
