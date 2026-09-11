"""The real G1 arm driver: read-only ``rt/lowstate`` (T-018; CLAUDE.md 5.1, 5.3, R1, R2).

The robot LAN is down (agents/HARDWARE_NEEDED.md H-002), so the weight here is carried by a fake
subscriber that hands the driver **real** ``unitree_hg.msg.dds_.LowState_`` objects built from the
installed idl, on a fake clock. That exercises every field access the real callback performs and the
whole unpacking path, at 500 Hz, in milliseconds and with nothing plugged in.

The tests that need the actual robot are marked ``readonly`` and skip with a reason naming the DDS
interface, the same discipline as ``tests/test_cameras.py``. Nothing in this file can move anything:
the driver has no writer at all until T-021, and a test asserts it by grepping the module.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from unitree_sdk2py.idl.unitree_hg.msg.dds_ import IMUState_, LowState_, MotorState_

from drivers import make
from drivers.g1_arm import ArmProbe, ArmUnavailable, FullState, G1Arm
from drivers.interfaces import ArmDriver
from runtime import config
from runtime.clock import Stamped
from runtime.types import ARM_DOF, MotionCommand
from tools.hardware_checks import stream_stats

REPO = Path(__file__).resolve().parents[1]

#: A plausible interface name for the fake link. Nothing opens it: the fake subscriber never touches
#: the network, and the name only has to be something other than the UNMEASURED placeholder.
FAKE_INTERFACE = "enxfake0"

#: Slots in one LowState_ message (35), of which config/robot.yaml control.motor_count are joints.
MOTOR_SLOTS = 35

#: Run in a fresh interpreter: importing the driver must bind no DDS factory and import no SDK.
PROBE_IMPORT = "import sys, drivers.g1_arm as m; print(m.dds_binding(), 'unitree_sdk2py' in sys.modules)"


class FakeClock:
    """The injectable monotonic clock of docs/drivers.md: nanoseconds, advanced by hand."""

    def __init__(self) -> None:
        self.ns = 0

    def __call__(self) -> int:
        return self.ns

    def advance(self, seconds: float) -> None:
        self.ns += round(seconds * 1e9)


class FakeSubscriber:
    """Stands in for ``unitree_sdk2py.core.channel.ChannelSubscriber``: Init, Close, and a handler."""

    def __init__(self, topic: str, domain_id: int, interface: str) -> None:
        self.topic, self.domain_id, self.interface = topic, domain_id, interface
        self.handler: Any = None
        self.queue_len: int | None = None
        self.closes = 0

    def Init(self, handler: Any = None, queueLen: int = 0) -> None:  # noqa: N803 - the SDK's spelling
        self.handler, self.queue_len = handler, queueLen

    def Close(self) -> None:
        self.closes += 1

    def deliver(self, msg: LowState_) -> None:
        """Hand one message to the driver, as the cyclonedds reader thread would."""
        self.handler(msg)


def motor(q: float = 0.0, dq: float = 0.0, tau: float = 0.0) -> MotorState_:
    return MotorState_(
        mode=0,
        q=q,
        dq=dq,
        ddq=0.0,
        tau_est=tau,
        temperature=[0, 0],
        vol=0.0,
        sensor=[0, 0],
        motorstate=0,
        reserve=[0, 0, 0, 0],
    )


def low_state(q: list[float] | None = None, *, mode_machine: int = 5, tick: int = 0) -> LowState_:
    """A real ``LowState_``: 35 motor slots, ``q[i] = i/100`` by default, dq and tau derived from q."""
    values = [i / 100.0 for i in range(MOTOR_SLOTS)] if q is None else list(q)
    values += [0.0] * (MOTOR_SLOTS - len(values))
    return LowState_(
        version=[0, 0],
        mode_pr=0,
        mode_machine=mode_machine,
        tick=tick,
        imu_state=IMUState_(
            quaternion=[1.0, 0.0, 0.0, 0.0],
            gyroscope=[0.0, 0.0, 0.0],
            accelerometer=[0.0, 0.0, 0.0],
            rpy=[0.0, 0.0, 0.0],
            temperature=0,
        ),
        motor_state=[motor(v, dq=-v, tau=2 * v) for v in values],
        wireless_remote=[0] * 40,
        reserve=[0] * 4,
        crc=0,
    )


def robot_root(tmp_path: Path, **overrides: Any) -> Path:
    """A config directory whose ``robot.yaml`` has the given top-level subtrees updated."""
    data = yaml.safe_load((REPO / "config" / "robot.yaml").read_text(encoding="utf-8"))
    data["network"]["dds_interface"] = FAKE_INTERFACE
    for key, values in overrides.items():
        data[key].update(values)
    (tmp_path / "robot.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path


def linked_arm(tmp_path: Path, **kwargs: Any) -> tuple[G1Arm, FakeSubscriber, FakeClock]:
    """A driver wired to a fake subscriber on a fake clock. Nothing real is opened."""
    built: list[FakeSubscriber] = []

    def factory(topic: str, domain_id: int, interface: str) -> FakeSubscriber:
        built.append(FakeSubscriber(topic, domain_id, interface))
        return built[-1]

    clk = FakeClock()
    root = kwargs.pop("config_root", None) or robot_root(tmp_path)
    arm = G1Arm(subscriber_factory=factory, now_ns=clk, config_root=root, **kwargs)
    return arm, built[0], clk


def real_arm() -> G1Arm:
    """Open the real state stream or skip, naming the interface. Used by every ``readonly`` test."""
    interface = config.load("robot")["network"]["dds_interface"]
    try:
        return G1Arm()
    except ArmUnavailable as exc:
        pytest.skip(f"no G1 state stream on interface {interface!r}: {exc}")


# --------------------------------------------------------------------------------------------------
# R1/R2: this driver cannot move anything
# --------------------------------------------------------------------------------------------------


def test_the_module_creates_no_publisher_and_names_no_command_topic() -> None:
    """T-018 creates no DDS writer of any kind: no publisher class, no command topic string (R1)."""
    source = (REPO / "drivers" / "g1_arm.py").read_text(encoding="utf-8")
    for forbidden in ("ChannelPublisher", "rt/arm_sdk", "rt/lowcmd"):
        assert forbidden not in source, f"drivers/g1_arm.py must not mention {forbidden!r} in T-018"
    assert "ChannelSubscriber" in source, "the read path is a subscriber"


def test_send_targets_refuses_and_names_the_task_that_will_implement_it(tmp_path: Path) -> None:
    arm, _, _ = linked_arm(tmp_path)
    cmd = MotionCommand(arm=np.zeros(ARM_DOF), waist_yaw=0.0, pinch=0.0)
    with pytest.raises(NotImplementedError) as exc:
        arm.send_targets(cmd)
    assert "T-021" in str(exc.value)


def test_the_driver_satisfies_the_arm_driver_protocol() -> None:
    """Same protocol as MockArm, so nothing downstream of a driver can tell them apart."""
    assert isinstance(G1Arm.__new__(G1Arm), ArmDriver)


def test_the_driver_builds_no_guard_and_holds_no_session(tmp_path: Path) -> None:
    """There is nothing to admit: with no writer there is no motion path to guard (R3 applies at T-021)."""
    from drivers import g1_arm

    arm, _, _ = linked_arm(tmp_path)
    assert not hasattr(arm, "guard")
    assert not hasattr(g1_arm, "Guard") and not hasattr(g1_arm, "safety")


def test_importing_the_module_initialises_no_dds_and_imports_no_sdk() -> None:
    """The factory binds the process to one interface, so it happens on first use, never on import."""
    done = subprocess.run(
        [sys.executable, "-c", PROBE_IMPORT],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "None False", done.stdout


# --------------------------------------------------------------------------------------------------
# subscription and unpacking
# --------------------------------------------------------------------------------------------------


def test_it_subscribes_to_the_configured_topic_domain_and_interface(tmp_path: Path) -> None:
    arm, sub, _ = linked_arm(tmp_path)
    robot = config.load("robot")
    expected = (robot["topics"]["state"], robot["network"]["dds_domain_id"], FAKE_INTERFACE)
    assert (sub.topic, sub.domain_id, sub.interface) == expected
    assert sub.topic == "rt/lowstate"
    assert sub.handler is not None and sub.queue_len == 10
    arm.close()
    arm.close()  # idempotent
    assert sub.closes == 1


def test_read_state_returns_the_configured_joints_in_action_order(tmp_path: Path) -> None:
    """The 7 arm joints and waist yaw are read at the indices config/robot.yaml declares (5.3)."""
    arm, sub, clk = linked_arm(tmp_path)
    robot = config.load("robot")
    sub.deliver(low_state())
    state = arm.read_state().payload
    expected = [j["index"] / 100.0 for j in robot["arm"]["joints"]]
    assert state.arm.tolist() == pytest.approx(expected)
    assert state.waist_yaw == pytest.approx(robot["waist"]["joints"][0]["index"] / 100.0)
    assert state.arm.shape == (ARM_DOF,)
    # The hand is a different device on a different SDK: LowState_ carries no pinch.
    assert state.pinch == 0.0


def test_the_sample_is_stamped_at_callback_time_not_at_read_time(tmp_path: Path) -> None:
    arm, sub, clk = linked_arm(tmp_path)
    clk.advance(0.25)
    sub.deliver(low_state())
    clk.advance(0.5)
    stamped = arm.read_state()
    assert stamped.ts_ns == round(0.25e9)
    assert stamped.payload.ts_ns == stamped.ts_ns


def test_full_state_carries_every_joint_for_the_dataset(tmp_path: Path) -> None:
    arm, sub, _ = linked_arm(tmp_path)
    n = int(config.load("robot")["control"]["motor_count"])
    sub.deliver(low_state(mode_machine=9, tick=1234))
    full = arm.full_state().payload
    assert isinstance(full, FullState)
    assert full.q.shape == full.dq.shape == full.tau_est.shape == (n,)
    assert full.q.tolist() == pytest.approx([i / 100.0 for i in range(n)])
    assert full.dq.tolist() == pytest.approx([-i / 100.0 for i in range(n)])
    assert full.tau_est.tolist() == pytest.approx([2 * i / 100.0 for i in range(n)])
    assert (full.mode_machine, full.tick) == (9, 1234)


def test_poll_drains_every_message_in_arrival_order(tmp_path: Path) -> None:
    arm, sub, clk = linked_arm(tmp_path)
    for i in range(5):
        clk.advance(0.002)
        sub.deliver(low_state(q=[i / 10.0] * MOTOR_SLOTS))
    samples = arm.poll()
    assert [s.ts_ns for s in samples] == [round((i + 1) * 0.002e9) for i in range(5)]
    assert [s.payload.waist_yaw for s in samples] == pytest.approx([i / 10.0 for i in range(5)])
    assert arm.poll() == []  # drained


def test_the_backlog_is_bounded(tmp_path: Path) -> None:
    """A consumer that never polls drops the oldest, exactly as a StreamBuffer would."""
    from drivers.g1_arm import BACKLOG

    arm, sub, clk = linked_arm(tmp_path)
    msg = low_state()
    for _ in range(BACKLOG + 50):
        clk.advance(0.002)
        sub.deliver(msg)
    assert len(arm.poll()) == BACKLOG


# --------------------------------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------------------------------


def test_probe_measures_the_rate_the_arrivals_imply(tmp_path: Path) -> None:
    """500 messages on a 500 Hz fake clock: the probe reports what arrived, not config state_hz."""
    arm, sub, clk = linked_arm(tmp_path)
    for i in range(500):
        clk.advance(1 / 500)
        sub.deliver(low_state(mode_machine=4, tick=i))
    probe = arm.probe()
    assert isinstance(probe, ArmProbe)
    assert probe.state_hz == pytest.approx(500.0, rel=0.01)
    assert probe.samples == 500
    assert probe.expected_hz == float(config.load("robot")["control"]["state_hz"])
    assert (probe.interface, probe.topic) == (FAKE_INTERFACE, "rt/lowstate")
    assert probe.mode_machine == 4 and probe.tick == 499


def test_probe_only_counts_the_window(tmp_path: Path) -> None:
    arm, sub, clk = linked_arm(tmp_path, timeout_s=30.0)
    for _ in range(100):
        clk.advance(0.002)
        sub.deliver(low_state())
    clk.advance(5.0)  # a long silence, inside the timeout
    for _ in range(100):
        clk.advance(0.002)
        sub.deliver(low_state())
    assert arm.probe(window_s=1.0).samples == 100
    assert arm.probe(window_s=10.0).samples == 200
    with pytest.raises(ValueError):
        arm.probe(window_s=0.0)


# --------------------------------------------------------------------------------------------------
# unavailability: every "there is no state" is an ArmUnavailable naming the interface
# --------------------------------------------------------------------------------------------------


def test_an_unconfigured_interface_is_unavailable_not_a_crash(tmp_path: Path) -> None:
    """The state of this laptop today: nothing says which interface holds the G1 LAN (H-002)."""
    data = yaml.safe_load((REPO / "config" / "robot.yaml").read_text(encoding="utf-8"))
    assert data["network"]["dds_interface"] == config.UNMEASURED, "H-002 resolved: update this test"
    (tmp_path / "robot.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ArmUnavailable) as exc:
        G1Arm(config_root=tmp_path)
    assert "dds_interface" in str(exc.value) and "H-002" in str(exc.value)


def test_no_message_within_the_timeout_names_the_interface(tmp_path: Path) -> None:
    arm, _, _ = linked_arm(tmp_path, timeout_s=0.05)
    for call in (arm.read_state, arm.full_state, arm.probe):
        with pytest.raises(ArmUnavailable) as exc:
            call()
        assert FAKE_INTERFACE in str(exc.value) and "rt/lowstate" in str(exc.value)
    assert arm.poll() == []  # poll never raises: an empty list means nothing arrived


def test_a_stream_that_went_silent_is_unavailable(tmp_path: Path) -> None:
    arm, sub, clk = linked_arm(tmp_path, timeout_s=1.0)
    sub.deliver(low_state())
    clk.advance(0.9)
    assert arm.read_state().payload.waist_yaw == pytest.approx(0.12)
    clk.advance(0.2)
    with pytest.raises(ArmUnavailable) as exc:
        arm.read_state()
    assert "went silent" in str(exc.value) and FAKE_INTERFACE in str(exc.value)


def test_a_closed_driver_refuses_to_read(tmp_path: Path) -> None:
    arm, sub, _ = linked_arm(tmp_path)
    sub.deliver(low_state())
    arm.close()
    with pytest.raises(ArmUnavailable):
        arm.read_state()


def test_a_non_positive_timeout_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError):
        linked_arm(tmp_path, timeout_s=0.0)


def test_the_config_carries_the_keys_the_driver_reads() -> None:
    """Section 7: every number the driver uses is in config/robot.yaml, none is a constant in code."""
    control = config.load("robot")["control"]
    assert control["motor_count"] == 29
    assert control["state_timeout_s"] > 0
    assert config.load("robot")["topics"]["state"] == "rt/lowstate"


# --------------------------------------------------------------------------------------------------
# stream_stats.py --stream arm
# --------------------------------------------------------------------------------------------------


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "tools/hardware_checks/stream_stats.py", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def test_drain_collects_arrival_timestamps_and_discards_the_warmup() -> None:
    """`drain` reports when the driver stamped each message, not when the loop asked for it."""

    class Source:
        def __init__(self) -> None:
            self.ts = 0

        def poll(self) -> list[Any]:
            out = [Stamped(self.ts + i, None) for i in range(1, 4)]
            self.ts += 3
            return out

    source = Source()
    got = stream_stats.drain(source, seconds=0.05, poll_s=0.001, warmup=3)
    assert got[0] == 4, got[:5]  # the first three samples were the warmup
    assert got == sorted(got) and len(set(got)) == len(got)


def test_stream_stats_on_the_mock_arm_reports_the_mock_state_rate() -> None:
    """The same statistics path as the cameras, driven by MockArm: no hardware, no DDS."""
    expected = float(config.load("robot")["mock"]["state_hz"])
    done = run_tool("--backend", "mock", "--stream", "arm", "--seconds", "3", "--json")
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["stream"] == "arm"
    assert abs(report["stats"]["fps"] - expected) <= 1.0, report["stats"]
    assert report["stats"]["drops"] == 0
    assert report["stats"]["frames"] > 100
    assert report["stats"]["expected_hz"] == expected


def test_stream_stats_arm_exits_3_while_the_lan_is_down() -> None:
    if config.load("robot")["network"]["dds_interface"] != config.UNMEASURED:
        pytest.skip("the G1 LAN interface is configured (H-002 resolved); this is the LAN-down path")
    done = run_tool("--backend", "real", "--stream", "arm", "--seconds", "1")
    assert done.returncode == stream_stats.NO_STREAM
    assert "dds_interface" in done.stderr


def test_stream_stats_still_accepts_the_camera_spelling() -> None:
    done = run_tool("--backend", "mock", "--camera", "top", "--seconds", "2", "--json")
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["camera"] == "top" and report["stream"] == "top"
    assert run_tool("--stream", "arm", "--camera", "top", "--seconds", "1").returncode == 2
    assert run_tool("--backend", "real", "--stream", "arm", "--device", "/dev/video0").returncode == 2


# --------------------------------------------------------------------------------------------------
# readonly: these need the robot and skip, naming the interface, when it is not there
# --------------------------------------------------------------------------------------------------


@pytest.mark.readonly
def test_real_state_arrives_and_is_shaped_like_a_mock_sample() -> None:
    with real_arm() as arm:
        first = arm.read_state()
        samples = arm.poll()
    mock = make("arm").read_state()
    assert first.payload.arm.shape == mock.payload.arm.shape
    assert first.payload.ts_ns == first.ts_ns > 0
    stamps = [s.ts_ns for s in samples]
    assert stamps == sorted(stamps)


@pytest.mark.readonly
def test_real_probe_reports_a_rate_and_the_robot_mode() -> None:
    with real_arm() as arm:
        probe = arm.probe()
    assert probe.samples > 1 and probe.state_hz > 0
    assert probe.mode_machine >= 0


@pytest.mark.readonly
def test_the_factory_builds_a_real_arm() -> None:
    interface = config.load("robot")["network"]["dds_interface"]
    try:
        arm = make("arm", backend="real")
    except ArmUnavailable as exc:
        pytest.skip(f"no G1 state stream on interface {interface!r}: {exc}")
    try:
        assert isinstance(arm, G1Arm) and isinstance(arm, ArmDriver)
    finally:
        arm.close()
