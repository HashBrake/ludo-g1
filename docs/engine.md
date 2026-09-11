# The game engine: contract and stub

The game engine decides *what* move to make; this project executes it with a learned policy. The
boundary between the two is `engine/interface.py` (CLAUDE.md 5.5) and it is deliberately tiny: three
dataclasses, one enum, one abstract class. Everything else in `engine/` is scaffolding that stands in
for an engine that does not exist yet and will be deleted when it does.

```python
from engine.cells import load_cells
from engine.interface import Outcome, Primitive
from engine.stub import StubEngine, load_script

engine = StubEngine(seed=7)                                   # random games, pinned to the seed
engine = StubEngine(seed=0, script=load_script("eval_20_moves"))  # a fixed eval sequence

cmd = engine.next_command()          # None: nothing left to play
...                                  # execute cmd with the policy
engine.report(Outcome(success=True, observed_state_delta={}, failure_mode=None))
engine.board_state()                 # {"horses": {"R0": "track-14", ...}, "die": 3, ...}
```

## The contract

| type | what it is |
|---|---|
| `Primitive` | `MOVE` / `ROLL` / `RECOVER`, values `"move"`, `"roll"`, `"recover"`. These are the `task_id` one-hot of CLAUDE.md 5.3 and the three things the policy is trained to do. |
| `Cell` | `id`, `board_xy_mm`, `top_px`. One addressable board position. |
| `Command` | `primitive`, `src`, `dst`, `horse_id`. `src`/`dst` are `None` for a primitive that addresses no board cell. |
| `Outcome` | `success`, `observed_state_delta`, `failure_mode`. One per command handed out. |
| `EngineClient` | `next_command()`, `report(outcome)`, `board_state()`. Abstract (5.1). |

The three dataclasses are frozen: a `Command` travels from the engine through `runtime/controller.py`
into the goal heatmap, and nothing on that path may edit it behind the engine's back. CLAUDE.md 5.5
spells the optional fields `Optional[X]`; this file spells them `X | None`, the identical type and the
only spelling this project's ruff rules accept.

`runtime/controller.py` runs exactly one cycle -- `next_command`, execute, `report` -- and never
invents recovery of its own. Recovery is the *engine's* job (5.5), which is why the stub below owns
the retry state machine. That keeps R2 intact: retries and re-issues are orchestration, not motion.

## Cells

`engine/cells.py` turns the `layout.cells` table in `config/board.yaml` into `Cell` objects:

```python
cells = load_cells()                 # {"track-0": Cell(...), ..., "B-home-5": Cell(...)}, 88 of them
len(cells)                           # 48 track + 4 x (6 home + 4 base)
cells["track-12"].board_xy_mm        # (40.0, 280.0)
cells["track-12"].top_px             # None: no calibration was supplied
```

`top_px` is **not** in `config/board.yaml` and cannot be -- it changes whenever the Brio moves. It
comes from `board/calibration.py` (T-008), and `load_cells(top_px=...)` takes it as a mapping from
cell id to pixel; ids it does not mention keep `top_px = None`, and an id that is not in the board
config is an error, so a calibration of a different board cannot pass silently. Everything in Phase 0
runs with `top_px = None`.

`load_layout()` returns the same file's topology: the colours, each colour's start and home-entry
cell, and the track/home/base sizes. Both are placeholders in the sense of `docs/config.md`
(`layout_status: UNMEASURED`) and the engine team owns the real thing.

## The stub

`engine/stub.py` is a move *generator*, not a rules engine. Its job is to produce deterministic,
board-consistent command streams so that the recorder, the goal heatmaps and `eval/` have something
concrete to run against. About 300 lines is the whole budget it gets.

### Random mode

Four colours (`R`, `G`, `Y`, `B`), four horses each (`R0`..`R3`), all starting in base. The robot
plays one colour (`robot_color`, default `R`). **Only the robot's turns produce commands**: the other
three colours are simulated internally, so `board_state()` keeps moving and the board keeps looking
like a real game without the robot ever being asked to touch someone else's piece.

A robot turn is a `ROLL`, followed by one `MOVE` if the roll admits a legal move.

Rules, all as placeholder as the cell table they run on:

- **Entering from base** needs a **1 or a 6** (`stub.ENTER_ROLLS`). The co ca ngua variant admits
  both, where International Ludo admits only the 6; this is the one rules choice T-007 had to make
  and it is made here, in one constant. The horse lands on its colour's start cell (`layout.starts`).
- **Progress** is counted from a colour's own start cell: `0..47` is the shared track (cell
  `track-((start + p) % 48)`), `48..53` is that colour's private home lane (`<C>-home-(p - 48)`). A
  roll that would carry a horse past `home-5` is illegal, so a horse can sit short of the end.
- **Capture**: landing on an opponent sends that horse back to its own base cell. The robot has to do
  that with its own arm, so a capture is emitted as **two** MOVE commands -- the captured horse out to
  its base first, then ours onto the cell just cleared. Both are ordinary MOVEs, as 5.5 requires
  ("covers enter-from-base and capture").
