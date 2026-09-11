"""The wire schema of the engine contract: one versioned JSON encoding, in one place (T-039).

``engine/interface.py`` is the contract as Python objects; this is the same contract as bytes, so the
real engine can live in another process, another language, or on another machine. Both ends of the
socket -- :class:`engine.net.NetEngineClient` and ``engine/serve_stub.py`` -- encode and decode here
and nowhere else, which is what keeps the engine team with one document to target (``docs/engine.md``)
instead of two implementations to reconcile.

The frame is one JSON object per line, UTF-8, ``\\n`` terminated (``json.dumps`` escapes any newline
inside a string, so a line break is unambiguously a frame boundary). One request, one response, in
order, on one connection::

    -> {"v": 1, "op": "next_command"}
    <- {"v": 1, "ok": true, "result": {"primitive": "move", "src": {...}, "dst": {...}, "horse_id": "R0"}}

Every message carries ``v``; this module refuses any other version rather than guessing at a field it
does not know (:class:`ProtocolError`). An engine-side exception is a *response*, not a dropped
connection: ``{"ok": false, "error": {...}}``, so that the contract misuse the stub raises
``RuntimeError`` for in process raises ``RuntimeError`` over a socket too.
"""

from __future__ import annotations

import json
from typing import Any

from engine.interface import Cell, Command, EngineClient, Outcome, Primitive

__all__ = [
    "MAX_FRAME_BYTES",
    "OPS",
    "VERSION",
    "ProtocolError",
    "RemoteEngineError",
    "decode_cell",
    "decode_command",
    "decode_outcome",
    "dumps",
    "encode_cell",
    "encode_command",
    "encode_outcome",
    "error_response",
    "handle",
    "loads",
    "ok_response",
    "request",
    "result_of",
]

#: Schema version. Bumped only for a change a v1 peer could not read correctly.
VERSION = 1

#: The three methods of :class:`~engine.interface.EngineClient`, and the only ops that exist.
OPS: tuple[str, ...] = ("next_command", "report", "board_state")

#: A frame longer than this is a peer that is not speaking this protocol (or a board_state that has
#: gone wrong); both ends give up rather than buffering without bound.
MAX_FRAME_BYTES = 8 * 1024 * 1024


class ProtocolError(ValueError):
    """A frame is not valid JSON, is not this schema, or is of another version."""


class RemoteEngineError(RuntimeError):
    """The far side raised. ``remote_type`` is its exception class name, verbatim.

    A ``RuntimeError``, because that is what the in-process engine raises on contract misuse
    (``next_command()`` twice without a ``report()``): the same mistake fails the same way whether the
    engine is an object or a socket.
    """

    def __init__(self, remote_type: str, remote_message: str) -> None:
        super().__init__(f"engine raised {remote_type}: {remote_message}")
        self.remote_type = remote_type
        self.remote_message = remote_message


# -- values ----------------------------------------------------------------------------------------
def encode_cell(cell: Cell | None) -> dict[str, Any] | None:
    """A :class:`~engine.interface.Cell` as ``{"id", "board_xy_mm", "top_px"}``; ``None`` stays null."""
    if cell is None:
        return None
    return {
        "id": cell.id,
        "board_xy_mm": [float(v) for v in cell.board_xy_mm],
        "top_px": None if cell.top_px is None else [float(v) for v in cell.top_px],
    }


def decode_cell(obj: Any) -> Cell | None:
    """Inverse of :func:`encode_cell`. Tuples, not lists: the dataclass is frozen and compared."""
    if obj is None:
        return None
    if not isinstance(obj, dict):
        raise ProtocolError(f"cell must be an object or null, got {type(obj).__name__}")
    for key in ("id", "board_xy_mm"):
        if key not in obj:
            raise ProtocolError(f"cell is missing {key!r}")
    return Cell(id=str(obj["id"]),
                board_xy_mm=_pair(obj["board_xy_mm"], "board_xy_mm"),
                top_px=None if obj.get("top_px") is None else _pair(obj["top_px"], "top_px"))


