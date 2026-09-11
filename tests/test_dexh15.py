"""The real DexH15 driver: read-only joints, telemetry and palm camera (T-019; CLAUDE.md 5.3, R1, R2).

The hand is not attached (agents/HARDWARE_NEEDED.md H-003), so the weight here is carried by
``FakeControl`` and ``FakeCamera``: objects with the method names and return shapes of
``pxdex.dh15.DexH15Control`` and ``pxdex.dh15.DexH15Camera`` as the installed stubs declare them
(``.venv/lib/python3.10/site-packages/pxdex/dh15.pyi``). That exercises every call the driver makes
and the whole unpacking path with nothing plugged in.

The tests that need the actual hand are marked ``readonly`` and skip with a reason naming the serial
device, the same discipline as ``tests/test_cameras.py`` and ``tests/test_g1_arm.py``. Nothing in
this file can move anything: the driver never writes to the hand, and a test asserts it by grepping
the module for the SDK's writing verbs.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from drivers import make
from drivers.cameras import CameraUnavailable
from drivers.dexh15 import (
    DexH15,
    HandProbe,
    HandTelemetry,
    HandUnavailable,
    PalmCamera,
    find_port,
    resolve_port,
)
from drivers.interfaces import CameraDriver, HandDriver, HandState
from runtime import config
from tools.hardware_checks import stream_stats

REPO = Path(__file__).resolve().parents[1]

#: A serial node that certainly exists and is certainly not a hand. The fake control never opens it;
#: the name only has to be a real path, because `resolve_port` checks that an explicit port exists.
FAKE_PORT = "/dev/null"

#: A video node that certainly exists, for the palm camera's device discovery. Same reasoning.
FAKE_VIDEO = "/dev/null"

#: The writing verbs of the DexH15 SDK. None of them may appear outside `send_pinch` (R1, R2).
WRITE_VERBS = re.compile(r"enableMotor|setMotor|setJoint")


class FakeClock:
    """The injectable monotonic clock of docs/drivers.md: nanoseconds, advanced by hand."""

    def __init__(self) -> None:
        self.ns = 0

    def __call__(self) -> int:
        self.ns += 1_000_000  # every acquisition costs a millisecond of fake time
        return self.ns


class ForcePoint:
    def __init__(self, x: int, y: int, z: int) -> None:
        self.x, self.y, self.z = x, y, z


class MotorPosition:
    """``Dex15MotorPosition``: seven integer fields named ``motor1_pos`` .. ``motor7_pos``."""

    def __init__(self, counts: list[int]) -> None:
        for i, value in enumerate(counts):
            setattr(self, f"motor{i + 1}_pos", value)


class Versions:
    mainboard_hardware_version = "HW1.2"
    unified_version = "FW3.2.1"


class FakeControl:
    """``pxdex.dh15.DexH15Control``'s read half, with the stubs' exact return shapes."""

    def __init__(self, *, joints: int = 15, motors: int = 7, open_ok: bool = True, init: int = 1) -> None:
        self.joints, self.motors, self._open_ok, self._init = joints, motors, open_ok, init
        self.opened: tuple[str, int] | None = None
        self.calls: list[str] = []
        self.disconnects = 0
        self.norm = [i / 100.0 for i in range(joints)]

    def openModbusDevice(self, port: str, baud: int) -> bool:  # noqa: N802 - the SDK's spelling
        self.opened = (port, baud)
        self.calls.append("openModbusDevice")
        return self._open_ok

    def initModbusDevice(self, slave: int) -> int:  # noqa: N802
        self.calls.append("initModbusDevice")
        self.slave = slave
        return self._init

    def isModbusDeviceConnected(self, slave: int) -> bool:  # noqa: N802
        self.calls.append("isModbusDeviceConnected")
        return True

    def getJointPositionsAngle(self, slave: int) -> tuple[int, list[float]]:  # noqa: N802
        self.calls.append("getJointPositionsAngle")
        return 1, list(self.norm)

    def calculateRealAngle(self, slave: int, normalized: list[float]) -> tuple[int, list[float]]:  # noqa: N802
        self.calls.append("calculateRealAngle")
        return 1, [2.0 * v for v in normalized]

    def getMotorPosition(self, slave: int) -> MotorPosition:  # noqa: N802
        self.calls.append("getMotorPosition")
        return MotorPosition([1000 * (i + 1) for i in range(self.motors)])

    def getFingerResultantForce(self, slave: int) -> list[ForcePoint]:  # noqa: N802
        self.calls.append("getFingerResultantForce")
        return [ForcePoint(i, 2 * i, 3 * i) for i in range(5)]

    def getDeviceInfo(self, slave: int) -> tuple[bool, str, Versions, Versions, Any]:  # noqa: N802
        self.calls.append("getDeviceInfo")
        return True, "SN-0001", Versions(), Versions(), None

    def getSDKVersion(self) -> str:  # noqa: N802
        return "DexHandSDK_3.2.1"

    def disconnectModbus(self) -> bool:  # noqa: N802
        self.disconnects += 1
        return True


class FakeCamera:
    """``pxdex.dh15.DexH15Camera``: connect, configure, getFrame, release."""

    def __init__(self, *, size: tuple[int, int] = (640, 480), configure_ok: bool = True) -> None:
        self.size, self._configure_ok = size, configure_ok
        self.connected: str | None = None
        self.config: tuple[int, int, float] | None = None
        self.releases = 0
        self.frames = 0

    def connectCameraDevice(self, port: str) -> None:  # noqa: N802
        self.connected = port

    def setCameraConfig(self, width: int, height: int, fps: float) -> bool:  # noqa: N802
        self.config = (width, height, fps)
        return self._configure_ok

    def getFrame(self) -> np.ndarray:  # noqa: N802
        self.frames += 1
        width, height = self.size
        return np.full((height, width, 3), self.frames % 256, dtype=np.uint8)

    def releaseCameraDevice(self) -> None:  # noqa: N802
        self.releases += 1


def hand_root(tmp_path: Path, **overrides: Any) -> Path:
    """A config directory whose ``hand.yaml`` has the given top-level subtrees updated."""
    data = yaml.safe_load((REPO / "config" / "hand.yaml").read_text(encoding="utf-8"))
    data["device"]["port"] = FAKE_PORT
    for key, values in overrides.items():
        data[key].update(values)
    (tmp_path / "hand.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    for name in ("cameras",):  # the palm camera reads this one
        (tmp_path / f"{name}.yaml").write_text(
            (REPO / "config" / f"{name}.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
    return tmp_path


def linked_hand(tmp_path: Path, control: FakeControl | None = None, **kwargs: Any) -> tuple[DexH15, FakeControl]:
    """A driver wired to a fake control on a fake clock. Nothing real is opened."""
    fake = control or FakeControl()
    root = kwargs.pop("config_root", None) or hand_root(tmp_path)
    hand = DexH15(control_factory=lambda: fake, now_ns=FakeClock(), config_root=root, **kwargs)
    return hand, fake


def palm_root(tmp_path: Path) -> Path:
    """A config directory whose ``cameras.yaml`` points ``palm`` at a node that exists."""
    data = yaml.safe_load((REPO / "config" / "cameras.yaml").read_text(encoding="utf-8"))
    data["palm"]["device"] = FAKE_VIDEO
    (tmp_path / "cameras.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path


def real_hand() -> DexH15:
    """Open the real hand or skip, naming the serial device. Used by every ``readonly`` test."""
    try:
        return DexH15()
    except HandUnavailable as exc:
        pytest.skip(f"no DexH15 on the Modbus bus: {exc}")


# --------------------------------------------------------------------------------------------------
# R1/R2: this driver cannot move anything
# --------------------------------------------------------------------------------------------------


def test_the_module_names_a_writing_verb_only_inside_the_send_pinch_stub() -> None:
    """T-019 writes nothing: enableMotor / setMotor* / setJoint* appear only in the refusal (R1, R2)."""
    source = (REPO / "drivers" / "dexh15.py").read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(source) if line.strip().startswith("def send_pinch")]
    assert len(starts) == 1, "there is exactly one send_pinch in this module"
    start = starts[0]
    ends = [i for i in range(start + 1, len(source)) if source[i].startswith("    def ")]
    end = ends[0] if ends else len(source)
    hits = [i for i, line in enumerate(source) if WRITE_VERBS.search(line)]
    assert hits, "the stub names what it refuses to call"
    for i in hits:
        assert start < i < end, f"drivers/dexh15.py:{i + 1} names a writing verb outside send_pinch: {source[i]!r}"
    # initMotorPosition rewrites the hand's zero and is part of the T-022 bring-up, not of this task.
    assert "initMotorPosition" not in "\n".join(source)


def test_send_pinch_refuses_and_names_the_task_that_will_implement_it(tmp_path: Path) -> None:
    hand, fake = linked_hand(tmp_path)
    with pytest.raises(NotImplementedError) as exc:
        hand.send_pinch(0.5)
    assert "T-022" in str(exc.value)
    assert not any(WRITE_VERBS.search(call) for call in fake.calls)


def test_the_driver_satisfies_the_hand_driver_protocol() -> None:
    """Same protocol as MockHand, so nothing downstream of a driver can tell them apart."""
    assert isinstance(DexH15.__new__(DexH15), HandDriver)
    assert isinstance(PalmCamera.__new__(PalmCamera), CameraDriver)


def test_the_driver_builds_no_guard_and_holds_no_session(tmp_path: Path) -> None:
    """With no writer there is no motion path to guard; R3 applies at T-022."""
    from drivers import dexh15

    hand, _ = linked_hand(tmp_path)
    assert not hasattr(hand, "guard")
    assert not hasattr(dexh15, "Guard") and not hasattr(dexh15, "safety")


def test_importing_the_module_imports_no_sdk_and_opens_nothing() -> None:
    """The SDK is imported inside default_control/default_camera, never at module import."""
    probe = "import sys, drivers.dexh15 as m; print('pxdex' in sys.modules, 'cv2' in sys.modules)"
    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO, capture_output=True, text=True, check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "False False", done.stdout


# --------------------------------------------------------------------------------------------------
# opening the bus
# --------------------------------------------------------------------------------------------------


def test_it_opens_the_configured_port_baud_and_slave(tmp_path: Path) -> None:
    hand, fake = linked_hand(tmp_path)
    cfg = config.load("hand")["device"]
    assert fake.opened == (FAKE_PORT, cfg["baud"])
    assert fake.slave == cfg["slave_address"] == 0x78
    assert fake.calls[:2] == ["openModbusDevice", "initModbusDevice"]
    assert hand.port == FAKE_PORT
    hand.close()
    hand.close()  # idempotent
    assert fake.disconnects == 1


def test_a_port_that_will_not_open_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(HandUnavailable) as exc:
        linked_hand(tmp_path, FakeControl(open_ok=False))
    assert FAKE_PORT in str(exc.value) and "H-003" in str(exc.value)


def test_a_slave_that_does_not_answer_is_unavailable(tmp_path: Path) -> None:
    fake = FakeControl(init=0)
    with pytest.raises(HandUnavailable) as exc:
        linked_hand(tmp_path, fake)
    assert "0x78" in str(exc.value) and "slave_address" in str(exc.value)
    assert fake.disconnects == 1  # the link opened, so it is dropped again


def test_an_unconfigured_port_is_unavailable_not_a_crash() -> None:
    """The state of this laptop today: nothing says which serial node the hand is on (H-003)."""
    device = config.load("hand")["device"]
    assert device["port"] == config.UNMEASURED, "H-003 resolved: update this test"
    with pytest.raises(HandUnavailable) as exc:
        resolve_port()
    message = str(exc.value)
    assert "device.port" in message and "/dev/ttyUSB*" in message and "H-003" in message


def test_an_explicit_port_that_does_not_exist_is_unavailable() -> None:
    with pytest.raises(HandUnavailable) as exc:
        resolve_port("/dev/ttyUSB-nope")
    assert "/dev/ttyUSB-nope" in str(exc.value)


def test_a_configured_port_that_is_not_there_is_unavailable(tmp_path: Path) -> None:
    root = hand_root(tmp_path, device={"port": "/dev/ttyUSB-nope"})
    with pytest.raises(HandUnavailable) as exc:
        resolve_port(root=root)
    assert "device.port" in str(exc.value) and "/dev/ttyUSB-nope" in str(exc.value)


def test_port_discovery_by_usb_id_finds_nothing_with_no_adapter_plugged_in() -> None:
    """The configured usb id is the hand's (docs/sdks.md 4.2); no node carries it today."""
    assert config.load("hand")["device"]["usb_id"] == "067b:23a3"
    assert find_port("067b:23a3") is None or Path(str(find_port("067b:23a3"))).exists()