- A horse with no legal move is skipped; a turn with no legal move at all is just the `ROLL`.
- A colour with every horse in its home lane has **won**. `winner()` says who. A won board can
  generate no further moves (a horse in the lane can never leave it), so random mode deals a fresh
  board and plays on -- otherwise the generator would roll the die forever and a collection session
  wanting a few hundred episodes would stall. The RNG is not reset, so the stream stays pinned to the
  seed across game boundaries, and `board_state()["game"]` counts the deals.

Every random choice comes from one `random.Random(seed)`; nothing reads the global RNG. The same seed
therefore reproduces a command stream exactly, for as long as you care to drive it.

The die value is decided *by the stub*. A real engine reads the physical die through
`board/perception.py`; this one does not pretend to.

### The bowl

`ROLL` commands carry `src = dst = None` by default. `config/board.yaml` describes no bowl cell and
`die.bowl_centre_mm` is UNMEASURED, so there is no honest `Cell` to point at -- and inventing one
would put a goal heatmap somewhere real on the board. A caller that has measured the bowl passes
`StubEngine(..., bowl_cell=Cell("bowl", (x, y), None))` and both `ROLL` and any `RECOVER` after a
failed roll then address it. When the board config grows a bowl cell, that is the one line to change.

### Script mode

```python
engine = StubEngine(seed=0, script=load_script("eval_20_moves"))
```

The stub hands out exactly the commands in the list, in order, and then returns `None`. Scripts live
in `engine/scripts/*.yaml`:

```yaml
name: eval_20_moves
description: ...
commands:
  - {primitive: move, src: R-base-0, dst: track-12, horse_id: R0}
```

`src`/`dst` are cell **ids** and are resolved against `config/board.yaml` at load time, so a script
can never address a cell that does not exist. Script mode does **not** check the moves against the
rules: an eval sequence is a fixed set of pick-and-place problems, not a game. `board_state()` in
script mode just records where each `horse_id` was last placed.

`engine/scripts/eval_20_moves.yaml` is the T-007 deliverable: 20 MOVE commands over 10 distinct
`(src, dst)` pairs, each pair twice so a per-pair success rate has two samples. The pairs cover an
enter-from-base, short and long hops on each arm of the cross, the 80 mm step at an arm tip, a home
entry and a move inside the home lane.

### Retries and recovery

The engine, not the controller, decides what happens after a failure (5.5). On
`report(Outcome(success=False, failure_mode=...))`:

1. a `RECOVER` is issued for the cell where the failure happened -- `dst` of the failed command, with
   `src = dst = ` that cell and the same `horse_id`;
2. once the recovery is reported, the **original command is re-issued unchanged**;
3. at most **two** re-issues. A third failure gives the turn up: it is appended to `engine.failures`
   (with the turn, the command, the attempt count and every `failure_mode` string seen) and the rest
   of that turn is dropped, because the rest of a turn depends on the step that failed.

So, counting from the second failure: the next command is the `RECOVER`, the one after it is the
original for the last time, and the third is a **different turn**.

A `RECOVER` that itself fails is never recovered in its own right -- that would not terminate. It is
charged to the command it was protecting, which then offers the cell a fresh recovery or gives up.

`engine.failures` is what `eval/` counts failure modes out of, and CLAUDE.md 6.5 lists the labelled
strings the rest of the system uses.

### Contract misuse

`next_command()` raises `RuntimeError` if the previous command has not been reported, and `report()`
raises if nothing is outstanding. There is exactly one report per command; an engine that silently
tolerated a missing one would let the controller lose an execution and still produce a plausible
dataset.

## What the stub is not

- Not a rules engine. No blocking, no doubling, no extra turn on a six, no exact-count finish, no
  choice of the *best* move -- it picks a legal-looking one at random.
- Not a die reader, not a perception system, not an opponent worth playing.
- Not a place to grow features. When the engine team delivers, `engine/stub.py` is deleted and
  `engine/interface.py` stays exactly as it is. That is the whole point of the split.

## The engine over a socket (T-039)

The real engine is another team's process. `engine/net.py` is the client, `engine/schema.py` is the
wire format, and `engine/serve_stub.py` serves `StubEngine` on it so the remote path runs today:

```bash
.venv/bin/python -m engine.serve_stub --bind 127.0.0.1:5555 --seed 0
.venv/bin/python -m runtime.controller --backend mock --engine tcp://127.0.0.1:5555 --seconds 20
```

```python
from engine.net import NetEngineClient
with NetEngineClient("tcp://127.0.0.1:5555") as engine:   # or NetEngineClient() for the configured url
    cmd = engine.next_command()
```

`NetEngineClient` **is** an `EngineClient`: same three methods, same frozen dataclasses, same
`RuntimeError` on contract misuse. `runtime/controller.py` cannot tell it from the in-process stub,
and `build(..., engine=<url>)` is the only line that differs.