def _pair(value: Any, what: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2 or not all(isinstance(v, (int, float)) for v in value):
        raise ProtocolError(f"cell {what} must be two numbers, got {value!r}")
    return (float(value[0]), float(value[1]))


def encode_command(command: Command | None) -> dict[str, Any] | None:
    """A :class:`~engine.interface.Command`; ``None`` (nothing left to play) stays null."""
    if command is None:
        return None
    return {
        "primitive": command.primitive.value,
        "src": encode_cell(command.src),
        "dst": encode_cell(command.dst),
        "horse_id": command.horse_id,
    }


def decode_command(obj: Any) -> Command | None:
    """Inverse of :func:`encode_command`."""
    if obj is None:
        return None
    if not isinstance(obj, dict):
        raise ProtocolError(f"command must be an object or null, got {type(obj).__name__}")
    if "primitive" not in obj:
        raise ProtocolError("command is missing 'primitive'")
    try:
        primitive = Primitive(obj["primitive"])
    except ValueError as exc:
        raise ProtocolError(f"command: {exc}") from exc
    horse = obj.get("horse_id")
    return Command(primitive=primitive, src=decode_cell(obj.get("src")), dst=decode_cell(obj.get("dst")),
                   horse_id=None if horse is None else str(horse))


def encode_outcome(outcome: Outcome) -> dict[str, Any]:
    """An :class:`~engine.interface.Outcome`. ``observed_state_delta`` travels as a JSON object."""
    delta = outcome.observed_state_delta
    if not isinstance(delta, dict):
        raise ProtocolError(f"observed_state_delta must be a mapping, got {type(delta).__name__}")
    return {
        "success": bool(outcome.success),
        "observed_state_delta": delta,
        "failure_mode": outcome.failure_mode,
    }


def decode_outcome(obj: Any) -> Outcome:
    """Inverse of :func:`encode_outcome`."""
    if not isinstance(obj, dict):
        raise ProtocolError(f"outcome must be an object, got {type(obj).__name__}")
    if "success" not in obj:
        raise ProtocolError("outcome is missing 'success'")
    delta = obj.get("observed_state_delta", {})
    if not isinstance(delta, dict):
        raise ProtocolError(f"outcome observed_state_delta must be an object, got {type(delta).__name__}")
    mode = obj.get("failure_mode")
    return Outcome(success=bool(obj["success"]), observed_state_delta=delta,
                   failure_mode=None if mode is None else str(mode))


# -- frames ----------------------------------------------------------------------------------------
def dumps(message: dict[str, Any]) -> bytes:
    """One frame: the message as compact JSON plus the terminating newline."""
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def loads(frame: bytes | str) -> dict[str, Any]:
    """One frame back to a message, with its version checked."""
    text = frame.decode("utf-8", errors="replace") if isinstance(frame, bytes) else frame
    try:
        message = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"frame is not valid JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError(f"frame must be a JSON object, got {type(message).__name__}")
    if message.get("v") != VERSION:
        raise ProtocolError(f"frame speaks schema version {message.get('v')!r}, this peer speaks {VERSION}")
    return message


def request(op: str, **fields: Any) -> dict[str, Any]:
    """A request message for ``op``: ``{"v": 1, "op": ..., **fields}``."""
    if op not in OPS:
        raise ProtocolError(f"unknown op {op!r}; known ops: {', '.join(OPS)}")
    return {"v": VERSION, "op": op, **fields}


def ok_response(result: Any) -> dict[str, Any]:
    """A successful response carrying ``result`` (the op's return value, already encoded)."""
    return {"v": VERSION, "ok": True, "result": result}


def error_response(exc: BaseException) -> dict[str, Any]:
    """A failed response. The exception type travels by name so the client can re-raise in kind."""
    return {"v": VERSION, "ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}


def result_of(message: dict[str, Any]) -> Any:
    """The ``result`` of a response, or raise what the far side raised.

    Every remote failure raises :class:`RemoteEngineError`, which *is* a ``RuntimeError``: the stub's
    contract misuse ("report() the outstanding ... first") therefore fails the same way in process and
    over a socket. The remote class name is carried on the exception rather than re-instantiated, so
    the client never invents an exception class it does not have.
    """
    if "ok" not in message:
        raise ProtocolError(f"response has no 'ok' field: {sorted(message)}")
    if message["ok"]:
        if "result" not in message:
            raise ProtocolError("successful response has no 'result' field")
        return message["result"]
    error = message.get("error")
    if not isinstance(error, dict) or "message" not in error:
        raise ProtocolError(f"failed response has no usable 'error' object: {error!r}")
    raise RemoteEngineError(str(error.get("type", "RuntimeError")), str(error["message"]))


# -- the server side -------------------------------------------------------------------------------
def handle(engine: EngineClient, message: dict[str, Any]) -> dict[str, Any]:
    """Apply one decoded request to ``engine`` and return the response message.

    The only place the three ops are dispatched. Every exception the engine raises -- including the
    contract misuse it is *supposed* to raise on -- becomes an error response; the connection is not
    the engine's to drop.
    """
    op = message.get("op")
    try:
        if op == "next_command":
            return ok_response(encode_command(engine.next_command()))
        if op == "report":
            if "outcome" not in message:
                raise ProtocolError("report request is missing 'outcome'")
            engine.report(decode_outcome(message["outcome"]))
            return ok_response(None)
        if op == "board_state":
            state = engine.board_state()
            if not isinstance(state, dict):
                raise ProtocolError(f"board_state must be a mapping, got {type(state).__name__}")
            return ok_response(state)
        raise ProtocolError(f"unknown op {op!r}; known ops: {', '.join(OPS)}")
    except Exception as exc:
        # Deliberately broad: every engine-side failure is *reported*, never dropped. The connection
        # is the transport's to manage, and a server that died on a bad request would look to the
        # client exactly like an engine that had gone away.
        return error_response(exc)