def test_a_closed_driver_refuses_to_read(tmp_path: Path) -> None:
    hand, _ = linked_hand(tmp_path)
    hand.close()
    for call in (hand.read_state, hand.full_state, hand.probe):
        with pytest.raises(HandUnavailable) as exc:
            call()
        assert "closed" in str(exc.value)


def test_anything_the_sdk_throws_becomes_hand_unavailable(tmp_path: Path) -> None:
    hand, fake = linked_hand(tmp_path)

    def boom(slave: int) -> tuple[int, list[float]]:
        raise RuntimeError("Interrupted system call")

    fake.getJointPositionsAngle = boom  # type: ignore[method-assign]
    with pytest.raises(HandUnavailable) as exc:
        hand.read_state()
    assert "getJointPositionsAngle" in str(exc.value) and "Interrupted" in str(exc.value)


# --------------------------------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------------------------------


def test_read_state_is_one_round_trip_of_joints_in_real_radians(tmp_path: Path) -> None:
    hand, fake = linked_hand(tmp_path)
    fake.calls.clear()
    stamped = hand.read_state()
    assert fake.calls == ["getJointPositionsAngle", "calculateRealAngle"]
    state = stamped.payload
    assert isinstance(state, HandState)
    assert state.joints_rad.shape == (len(config.load("hand")["joint_order"]),) == (15,)
    # FakeControl's conversion is x2, and the driver must hand out what the SDK converted.
    assert state.joints_rad.tolist() == pytest.approx([2 * i / 100.0 for i in range(15)])
    assert not state.joints_rad.flags.writeable
    assert stamped.ts_ns > 0