**This is a plain TCP protocol, not ZeroMQ.** The task allowed ZeroMQ REQ/REP only if `pyzmq` were
already installed (`.venv/bin/python -c "import zmq"` fails) and T-039 was not to add a dependency, so
the transport is JSON lines over TCP from the standard library and the URL scheme is `tcp://`. A
`zmq://` URL is accepted as a synonym and speaks the same protocol; nothing in this section depends on
either library, which is the point -- the engine team implements *this document*, in whatever language.

### The frame

One JSON object per line, UTF-8, `\n` terminated. One request, one response, in order, on one
connection (REQ/REP semantics). `json.dumps` escapes any newline inside a string, so a line break is
always a frame boundary. Either end refuses a frame over 8 MiB (`schema.MAX_FRAME_BYTES`) rather than
buffering without bound.

Every message carries `"v"`, the schema version, currently **1**. A peer that receives another version
refuses the frame (`ProtocolError`) instead of guessing at fields it does not know; bump the version
only for a change a v1 peer could not read correctly.

### The three ops

`next_command` -- the next command, or `null` when there is nothing left to play:

```json
--> {"v":1,"op":"next_command"}
<-- {"v":1,"ok":true,"result":{"primitive":"move",
                               "src":{"id":"R-base-0","board_xy_mm":[-220.0,-220.0],"top_px":null},
                               "dst":{"id":"track-12","board_xy_mm":[40.0,280.0],"top_px":null},
                               "horse_id":"R0"}}
<-- {"v":1,"ok":true,"result":null}
```

`report` -- exactly one per command handed out:

```json
--> {"v":1,"op":"report","outcome":{"success":false,
                                    "observed_state_delta":{"horses":{"R0":["R-base-0","track-12"]}},
                                    "failure_mode":"grasp_failed"}}
<-- {"v":1,"ok":true,"result":null}
```

`board_state` -- the engine's view of the board, any JSON object:

```json
--> {"v":1,"op":"board_state"}
<-- {"v":1,"ok":true,"result":{"robot_color":"R","game":0,"turn":3,"winner":null,"die":4,
                               "horses":{"R0":"track-12"},"progress":{"R0":0},"failures":[]}}
```

| type | on the wire |
|---|---|
| `Primitive` | its value string: `"move"`, `"roll"`, `"recover"` |
| `Cell` | `{"id": str, "board_xy_mm": [x, y], "top_px": [u, v] \| null}`; a `null` cell is a `None` cell |
| `Command` | `{"primitive", "src", "dst", "horse_id"}`; `null` means "nothing left to play" |
| `Outcome` | `{"success": bool, "observed_state_delta": object, "failure_mode": str \| null}` |

### Errors: said no, versus not there

An exception on the engine side is a **response**, not a dropped connection:

```json
<-- {"v":1,"ok":false,"error":{"type":"RuntimeError","message":"report() the outstanding roll first"}}
```

The client raises `schema.RemoteEngineError` (a `RuntimeError`, carrying `remote_type`), the
connection stays up and the far side's state machine is untouched -- so the contract misuse that
raises `RuntimeError` in process raises `RuntimeError` over a socket too.

A **transport** failure is different: no connection, no response inside the budget, or a peer that
went away raises `net.EngineUnavailable` (also a `RuntimeError`, but a class of its own). The two are
never confused, because "the engine said no" and "the engine is not there" need different answers.

### Timeouts and reconnection

`config/training.yaml`:

```yaml
engine:
  url: tcp://127.0.0.1:5555
  request_timeout_s: 2.0
  connect_timeout_s: 2.0
```

Every call is bounded end to end by `request_timeout_s`: the client never holds the controller's tick
longer than that, whatever the far side does (including accepting the connection and then saying
nothing, the case a naive client hangs on forever). On a timeout or a dropped peer the socket is
closed and `EngineUnavailable` is raised; the **next** call reconnects, so an engine that restarts is
picked up without restarting the controller. The connection is lazy -- constructing a client touches
no socket -- so a controller can be built before the engine process is up.

Budget note: the controller makes **one** `next_command`, **one** `report` and one `board_state` per
command, plus one `board_state` per watchdog sample (`runtime.watchdog_interval_s`, 1 s). A 20 s
primitive is therefore ~24 round trips, not 200: the engine is not in the 10 Hz path.

### The served stub

`engine/serve_stub.py` wraps any `EngineClient` (`StubServer(engine, "127.0.0.1:5555")`) and answers
one connection at a time, requests in order. One connection is not a limitation but the contract:
there is one outstanding command at any moment, so a second concurrent client would corrupt the state
machine rather than share it. A client that disconnects frees the server for the next one, which is
how the client's reconnect works. `--bind 127.0.0.1:0` takes a free port and prints `listening
tcp://host:port` on stdout, which is what the tests read.

The served stub is the same object as the in-process one, so the same seed produces the same stream:
`tests/test_engine_net.py` drives 50 commands through both and compares them command by command.
