"""The networked engine: schema, client, served stub (T-039; CLAUDE.md 5.5, R5).

Nothing here touches hardware and nothing here sends a motion command: a served stub is a simulated
engine on a loopback socket. Two kinds of server are used deliberately -- a **subprocess** for the
acceptance checks (50 commands round trip; a dropped server raises rather than hangs, which only a
real process can be killed to show) and an **in-process thread** for the finer behaviour (reconnect,
remote errors, bad frames), where a thread is cheaper and the assertions can see the server object.

Every test that waits on a socket asserts an *elapsed time* as well as an exception: "does not hang"
is the criterion, and an exception raised after 30 s would satisfy a test that only checked the type.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from engine import schema
from engine.interface import Cell, Command, Outcome, Primitive
from engine.net import EngineUnavailable, NetEngineClient, parse_url, settings
from engine.schema import ProtocolError, RemoteEngineError
from engine.serve_stub import StubServer, build_engine
from engine.stub import StubEngine
from runtime.safety import REPO_ROOT

OK = Outcome(success=True, observed_state_delta={}, failure_mode=None)
LOOPBACK = "127.0.0.1:0"
#: Seed shared by the parity checks: the socket must not change what the same seed produces.
SEED = 7


def key(cmd: Command | None) -> tuple:
    """The comparable identity of a command, as tests/test_engine_stub.py defines it."""
    if cmd is None:
        return ()
    return (cmd.primitive, None if cmd.src is None else cmd.src.id,
            None if cmd.dst is None else cmd.dst.id, cmd.horse_id)


@pytest.fixture
def served():
    """A :class:`StubServer` on a free loopback port, serving a seeded stub in a thread."""
    server = StubServer(StubEngine(SEED), LOOPBACK)
    thread = threading.Thread(target=server.serve_forever, name="stub-server", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture
def client(served):
    with NetEngineClient(served.url, request_timeout_s=2.0, connect_timeout_s=2.0) as c:
        yield c


# --------------------------------------------------------------------------------------------
# the schema: encode/decode, versioning, errors
# --------------------------------------------------------------------------------------------
CELLS = (
    Cell("track-12", (40.0, 280.0), None),
    Cell("R-base-2", (-220.0, -220.0), (321.5, 118.0)),
    None,
)


@pytest.mark.parametrize("cell", CELLS)
def test_cell_round_trips_including_a_null_top_px(cell):
    assert schema.decode_cell(schema.encode_cell(cell)) == cell


@pytest.mark.parametrize("command", [
    None,
    Command(Primitive.ROLL, None, None, None),
    Command(Primitive.MOVE, CELLS[0], CELLS[1], "R0"),
    Command(Primitive.RECOVER, CELLS[1], CELLS[1], "B3"),
])
def test_command_round_trips(command):
    assert schema.decode_command(schema.encode_command(command)) == command


@pytest.mark.parametrize("outcome", [
    OK,
    Outcome(success=False, observed_state_delta={"horses": {"R0": ["track-1", "track-3"]}}, failure_mode="horse_fell"),
])
def test_outcome_round_trips(outcome):
    assert schema.decode_outcome(schema.encode_outcome(outcome)) == outcome


def test_a_frame_is_one_line_of_json():
    frame = schema.dumps(schema.request("next_command"))
    assert frame.endswith(b"\n") and frame.count(b"\n") == 1
    assert schema.loads(frame[:-1]) == {"v": 1, "op": "next_command"}


def test_another_schema_version_is_refused_rather_than_guessed_at():
    with pytest.raises(ProtocolError, match="version 2"):
        schema.loads(b'{"v": 2, "op": "next_command"}')


@pytest.mark.parametrize("frame", [b"not json", b"[1, 2]", b'{"op": "next_command"}'])
def test_malformed_frames_are_protocol_errors(frame):
    with pytest.raises(ProtocolError):
        schema.loads(frame)


def test_unknown_op_is_refused_at_the_request_and_at_the_server():
    with pytest.raises(ProtocolError, match="unknown op"):
        schema.request("resign")
    response = schema.handle(StubEngine(0), {"v": 1, "op": "resign"})
    assert response["ok"] is False and "unknown op" in response["error"]["message"]


def test_result_of_reraises_what_the_far_side_raised():
    response = schema.error_response(RuntimeError("report() with no command outstanding"))
    with pytest.raises(RemoteEngineError, match="report\\(\\) with no command outstanding") as excinfo:
        schema.result_of(response)
    assert excinfo.value.remote_type == "RuntimeError"
    assert isinstance(excinfo.value, RuntimeError)  # identical to the in-process failure (5.5)


@pytest.mark.parametrize("url,expected", [
    ("tcp://127.0.0.1:5555", ("127.0.0.1", 5555)),
    ("zmq://127.0.0.1:5555", ("127.0.0.1", 5555)),
    ("127.0.0.1:5555", ("127.0.0.1", 5555)),
    ("tcp://localhost", ("localhost", 5555)),
    ("tcp://127.0.0.1:0", ("127.0.0.1", 0)),  # 0 is a real request, not "use the default"
])
def test_parse_url(url, expected):
    assert parse_url(url) == expected


@pytest.mark.parametrize("url", ["http://127.0.0.1:5555", "tcp://"])
def test_a_url_this_client_cannot_speak_fails_at_construction(url):
    with pytest.raises(ValueError, match="engine url"):
        parse_url(url)


def test_the_defaults_come_from_config():
    block = settings()
    assert parse_url(block["url"]) == ("127.0.0.1", 5555)
    assert block["request_timeout_s"] == 2.0 and block["connect_timeout_s"] == 2.0
    client = NetEngineClient()
    assert (client.url, client.request_timeout_s) == (block["url"], float(block["request_timeout_s"]))


# --------------------------------------------------------------------------------------------
# acceptance 1: the served stub is the in-process stub
# --------------------------------------------------------------------------------------------
def test_fifty_commands_over_a_subprocess_match_the_in_process_stub():
    """The acceptance check: same seed, same 50 commands, whether the engine is local or remote."""
    local = StubEngine(SEED)
    with _server_process(SEED) as url, NetEngineClient(url) as remote:
        for i in range(50):
            expected, got = local.next_command(), remote.next_command()
            assert key(got) == key(expected), f"command {i} differs over the socket"
            assert got == expected  # frozen dataclasses: structural equality, board_xy_mm included
            local.report(OK)
            remote.report(OK)
        assert local.board_state() == remote.board_state()
        assert remote.requests == 50 * 2 + 1 and remote.reconnects == 0


def test_a_script_and_a_failure_stream_survive_the_socket(served, client):
    """A failed report gets the same RECOVER + re-issue over the socket as in process (docs/engine.md)."""
    local = StubEngine(SEED)
    fail = Outcome(success=False, observed_state_delta={}, failure_mode="grasp_failed")
    for i in range(12):
        outcome = fail if i % 3 == 0 else OK
        assert key(client.next_command()) == key(local.next_command()), f"command {i}"
        client.report(outcome)
        local.report(outcome)
    assert client.board_state()["failures"] == local.board_state()["failures"]


def test_recover_commands_do_appear_in_the_stream(served, client):
    fail = Outcome(success=False, observed_state_delta={}, failure_mode="grasp_failed")
    first = client.next_command()
    assert first is not None
    client.report(fail)
    recovery = client.next_command()
    assert recovery is not None and recovery.primitive is Primitive.RECOVER


def test_board_state_is_a_json_object_with_the_stub_fields(client):
    state = client.board_state()
    assert set(state) >= {"robot_color", "game", "turn", "die", "horses", "progress", "failures"}
    assert isinstance(state["horses"], dict)


def test_contract_misuse_raises_over_the_socket_too(client):
    client.next_command()
    with pytest.raises(RuntimeError, match="report"):
        client.next_command()
    # The connection is untouched: an engine that said no is not an engine that went away.
    client.report(OK)
    assert client.next_command() is not None


def test_a_script_served_over_the_socket_ends_with_none():
    commands = [Command(Primitive.ROLL, None, None, None)]
    server = StubServer(StubEngine(0, script=commands), LOOPBACK)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with NetEngineClient(server.url) as client:
            assert key(client.next_command()) == key(commands[0])
            client.report(OK)
            assert client.next_command() is None
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_build_engine_loads_a_script_by_name():
    engine = build_engine(0, "eval_20_moves")
    assert engine.next_command() is not None


# --------------------------------------------------------------------------------------------
# acceptance 2: a dropped or silent server raises EngineUnavailable, and does not hang
# --------------------------------------------------------------------------------------------
def test_a_dropped_server_raises_engine_unavailable_and_does_not_hang():
    """The acceptance check: kill the engine process mid-game; the next call fails fast and clearly."""
    with _server_process(SEED) as url:
        client = NetEngineClient(url, request_timeout_s=2.0)
        assert client.next_command() is not None
    # the context manager killed the server; the socket is still open on this side
    started = time.monotonic()
    with pytest.raises(EngineUnavailable) as excinfo:
        client.report(OK)
    elapsed = time.monotonic() - started
    assert elapsed < 2.5, f"a dead server took {elapsed:.2f} s to notice"
    assert url in str(excinfo.value)
    # and the next call reconnects rather than reusing a dead socket: nothing is there, so it says so
    with pytest.raises(EngineUnavailable, match="cannot connect"):
        client.next_command()
    client.close()


def test_connecting_to_nothing_raises_engine_unavailable():
    free = _free_port()
    client = NetEngineClient(f"tcp://127.0.0.1:{free}", connect_timeout_s=2.0)
    started = time.monotonic()
    with pytest.raises(EngineUnavailable, match="cannot connect"):
        client.next_command()
    assert time.monotonic() - started < 2.5


def test_a_server_that_never_answers_times_out_within_the_budget():
    """A socket that accepts and then says nothing is the case a naive client hangs on forever."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    accepted: list[socket.socket] = []
    thread = threading.Thread(target=lambda: accepted.append(listener.accept()[0]), daemon=True)
    thread.start()
    try:
        client = NetEngineClient(f"tcp://127.0.0.1:{listener.getsockname()[1]}", request_timeout_s=0.5)
        started = time.monotonic()
        with pytest.raises(EngineUnavailable, match="no response within"):
            client.next_command()
        elapsed = time.monotonic() - started
        assert 0.4 < elapsed < 1.5, f"timed out after {elapsed:.2f} s, budget was 0.5 s"
        client.close()
    finally:
        thread.join(timeout=5)
        for sock in accepted:
            sock.close()
        listener.close()