def test_the_pinch_scalar_is_nan_while_the_synergy_poses_are_unmeasured(tmp_path: Path) -> None:
    """A wrong 0.0 would read as "the hand is open"; nan cannot be mistaken for a measurement."""
    assert config.load("hand")["pinch"]["open_pose"] == config.UNMEASURED, "T-020 done: update this test"
    hand, _ = linked_hand(tmp_path)
    assert not hand.pinch_measurable
    assert np.isnan(hand.read_state().payload.pinch)


def test_the_pinch_scalar_is_the_projection_onto_the_synergy_once_the_poses_exist(tmp_path: Path) -> None:
    """What T-020 will fill in: the driver inverts `q = open + s * (closed - open)` (CLAUDE.md 5.4)."""
    open_pose = [0.0] * 15
    closed_pose = [1.0] * 15
    root = hand_root(tmp_path, pinch={"open_pose": open_pose, "closed_pose": closed_pose})
    fake = FakeControl()
    fake.norm = [0.15] * 15  # the fake converts x2, so the real pose is 0.3 of the way closed
    hand, _ = linked_hand(tmp_path, fake, config_root=root)
    assert hand.pinch_measurable
    assert hand.read_state().payload.pinch == pytest.approx(0.3)
    # Out of range is clipped to config/hand.yaml pinch.scalar_range, never extrapolated.
    assert hand.estimate_pinch(np.full(15, -5.0)) == 0.0
    assert hand.estimate_pinch(np.full(15, 5.0)) == 1.0


