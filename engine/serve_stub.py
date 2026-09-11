"""Serve :class:`~engine.stub.StubEngine` on the network protocol of ``engine/schema.py`` (T-039).

```bash
.venv/bin/python -m engine.serve_stub --bind 127.0.0.1:5555 --seed 0
.venv/bin/python -m runtime.controller --backend mock --engine tcp://127.0.0.1:5555 --seconds 20
```

The point is not the stub -- the controller can have that in process -- it is the *socket*: the
remote-engine path (connect, request, response, timeout, reconnect) is exercised end to end today, so
when the engine team plugs their process in, only their side is new. The commands a served stub hands
out are exactly the commands the same seed produces in process, because it is the same object; a test
asserts that over 50 commands.

One engine, one connection at a time, requests answered in order. That is what the contract allows:
there is one outstanding command at any moment (``docs/engine.md``), so a second concurrent client
would corrupt the state machine rather than share it. A client that disconnects frees the server for
the next one, which is how :class:`~engine.net.NetEngineClient` reconnects after a timeout.

This file serves a *simulated* engine over a socket and commands no motion; R1 does not touch it.
"""

from __future__ import annotations

import argparse
import socket
import threading

from engine import net, schema
from engine.interface import EngineClient
from engine.stub import StubEngine, load_script
from runtime.log import configure, get_logger

__all__ = ["StubServer", "main"]

#: How long the accept loop blocks before re-checking :meth:`StubServer.shutdown`.
ACCEPT_POLL_S = 0.2


class StubServer:
    """A TCP JSON-lines server in front of one :class:`~engine.interface.EngineClient`.

    ``bind`` is ``host:port`` (a ``tcp://`` URL is accepted too, so the address in
    ``config/training.yaml`` can be passed through unchanged). Port 0 asks the OS for a free one and
    :attr:`url` then names it, which is what the tests use so that two runs never collide on a port.
    There is no default: the address is a configured value (``engine.url``), not a constant in code.
    """

    def __init__(self, engine: EngineClient, bind: str, *, backlog: int = 1) -> None:
        host, port = net.parse_url(bind)
        self.engine = engine
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(ACCEPT_POLL_S)
        self._sock.bind((host, port))
        self._sock.listen(backlog)
        self.host, self.port = self._sock.getsockname()[:2]
        self.connections = 0
        self.requests = 0
        self._log = get_logger("engine.serve_stub", url=self.url)

    def __repr__(self) -> str:
        return f"StubServer({self.url!r}, engine={type(self.engine).__name__}, requests={self.requests})"

    @property
    def url(self) -> str:
        """The URL a :class:`~engine.net.NetEngineClient` connects to."""
        return f"tcp://{self.host}:{self.port}"

    def __enter__(self) -> StubServer:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()

    def serve_forever(self) -> None:
        """Accept one connection at a time and answer its requests until :meth:`shutdown`."""
        self._log.info("serving", engine=type(self.engine).__name__)
        while not self._stop.is_set():
            try:
                conn, peer = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:  # the listening socket was closed under us by shutdown()
                break
            self.connections += 1
            self._log.info("client_connected", peer=f"{peer[0]}:{peer[1]}", connections=self.connections)
            with conn:
                self._serve_connection(conn)
            self._log.info("client_disconnected", peer=f"{peer[0]}:{peer[1]}", requests=self.requests)

    def _serve_connection(self, conn: socket.socket) -> None:
        """Answer requests on one connection until the client goes away (or the server stops)."""
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(ACCEPT_POLL_S)
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = conn.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            if len(buffer) > schema.MAX_FRAME_BYTES:
                self._log.warning("frame_too_long", bytes=len(buffer))
                return
            while b"\n" in buffer:
                frame, _, buffer = buffer.partition(b"\n")
                if not self._answer(conn, frame):
                    return

    def _answer(self, conn: socket.socket, frame: bytes) -> bool:
        """Answer one frame. False means the connection is gone and the loop should stop."""
        try:
            response = schema.handle(self.engine, schema.loads(frame))
        except schema.ProtocolError as exc:
            # A frame this server cannot parse is still answered: a client that sent nonsense learns
            # why, instead of watching its connection drop and calling the engine unavailable.
            self._log.warning("bad_frame", detail=str(exc))
            response = schema.error_response(exc)
        try:
            conn.sendall(schema.dumps(response))
        except OSError as exc:
            self._log.warning("send_failed", detail=str(exc))
            return False
        self.requests += 1
        return True

    def shutdown(self) -> None:
        """Stop the accept loop and close the listening socket. Idempotent."""
        self._stop.set()
        try:
            self._sock.close()
        except OSError:  # pragma: no cover - nothing to do about a failing close
            pass


def build_engine(seed: int = 0, script: str | None = None) -> StubEngine:
    """The engine this server serves: random games, or exactly the commands of ``script``."""
    return StubEngine(seed, script=None if script is None else load_script(script))


def main(argv: list[str] | None = None) -> int:
    """``python -m engine.serve_stub --bind 127.0.0.1:5555 --seed 0``."""
    parser = argparse.ArgumentParser(description="Serve the stub engine over the network protocol (T-039).")
    parser.add_argument("--bind", default=None,
                        help="host:port to listen on (default: engine.url in config/training.yaml)")
    parser.add_argument("--seed", type=int, default=0, help="stub engine seed (default 0)")
    parser.add_argument("--script", default=None, help="engine/scripts/<name>.yaml, or a path; default random games")
    parser.add_argument("--json-logs", action="store_true", help="JSON log lines instead of key=value")
    args = parser.parse_args(argv)

    configure(json=args.json_logs)
    server = StubServer(build_engine(args.seed, args.script), args.bind or net.settings()["url"])
    # stdout, flushed, because a caller that asked for port 0 has no other way to learn the port.
    print(f"listening {server.url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