def test_the_client_reconnects_after_a_dropped_connection(served, client):
    assert client.next_command() is not None
    client.report(OK)
    client.close()                      # as a timeout would leave it
    assert client.next_command() is not None
    assert client.reconnects == 1 and served.connections == 2


def test_a_nonsense_frame_is_answered_not_dropped(served):
    """A client that is not speaking this protocol gets told so; the server stays up for the next one."""
    with socket.create_connection((served.host, served.port), timeout=2.0) as sock:
        sock.sendall(b'{"v": 99, "op": "next_command"}\n')
        response = schema.loads(sock.makefile("rb").readline())
    assert response["ok"] is False and "version" in response["error"]["message"]
    with NetEngineClient(served.url) as client:
        assert client.next_command() is not None


def test_an_oversized_response_is_refused_rather_than_buffered():
    """The frame cap, exercised from the client side against a server that floods it."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def flood() -> None:
        conn, _ = listener.accept()
        with conn:
            chunk = b"x" * 65536
            try:
                while True:
                    conn.sendall(chunk)
            except OSError:
                pass

    thread = threading.Thread(target=flood, daemon=True)
    thread.start()
    try:
        client = NetEngineClient(f"tcp://127.0.0.1:{listener.getsockname()[1]}", request_timeout_s=10.0)
        with pytest.raises(EngineUnavailable, match="exceeded"):
            client.next_command()
        client.close()
    finally:
        thread.join(timeout=10)
        listener.close()


# --------------------------------------------------------------------------------------------
# the controller runs against a remote engine (acceptance: --engine <url>)
# --------------------------------------------------------------------------------------------
def test_build_wires_a_url_to_a_net_engine_client(served):
    from runtime.controller import build

    controller = build("mock", engine=served.url)
    assert isinstance(controller._engine, NetEngineClient)
    controller._engine.close()


def test_build_defaults_to_the_in_process_stub():
    from runtime.controller import build

    assert isinstance(build("mock", seed=3)._engine, StubEngine)


def test_the_controller_cli_runs_against_a_served_engine(capsys):
    """The acceptance run, in miniature: the loop plays commands it got over a socket."""
    from runtime.controller import main

    with _server_process(SEED) as url:
        code = main(["--backend", "mock", "--engine", url, "--seconds", "1.0", "--session", "netcli"])
    printed = capsys.readouterr().out
    assert code == 0
    assert "controller run summary" in printed and f"engine {url}" in printed
    assert "commands" in printed


def test_the_controller_cli_reports_an_absent_engine_instead_of_hanging(capsys):
    from runtime.controller import main

    started = time.monotonic()
    code = main(["--backend", "mock", "--engine", f"tcp://127.0.0.1:{_free_port()}", "--seconds", "5"])
    elapsed = time.monotonic() - started
    assert code == 3 and "engine unavailable" in capsys.readouterr().out
    assert elapsed < 5.0, f"the CLI took {elapsed:.2f} s to give up on an engine that is not there"


def test_the_controller_cli_rejects_a_url_it_cannot_speak(capsys):
    from runtime.controller import main

    assert main(["--backend", "mock", "--engine", "http://127.0.0.1:5555", "--seconds", "1"]) == 2
    assert "cannot use engine" in capsys.readouterr().out


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------
def _free_port() -> int:
    """A port nothing is listening on: bound, read, released."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _server_process:
    """``engine/serve_stub.py`` in a real subprocess, on a port the OS picks.

    The port comes back on stdout ("listening tcp://host:port"), which is why the CLI prints it.
    Leaving the context kills the process, which is exactly the dropped-server case.
    """

    def __init__(self, seed: int, script: str | None = None) -> None:
        self.seed, self.script = seed, script
        self.process: subprocess.Popen | None = None

    def __enter__(self) -> str:
        argv = [sys.executable, "-m", "engine.serve_stub", "--bind", LOOPBACK, "--seed", str(self.seed)]
        if self.script:
            argv += ["--script", self.script]
        self.process = subprocess.Popen(argv, cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
        line = self.process.stdout.readline().strip()
        if not line.startswith("listening "):
            self.__exit__(None, None, None)
            raise AssertionError(f"serve_stub did not announce its port: {line!r}")
        return line.split(None, 1)[1]

    def __exit__(self, *exc_info: object) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        process.kill()
        process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()


def test_the_module_is_runnable_as_a_script():
    """``python -m engine.serve_stub`` is the documented way to start it (docs/engine.md)."""
    assert (Path(REPO_ROOT) / "engine" / "serve_stub.py").is_file()
    with _server_process(0) as url:
        assert url.startswith("tcp://127.0.0.1:")
