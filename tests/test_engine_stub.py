"""The engine contract and the stub engine (T-007).

Everything here runs on ``config/board.yaml`` and the stub; no hardware, no mocks needed.
"""

from __future__ import annotations

import dataclasses
from collections import Counter

import pytest
import yaml

from engine.cells import load_cells, load_layout
from engine.interface import Cell, Command, EngineClient, Outcome, Primitive
from engine.stub import ENTER_ROLLS, SCRIPTS_DIR, StubEngine, load_script

OK = Outcome(success=True, observed_state_delta={}, failure_mode=None)


def fail(mode: str = "grasp_failed") -> Outcome:
    return Outcome(success=False, observed_state_delta={}, failure_mode=mode)


def drive(engine: StubEngine, n: int, outcome: Outcome = OK) -> list[Command]:
    """Take up to ``n`` commands, reporting ``outcome`` for each."""
    out: list[Command] = []
    for _ in range(n):
        cmd = engine.next_command()
        if cmd is None:
            break
        out.append(cmd)
        engine.report(outcome)
    return out


def first_move(engine: StubEngine) -> Command:
    """Hand back the first MOVE of a random game, left outstanding (unreported).

    A turn whose roll admits no legal move is just a ROLL, so a test that needs a MOVE has to walk
    forward to one rather than assume the first turn produces it.
    """
    for _ in range(200):
        cmd = engine.next_command()
        assert cmd is not None
        if cmd.primitive is Primitive.MOVE:
            return cmd
        engine.report(OK)
    raise AssertionError("no MOVE in 200 commands")


def key(cmd: Command) -> tuple:
    """A comparable identity for a command (Cell is frozen, so equality is structural anyway)."""
    return (
        cmd.primitive,
        None if cmd.src is None else cmd.src.id,
        None if cmd.dst is None else cmd.dst.id,
        cmd.horse_id,
    )


# -- interface (CLAUDE.md 5.5) ---------------------------------------------------------------------


def test_interface_matches_claude_md_5_5():
    assert [p.name for p in Primitive] == ["MOVE", "ROLL", "RECOVER"]
    assert [p.value for p in Primitive] == ["move", "roll", "recover"]
    assert [f.name for f in dataclasses.fields(Cell)] == ["id", "board_xy_mm", "top_px"]
    assert [f.name for f in dataclasses.fields(Command)] == ["primitive", "src", "dst", "horse_id"]
    assert [f.name for f in dataclasses.fields(Outcome)] == ["success", "observed_state_delta", "failure_mode"]
    assert all(getattr(EngineClient, m).__isabstractmethod__ for m in ("next_command", "report", "board_state"))