def test_a_joint_count_that_disagrees_with_the_config_is_unavailable(tmp_path: Path) -> None:
    """The joint order is a hypothesis (config/hand.yaml joint_order_status); a live length that
    disagrees with it must stop the driver, not be silently reshaped."""
    hand, fake = linked_hand(tmp_path, FakeControl(joints=12))
    with pytest.raises(HandUnavailable) as exc:
        hand.read_state()
    assert "12 angles" in str(exc.value) and "joint_order" in str(exc.value)


def test_a_short_real_angle_conversion_is_unavailable(tmp_path: Path) -> None:
    hand, fake = linked_hand(tmp_path)
    fake.calculateRealAngle = lambda slave, values: (0, [])  # type: ignore[method-assign]
    with pytest.raises(HandUnavailable) as exc:
        hand.read_state()
    assert "calculateRealAngle" in str(exc.value) and "hardware version" in str(exc.value)


def test_full_state_carries_motors_and_tactile_for_the_dataset(tmp_path: Path) -> None:
    hand, fake = linked_hand(tmp_path)
    fake.calls.clear()
    telemetry = hand.full_state().payload
    assert isinstance(telemetry, HandTelemetry)
    assert fake.calls == [
        "getJointPositionsAngle",
        "getMotorPosition",
        "getFingerResultantForce",
        "calculateRealAngle",
    ]
    assert telemetry.joints_norm.tolist() == pytest.approx([i / 100.0 for i in range(15)])
    assert telemetry.joints_rad.tolist() == pytest.approx([2 * i / 100.0 for i in range(15)])
    assert telemetry.motor_counts.tolist() == [1000 * (i + 1) for i in range(7)]
    assert telemetry.force_xyz.shape == (5, 3)
    assert telemetry.force_xyz[2].tolist() == [2, 4, 6]
    for array in (telemetry.joints_norm, telemetry.joints_rad, telemetry.motor_counts, telemetry.force_xyz):
        assert not array.flags.writeable


