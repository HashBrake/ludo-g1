"""The engine over a socket: :class:`NetEngineClient` (T-039, CLAUDE.md 5.5).

The real game engine is another team's process. This is the integration point they target: an
:class:`~engine.interface.EngineClient` that speaks ``engine/schema.py`` over a TCP connection, so
``runtime/controller.py`` cannot tell a remote engine from ``engine/stub.py`` -- same three methods,
same objects, same exceptions. ``engine/serve_stub.py`` serves the stub on this protocol, so the
"remote engine" path runs today, months before the engine exists.

**Transport.** Plain TCP with one JSON object per line, from the standard library. CLAUDE.md's task
named ZeroMQ REQ/REP *if pyzmq were already present*; it is not installed in ``.venv`` and T-039 may
add no dependency, so the protocol is JSON-lines and the URL is ``tcp://host:port``. The framing is
request/response in lock step on one connection, which is REQ/REP semantics on a socket both ends
already have; ``docs/engine.md`` is the document the engine team implements against, and nothing in
it assumes either library.

**Timeouts and reconnection.** Every call is bounded by ``engine.request_timeout_s`` in
``config/training.yaml`` (2.0 s): a connect that does not complete, a response that does not arrive,
or a server that vanished raises :class:`EngineUnavailable` within that budget and never blocks the
controller's tick for longer. The socket is then closed and the *next* call reconnects, so an engine
that restarts is picked up without restarting the controller. A failure the engine itself reports
(``report()`` with nothing outstanding) is not a transport failure: it raises
:class:`~engine.schema.RemoteEngineError`, the connection stays up, and the state machine on the far
side is untouched.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from engine import schema
from engine.interface import Command, EngineClient, Outcome
from engine.schema import ProtocolError, RemoteEngineError
from runtime import config
from runtime.log import get_logger

__all__ = ["DEFAULT_PORT", "EngineUnavailable", "NetEngineClient", "ProtocolError", "RemoteEngineError",
           "parse_url", "settings"]

#: Port used when a URL names only a host. 5555 is the task's, and ``config/training.yaml``'s.
DEFAULT_PORT = 5555

#: URL schemes accepted. ``zmq`` is accepted as a synonym so that a URL written against the task's
#: wording connects rather than failing obscurely; the wire protocol is the one in engine/schema.py
#: either way, and ``docs/engine.md`` says so.
SCHEMES = ("tcp", "zmq")


class EngineUnavailable(RuntimeError):
    """The engine could not be reached: no connection, no response in time, or the peer went away.

    A ``RuntimeError`` so that a caller which already guards the engine call needs no new except
    clause, and a distinct class so that "the engine is not there" is never confused with "the engine
    said no" (:class:`~engine.schema.RemoteEngineError`).
    """


def settings(config_root: Path | str | None = None) -> dict[str, Any]:
    """The ``engine`` block of ``config/training.yaml``: the URL and the two timeouts."""
    training = config.load("training", root=config_root)
    block = training.get("engine")
    if not isinstance(block, dict):
        raise config.ConfigError("config/training.yaml: missing the 'engine' block (url, timeouts)")
    return block


def parse_url(url: str) -> tuple[str, int]:
    """``tcp://127.0.0.1:5555`` -> ``("127.0.0.1", 5555)``.

    Raises :class:`ValueError` for a scheme this client does not speak, so a mistyped URL fails at
    construction with the reason, not at the first tick with a connection error.
    """
    parts = urlsplit(url if "://" in url else f"tcp://{url}")
    if parts.scheme not in SCHEMES:
        raise ValueError(f"engine url {url!r}: scheme must be one of {', '.join(SCHEMES)}")
    if not parts.hostname:
        raise ValueError(f"engine url {url!r}: no host")
    # `or DEFAULT_PORT` would be wrong: port 0 is a real request (let the OS pick one).
    return parts.hostname, DEFAULT_PORT if parts.port is None else int(parts.port)


class NetEngineClient(EngineClient):
    """An :class:`~engine.interface.EngineClient` backed by a socket.

    ``url`` defaults to ``engine.url`` in ``config/training.yaml``; both timeouts default to the same
    file. The connection is lazy: constructing a client touches no socket, so a controller can be
    built before the engine process is up.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        request_timeout_s: float | None = None,
        connect_timeout_s: float | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        block = settings(config_root)
        self.url = str(block["url"] if url is None else url)
        self.host, self.port = parse_url(self.url)
        self.request_timeout_s = float(block["request_timeout_s"] if request_timeout_s is None else request_timeout_s)
        self.connect_timeout_s = float(block["connect_timeout_s"] if connect_timeout_s is None else connect_timeout_s)
        self._sock: socket.socket | None = None
        self._buffer = b""
        self.requests = 0
        self.reconnects = 0
        self._log = get_logger("engine.net", url=self.url)

    def __repr__(self) -> str:
        state = "connected" if self._sock is not None else "disconnected"
        return f"NetEngineClient({self.url!r}, {state}, requests={self.requests})"

    def __enter__(self) -> NetEngineClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- EngineClient ------------------------------------------------------------------------------
    def next_command(self) -> Command | None:
        """The engine's next command, or ``None`` when there is nothing left to play."""
        return schema.decode_command(self._call("next_command"))

    def report(self, outcome: Outcome) -> None:
        """Report the outstanding command's outcome. One report per command, as in process."""
        self._call("report", outcome=schema.encode_outcome(outcome))

    def board_state(self) -> dict[str, Any]:
        """The engine's view of the board, as it serialised it."""
        state = self._call("board_state")
        if not isinstance(state, dict):
            raise EngineUnavailable(f"{self.url}: board_state returned {type(state).__name__}, expected an object")
        return state

    # -- transport ---------------------------------------------------------------------------------
    def close(self) -> None:
        """Drop the connection. Idempotent; the next call reconnects."""
        sock, self._sock, self._buffer = self._sock, None, b""
        if sock is not None:
            try:
                sock.close()
            except OSError:  # pragma: no cover - close() failing changes nothing we can act on
                pass

    def _connect(self) -> socket.socket:
        if self._sock is not None:
            return self._sock
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout_s)
        except OSError as exc:
            raise EngineUnavailable(f"cannot connect to {self.url}: {exc}") from exc
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock, self._buffer = sock, b""
        if self.requests:
            self.reconnects += 1
        self._log.info("engine_connected", host=self.host, port=self.port, reconnects=self.reconnects)
        return sock

    def _call(self, op: str, **fields: Any) -> Any:
        """One request, one response, within ``request_timeout_s`` end to end."""
        message = schema.request(op, **fields)
        deadline = time.monotonic() + self.request_timeout_s
        sock = self._connect()
        try:
            sock.settimeout(max(0.0, deadline - time.monotonic()))
            sock.sendall(schema.dumps(message))
            frame = self._read_frame(sock, deadline)
            response = schema.loads(frame)
        except (OSError, ProtocolError) as exc:
            self.close()
            raise EngineUnavailable(f"{self.url}: {op} failed: {exc}") from exc
        self.requests += 1
        return schema.result_of(response)

    def _read_frame(self, sock: socket.socket, deadline: float) -> bytes:
        """Read up to the next newline, never past ``deadline`` and never past the frame cap."""
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no response within {self.request_timeout_s:g} s")
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(65536)
            except TimeoutError as exc:
                # The socket's own timeout, re-raised with the budget in it: "timed out" alone does
                # not say whose budget was spent.
                raise TimeoutError(f"no response within {self.request_timeout_s:g} s") from exc
            if not chunk:
                raise ConnectionError("engine closed the connection")
            self._buffer += chunk
            if len(self._buffer) > schema.MAX_FRAME_BYTES:
                raise ConnectionError(f"response exceeded {schema.MAX_FRAME_BYTES} bytes")
        frame, _, self._buffer = self._buffer.partition(b"\n")
        return frame