def test_interface_dataclasses_are_frozen():
    cell = Cell("track-0", (0.0, 0.0), None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cell.id = "track-1"


def test_engine_client_cannot_be_instantiated():
    with pytest.raises(TypeError):
        EngineClient()


def test_stub_is_an_engine_client():
    assert isinstance(StubEngine(seed=0), EngineClient)


# -- engine/cells.py -------------------------------------------------------------------------------


def test_load_cells_covers_the_whole_board_config():
    cells = load_cells()
    layout = load_layout()
    expected = layout.track_length + len(layout.colors) * (layout.home_length + layout.base_size)
    assert len(cells) == expected == 88
    assert all(cell_id == cell.id for cell_id, cell in cells.items())
    assert all(cell.top_px is None for cell in cells.values())
    assert cells["track-12"].board_xy_mm == (40.0, 280.0)


def test_load_cells_takes_top_px_from_a_calibration_and_rejects_unknown_ids():
    cells = load_cells(top_px={"track-0": (11.0, 22.0)})
    assert cells["track-0"].top_px == (11.0, 22.0)
    assert cells["track-1"].top_px is None
    with pytest.raises(Exception, match="does not define"):
        load_cells(top_px={"nowhere-9": (0.0, 0.0)})


def test_layout_start_indices_agree_with_the_home_entries():
    layout = load_layout()
    for color in layout.colors:
        start = layout.start_index(color)
        # progress 47 (one full lap minus the start cell) is the home entry; 48 enters the lane.
        assert f"track-{(start + layout.track_length - 1) % layout.track_length}" == layout.home_entries[color]


# -- determinism (acceptance 1) --------------------------------------------------------------------


def test_same_seed_gives_an_identical_sequence_of_200_commands():
    a = drive(StubEngine(seed=7), 200)
    b = drive(StubEngine(seed=7), 200)
    assert len(a) == 200
    assert [key(c) for c in a] == [key(c) for c in b]
    assert a == b


def test_different_seeds_diverge():
    a = [key(c) for c in drive(StubEngine(seed=7), 200)]
    c = [key(c) for c in drive(StubEngine(seed=8), 200)]
    assert a != c


def test_a_finished_game_is_dealt_again_so_a_collection_run_never_stalls():
    """A won board can generate no moves, so random mode deals a new one and keeps the seed's stream."""
    engine = StubEngine(seed=7)
    cmds = drive(engine, 600)
    assert len(cmds) == 600, "the random stream stopped short"
    state = engine.board_state()
    assert state["game"] >= 1, "600 commands should have finished at least one game"
    assert state["winner"] is None, "a new board is dealt as soon as a colour is home"
    # Rolls and moves stay in the same order of magnitude; a board that silently finished and kept
    # rolling would be nearly all ROLL (the failure this test exists to catch).
    counts = Counter(c.primitive for c in cmds)
    assert counts[Primitive.MOVE] > 0.3 * len(cmds)


def test_a_game_uses_all_three_primitives_and_enters_from_base():
    cmds = drive(StubEngine(seed=3), 400)
    counts = Counter(c.primitive for c in cmds)
    assert counts[Primitive.ROLL] > 0 and counts[Primitive.MOVE] > 0
    base_ids = {f"R-base-{i}" for i in range(4)}
    assert any(c.primitive is Primitive.MOVE and c.src is not None and c.src.id in base_ids for c in cmds)


# -- recovery and retries (acceptance 2) -----------------------------------------------------------


def test_failure_yields_recover_at_the_failing_cell_then_the_original_command():
    engine = StubEngine(seed=5)
    original = first_move(engine)
    engine.report(fail("horse_fell"))

    recover = engine.next_command()
    assert recover.primitive is Primitive.RECOVER
    assert recover.src is recover.dst is original.dst      # the cell where the failure happened
    assert recover.horse_id == original.horse_id
    engine.report(OK)

    assert key(engine.next_command()) == key(original)


def test_two_failed_reissues_give_the_turn_up_and_the_game_moves_on():
    """Acceptance 2 in full: after the second failure, RECOVER, the original once more, then a new turn."""
    engine = StubEngine(seed=5)
    original = first_move(engine)
    turn = engine.board_state()["turn"]

    for _ in range(2):                    # failure 1 and failure 2, each answered with recover + re-issue
        engine.report(fail("missed_cell"))
        recover = engine.next_command()   # 1st next_command after the failure
        assert recover.primitive is Primitive.RECOVER
        engine.report(OK)
        assert key(engine.next_command()) == key(original)   # 2nd: the original again
    engine.report(fail("missed_cell"))    # failure 3: two re-issues have now failed

    nxt = engine.next_command()           # 3rd: a different turn
    assert key(nxt) != key(original)
    assert nxt.primitive is Primitive.ROLL             # a new turn starts with its roll
    assert engine.board_state()["turn"] == turn + 1
    assert len(engine.failures) == 1
    failed = engine.failures[0]
    assert failed["primitive"] == "move" and failed["dst"] == original.dst.id
    assert failed["attempts"] == 3 and failed["failure_modes"] == ["missed_cell"] * 3


def test_a_failed_recover_counts_against_the_command_it_protects():
    engine = StubEngine(seed=5)
    original = first_move(engine)
    engine.report(fail())                              # failure 1 -> recover
    assert engine.next_command().primitive is Primitive.RECOVER
    engine.report(fail("horse_fell"))                  # the recovery itself failed
    # A failed RECOVER is never recovered in its own right; it is charged to the command it protects
    # (so the budget still terminates) and the cell is offered a fresh recovery.
    assert engine.next_command().primitive is Primitive.RECOVER
    engine.report(OK)
    assert key(engine.next_command()) == key(original)
    engine.report(fail())                              # failure 3 -> the budget is spent
    assert engine.next_command().primitive is Primitive.ROLL
    assert len(engine.failures) == 1
    assert engine.failures[0]["failure_modes"] == ["grasp_failed", "horse_fell", "grasp_failed"]


def test_a_successful_command_leaves_no_failure_and_no_recover():
    cmds = drive(StubEngine(seed=11), 120)
    assert not any(c.primitive is Primitive.RECOVER for c in cmds)


# -- board consistency (acceptance 3) --------------------------------------------------------------


def test_every_command_addresses_a_cell_that_exists_in_board_yaml():
    cells = load_cells()
    engine = StubEngine(seed=2)
    seen = 0
    for i in range(300):
        cmd = engine.next_command()
        assert cmd is not None
        for end in (cmd.src, cmd.dst):
            if end is not None:
                assert end.id in cells, f"command {i} addresses unknown cell {end.id!r}"
                assert end == cells[end.id]
                seen += 1
        engine.report(OK if i % 7 else fail("grasp_failed"))
    assert seen > 0


def test_every_move_addresses_two_cells_and_a_horse():
    for cmd in drive(StubEngine(seed=4), 200):
        if cmd.primitive is Primitive.MOVE:
            assert cmd.src is not None and cmd.dst is not None and cmd.horse_id is not None
            assert cmd.src.id != cmd.dst.id
        elif cmd.primitive is Primitive.ROLL:
            assert cmd.src is None and cmd.dst is None and cmd.horse_id is None


def test_roll_addresses_the_bowl_when_one_is_supplied():
    bowl = Cell("bowl", (0.0, -400.0), None)
    cmd = StubEngine(seed=1, bowl_cell=bowl).next_command()
    assert cmd.primitive is Primitive.ROLL and cmd.src is bowl and cmd.dst is bowl


def test_board_state_stays_consistent_across_a_game():
    cells = load_cells()
    engine = StubEngine(seed=6)
    for _ in range(400):
        if engine.next_command() is None:
            break
        engine.report(OK)
        state = engine.board_state()
        assert set(state["horses"]) == {f"{c}{i}" for c in "RGYB" for i in range(4)}
        assert all(cell_id in cells for cell_id in state["horses"].values())
        on_board = [h for h, p in state["progress"].items() if p is not None]
        occupied = [state["horses"][h] for h in on_board]
        assert len(occupied) == len(set(occupied)), f"two horses on one cell: {occupied}"


def test_a_move_command_matches_the_board_state_before_and_after_it():
    engine = StubEngine(seed=9)
    for _ in range(300):
        cmd = engine.next_command()
        if cmd is None:
            break
        if cmd.primitive is Primitive.MOVE:
            assert engine.board_state()["horses"][cmd.horse_id] == cmd.src.id
            engine.report(OK)
            assert engine.board_state()["horses"][cmd.horse_id] == cmd.dst.id
        else:
            engine.report(OK)


def test_enter_from_base_only_happens_on_an_entering_roll():
    engine = StubEngine(seed=13)
    base_ids = {f"R-base-{i}" for i in range(4)}
    entries = 0
    for _ in range(400):
        cmd = engine.next_command()
        if cmd is None:
            break
        engine.report(OK)
        if cmd.primitive is Primitive.MOVE and cmd.src is not None and cmd.src.id in base_ids:
            if cmd.horse_id.startswith("R"):           # our own horse leaving base, not a capture
                assert engine.board_state()["die"] in ENTER_ROLLS
                assert cmd.dst.id == "track-12"        # R's start cell
                entries += 1
    assert entries > 0


def test_a_capture_is_emitted_as_two_moves_clearing_the_captured_horse_first():
    """Over several seeds, every capture puts the opponent's horse back in its own base first."""
    captures = 0
    for seed in range(12):
        engine = StubEngine(seed=seed)
        prev: Command | None = None
        for _ in range(400):
            cmd = engine.next_command()
            if cmd is None:
                break
            if cmd.primitive is Primitive.MOVE and cmd.horse_id is not None and not cmd.horse_id.startswith("R"):
                captures += 1
                color = cmd.horse_id[0]
                assert cmd.dst.id == f"{color}-base-{cmd.horse_id[1:]}"
                nxt_state_src = cmd.src.id
                engine.report(OK)
                follow = engine.next_command()         # ours moves onto the cell just cleared
                assert follow.primitive is Primitive.MOVE and follow.dst.id == nxt_state_src
                assert follow.horse_id.startswith("R")
                engine.report(OK)
                prev = follow
                continue
            prev = cmd
            engine.report(OK)
        assert prev is None or prev.primitive in (Primitive.MOVE, Primitive.ROLL)
    assert captures > 0, "no capture in 12 seeds x 400 commands; the generator is not exercising them"


# -- the eval script (acceptance 4) ----------------------------------------------------------------


def test_eval_20_moves_yields_exactly_twenty_moves_over_ten_distinct_pairs():
    cells = load_cells()
    engine = StubEngine(seed=0, script=load_script("eval_20_moves"))
    cmds = drive(engine, 100)
    assert len(cmds) == 20
    assert all(c.primitive is Primitive.MOVE for c in cmds)
    pairs = {(c.src.id, c.dst.id) for c in cmds}
    assert len(pairs) >= 10
    assert all(src in cells and dst in cells for src, dst in pairs)
    assert engine.next_command() is None


def test_every_script_in_the_scripts_dir_loads():
    paths = sorted(SCRIPTS_DIR.glob("*.yaml"))
    assert paths, "engine/scripts/ has no scripts"
    for path in paths:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert doc["name"] == path.stem
        assert len(load_script(path)) == len(doc["commands"])


def test_a_script_may_not_address_a_cell_that_does_not_exist(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("commands: [{primitive: move, src: track-0, dst: nowhere-1}]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a cell in config/board.yaml"):
        load_script(bad)


def test_script_mode_retries_the_scripted_command_and_then_moves_on():
    script = load_script("eval_20_moves")
    engine = StubEngine(seed=0, script=script)
    first = engine.next_command()
    for _ in range(2):
        engine.report(fail("grasp_failed"))
        assert engine.next_command().primitive is Primitive.RECOVER
        engine.report(OK)
        assert key(engine.next_command()) == key(first)
    engine.report(fail("grasp_failed"))
    assert key(engine.next_command()) == key(script[1])
    assert len(engine.failures) == 1


def test_script_mode_tracks_the_horses_it_places():
    engine = StubEngine(seed=0, script=load_script("eval_20_moves"))
    cmd = engine.next_command()
    engine.report(OK)
    assert engine.board_state()["horses"][cmd.horse_id] == cmd.dst.id


# -- contract misuse -------------------------------------------------------------------------------


def test_next_command_refuses_to_run_ahead_of_a_report():
    engine = StubEngine(seed=1)
    engine.next_command()
    with pytest.raises(RuntimeError, match="report"):
        engine.next_command()


def test_report_without_an_outstanding_command_is_an_error():
    with pytest.raises(RuntimeError, match="no command outstanding"):
        StubEngine(seed=1).report(OK)


def test_an_unknown_robot_color_is_rejected():
    with pytest.raises(ValueError, match="robot_color"):
        StubEngine(seed=1, robot_color="P")