def test_probe_reports_what_answered_on_the_bus(tmp_path: Path) -> None:
    hand, _ = linked_hand(tmp_path)
    probe = hand.probe()
    assert isinstance(probe, HandProbe)
    assert (probe.port, probe.baud, probe.slave_address) == (FAKE_PORT, 4000000, 0x78)
    assert probe.connected and probe.joints == 15
    assert (probe.serial_number, probe.hardware_version, probe.firmware_version) == ("SN-0001", "HW1.2", "FW3.2.1")
    assert probe.sdk_version == "DexHandSDK_3.2.1"
    assert probe.pinch_measurable is False


def test_the_config_carries_the_keys_the_driver_reads() -> None:
    """Section 7: every number the driver uses is in config/hand.yaml, none is a constant in code."""
    hand = config.load("hand")
    assert len(hand["joint_order"]) == 15 and len(hand["motor_order"]) == 7
    assert hand["device"]["baud"] == 4000000 and hand["device"]["slave_address"] == 0x78
    assert hand["pinch"]["scalar_range"] == [0.0, 1.0]
    assert hand["joint_order_status"] == config.UNMEASURED


# --------------------------------------------------------------------------------------------------
# the palm camera
# --------------------------------------------------------------------------------------------------


def test_the_palm_camera_configures_the_node_and_emits_policy_frames(tmp_path: Path) -> None:
    spec = config.load("cameras")["palm"]
    fake = FakeCamera(size=tuple(spec["resolution"]))
    camera = PalmCamera(camera_factory=lambda: fake, now_ns=FakeClock(), config_root=palm_root(tmp_path))
    assert fake.connected == FAKE_VIDEO
    assert fake.config == (spec["resolution"][0], spec["resolution"][1], float(spec["fps"]))
    frame = camera.grab()
    width, height = spec["policy_resolution"]
    assert frame.payload.shape == (height, width, 3) == (240, 320, 3)
    assert frame.payload.dtype == np.uint8 and frame.ts_ns > 0
    camera.close()
    camera.close()  # idempotent
    assert fake.releases == 1


def test_the_palm_camera_is_opened_lazily_on_the_first_frame(tmp_path: Path) -> None:
    """The hand's Modbus link and its camera are separate devices; opening one is not opening both."""
    built: list[FakeCamera] = []

    def factory() -> FakeCamera:
        built.append(FakeCamera())
        return built[-1]

    root = hand_root(tmp_path)  # writes hand.yaml and a copy of cameras.yaml ...
    palm_root(tmp_path)  # ... which this one then points at a node that exists
    hand, _ = linked_hand(tmp_path, camera_factory=factory, config_root=root)
    assert built == []
    hand.palm_frame()
    assert len(built) == 1
    hand.palm_frame()
    assert len(built) == 1  # opened once, not per frame
    hand.close()
    assert built[0].releases == 1


def test_an_unconfigured_palm_camera_is_unavailable_naming_the_config_key() -> None:
    """The state of this laptop today: palm.device is UNMEASURED and nothing is plugged in (H-003)."""
    assert config.load("cameras")["palm"]["device"] == config.UNMEASURED, "H-003 resolved: update this test"
    with pytest.raises(CameraUnavailable) as exc:
        PalmCamera(camera_factory=FakeCamera)
    assert "palm.device" in str(exc.value)


def test_a_camera_that_refuses_the_configured_format_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(CameraUnavailable) as exc:
        PalmCamera(camera_factory=lambda: FakeCamera(configure_ok=False), config_root=palm_root(tmp_path))
    assert "palm.resolution" in str(exc.value)


def test_a_camera_that_gives_a_bad_frame_is_unavailable(tmp_path: Path) -> None:
    fake = FakeCamera()
    fake.getFrame = lambda: np.zeros((4, 4), dtype=np.uint8)  # type: ignore[method-assign]
    camera = PalmCamera(camera_factory=lambda: fake, config_root=palm_root(tmp_path))
    with pytest.raises(CameraUnavailable) as exc:
        camera.grab()
    assert "want (h, w, 3) uint8" in str(exc.value)
    camera.close()
    with pytest.raises(CameraUnavailable):
        camera.grab()


# --------------------------------------------------------------------------------------------------
# the factory
# --------------------------------------------------------------------------------------------------


def test_the_factory_builds_a_real_hand_and_a_real_palm_camera() -> None:
    """Both are absent today, so both report their own unavailability rather than crashing."""
    with pytest.raises(HandUnavailable):
        make("hand", backend="real")
    with pytest.raises(CameraUnavailable):
        make("palm", backend="real")


# --------------------------------------------------------------------------------------------------
# stream_stats.py --stream hand
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


def test_stream_stats_on_the_mock_hand_reports_the_command_rate() -> None:
    """The same statistics path as the cameras, driven by MockHand: no hardware, no Modbus."""
    expected = float(config.load("hand")["device"]["command_hz"])
    done = run_tool("--backend", "mock", "--stream", "hand", "--seconds", "3", "--json")
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["stream"] == "hand" and "camera" not in report
    assert report["policy_resolution"] is None
    assert abs(report["stats"]["fps"] - expected) <= 1.0, report["stats"]
    assert report["stats"]["drops"] == 0
    assert report["stats"]["expected_hz"] == expected


def test_stream_stats_hand_exits_3_while_the_hand_is_absent() -> None:
    if config.load("hand")["device"]["port"] != config.UNMEASURED:
        pytest.skip("the hand's serial port is configured (H-003 resolved); this is the absent path")
    done = run_tool("--backend", "real", "--stream", "hand", "--seconds", "1")
    assert done.returncode == stream_stats.NO_STREAM
    assert "device.port" in done.stderr and "H-003" in done.stderr


def test_stream_stats_palm_exits_3_through_the_hands_camera() -> None:
    done = run_tool("--backend", "real", "--camera", "palm", "--seconds", "1")
    assert done.returncode == stream_stats.NO_STREAM
    assert "palm.device" in done.stderr


def test_stream_stats_rejects_a_device_override_for_the_hand() -> None:
    assert run_tool("--backend", "real", "--stream", "hand", "--device", "/dev/ttyUSB0").returncode == 2


def test_stream_takes_read_state_when_there_is_no_grab() -> None:
    """`stream` drives a camera through grab() and the hand through read_state(); one path, two names."""

    class Source:
        def __init__(self) -> None:
            self.clock = FakeClock()

        def read_state(self) -> Any:
            from runtime.clock import Stamped

            return Stamped(self.clock(), None)

    got = stream_stats.stream(Source(), seconds=0.05)
    assert len(got) > 2 and got == sorted(got) and len(set(got)) == len(got)


# --------------------------------------------------------------------------------------------------
# readonly: these need the hand and skip, naming the device, when it is not there
# --------------------------------------------------------------------------------------------------


@pytest.mark.readonly
def test_real_joint_angles_arrive_and_are_shaped_like_a_mock_sample() -> None:
    with real_hand() as hand:
        first = hand.read_state()
        telemetry = hand.full_state()
    mock = make("hand").read_state()
    assert first.payload.joints_rad.shape == mock.payload.joints_rad.shape
    assert telemetry.payload.motor_counts.shape == (7,)
    assert first.ts_ns > 0


@pytest.mark.readonly
def test_real_probe_reports_the_hand_that_answered() -> None:
    with real_hand() as hand:
        probe = hand.probe()
    assert probe.connected and probe.joints > 0
    assert probe.sdk_version


@pytest.mark.readonly
def test_a_real_palm_frame_arrives_at_the_policy_resolution() -> None:
    expected = tuple(config.load("cameras")["palm"]["policy_resolution"])
    try:
        camera = PalmCamera()
    except CameraUnavailable as exc:
        pytest.skip(f"no real palm camera: {exc}")
    try:
        frame = camera.grab().payload
    finally:
        camera.close()
    assert (frame.shape[1], frame.shape[0]) == expected
    assert frame.dtype == np.uint8
