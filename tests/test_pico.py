"""The real Pico controller-pose driver: read-only PicoBridge frames (T-020, D-006, R1, R2).

The headset is not on the network (agents/HARDWARE_NEEDED.md H-004), so the weight here is carried
by ``FakeBridge``, which delivers **real** ``pico_bridge`` objects -- ``PicoFrame``,
``ControllersFrame``, ``ControllerState``, ``Pose`` -- built by :func:`frame`. ``pico_bridge`` 0.2.1
is an installed dependency (D-006), so nothing about the frame's shape is invented here: only the
transport is faked.

The tests that need the actual headset are marked ``readonly`` and skip with a reason naming the
socket, the same discipline as ``tests/test_dexh15.py``. Nothing in this file can move anything: the
controller is an input device and this driver creates no writer of any kind.
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
from pico_bridge.bridge import PicoBridgeStats
from pico_bridge.frames import BodyFrame, ControllersFrame, ControllerState, HandFrame, PicoFrame, Pose

from drivers import make
from drivers.interfaces import PoseDriver, WristPose
from drivers.pico import Pico, PoseProbe, PoseUnavailable
from runtime import config
from tools.hardware_checks import stream_stats

REPO = Path(__file__).resolve().parents[1]


class Clock:
    """The injectable monotonic clock of docs/drivers.md: nanoseconds, advanced by hand."""

    def __init__(self, ns: int = 1_000_000) -> None:
        self.ns = ns

    def advance(self, seconds: float) -> None:
        self.ns += int(seconds * 1e9)

    def __call__(self) -> int:
        return self.ns


def _empty_hand() -> HandFrame:
    return HandFrame(active=False, joints=np.zeros((26, 7)), radii=np.zeros(26), status=np.zeros(26, dtype=np.uint64))


def frame(
    seq: int, position: Any = (0.1, 0.2, 0.3), quat: Any = (0.0, 0.0, 0.0, 1.0), *, left: bool = True
) -> PicoFrame:
    """One real ``PicoFrame`` carrying a left-controller pose (or, with ``left=False``, none)."""
    pose = Pose(position=np.asarray(position, dtype=np.float64), rotation=np.asarray(quat, dtype=np.float64))
    controller = ControllerState(pose=pose if left else None, axis={}, buttons={}, raw={})
    return PicoFrame(
        seq=seq,
        timestamp_ns=1_000 * seq,
        receive_time_s=float(seq),
        coordinate_space="pico_native",
        quat_order="xyzw",
        units="meters",
        head=None,
        body=BodyFrame(active=False, joints=np.zeros((24, 7))),
        left_hand=_empty_hand(),
        right_hand=_empty_hand(),
        controllers=ControllersFrame(left=controller, right=controller),
        raw={},
    )


class FakeBridge:
    """The read half of ``pico_bridge.PicoBridge``: latest-wins frames and a stats snapshot."""

    def __init__(self, *, start_error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.latest: PicoFrame | None = None
        self.start_error = start_error
        self.started = self.closed = False
        self.bind: tuple[str, int, bool, str | None] | None = None

    def start(self) -> None:
        self.calls.append("start")
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def close(self) -> None:
        self.calls.append("close")
        self.closed = True

    def latest_frame(self) -> PicoFrame | None:
        return self.latest

    def wait_frame(self, timeout: float | None = None, *, after_seq: int | None = None) -> PicoFrame:
        if self.latest is None:
            raise TimeoutError("no PICO tracking frame received before timeout")
        return self.latest

    def stats(self) -> PicoBridgeStats:
        return PicoBridgeStats(
            connected=self.latest is not None,
            device_sn="PICO-TEST-0001",
            frame_count=0 if self.latest is None else self.latest.seq,
            latest_seq=0 if self.latest is None else self.latest.seq,
            fps=119.5,
            latest_frame_age_s=0.0,
            latest_latency_s=0.0,
            dropped_ring_frames=0,
            video_enabled=False,
            video_running=False,
            video_source=None,
        )


def linked_pico(clock: Clock | None = None, **kwargs: Any) -> tuple[Pico, FakeBridge, Clock]:
    """A driver on a started fake bridge, on the repo's own config."""
    fake = FakeBridge(**{k: kwargs.pop(k) for k in ("start_error",) if k in kwargs})

    def factory(bind_host: str, port: int, discovery: bool, advertise_ip: str | None) -> FakeBridge:
        fake.bind = (bind_host, port, discovery, advertise_ip)
        return fake

    tick = Clock() if clock is None else clock
    return Pico(bridge_factory=factory, now_ns=tick, **kwargs), fake, tick


def real_pico() -> Pico:
    """Open the real receiver or skip, naming the socket. Used by every ``readonly`` test."""
    try:
        pico = Pico()
    except PoseUnavailable as exc:
        pytest.skip(f"no PicoBridge receiver: {exc}")
    try:
        pico.read()
    except PoseUnavailable as exc:
        pico.close()
        pytest.skip(f"no PICO controller pose: {exc}")
    return pico


# --------------------------------------------------------------------------------------------------
# R1/R2: this driver cannot move anything
# --------------------------------------------------------------------------------------------------


def test_the_driver_satisfies_the_pose_driver_protocol() -> None:
    """Same protocol as MockPose, so nothing downstream of a driver can tell them apart."""
    assert isinstance(Pico.__new__(Pico), PoseDriver)
    assert not hasattr(Pico, "send_targets") and not hasattr(Pico, "send_pinch")


def test_the_driver_builds_no_guard_and_holds_no_session() -> None:
    """An input device has no motion path to guard; R3 has nothing to say here."""
    from drivers import pico

    driver, _fake, _clock = linked_pico()
    assert not hasattr(driver, "guard")
    assert not hasattr(pico, "Guard") and not hasattr(pico, "safety")


def test_importing_the_module_starts_no_receiver_and_binds_no_socket() -> None:
    """pico_bridge is imported inside default_bridge, never at module import."""
    probe = "import sys, drivers.pico as m; print('pico_bridge' in sys.modules)"
    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO, capture_output=True, text=True, check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "False", done.stdout


# --------------------------------------------------------------------------------------------------
# binding the receiver
# --------------------------------------------------------------------------------------------------


def test_it_binds_what_the_config_says_and_starts_the_receiver() -> None:
    pico = config.load("robot")["teleop"]["pico"]
    driver, fake, _clock = linked_pico()
    assert fake.bind == (pico["bind_host"], pico["port"], pico["discovery"], None)
    assert fake.started
    assert driver.expected_hz == float(pico["input_hz"])


def test_an_unmeasured_advertise_ip_means_let_pico_bridge_choose() -> None:
    """`advertise_ip: UNMEASURED` must not reach pico_bridge as the literal string."""
    assert config.load("robot")["teleop"]["pico"]["advertise_ip"] == config.UNMEASURED
    _driver, fake, _clock = linked_pico()
    assert fake.bind is not None and fake.bind[3] is None


def test_a_measured_advertise_ip_is_passed_through(tmp_path: Path) -> None:
    data = yaml.safe_load((REPO / "config" / "robot.yaml").read_text(encoding="utf-8"))
    data["teleop"]["pico"]["advertise_ip"] = "192.168.123.2"
    (tmp_path / "robot.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    _driver, fake, _clock = linked_pico(config_root=tmp_path)
    assert fake.bind is not None and fake.bind[3] == "192.168.123.2"


def test_a_receiver_that_cannot_bind_names_the_socket_and_the_hardware_item() -> None:
    with pytest.raises(PoseUnavailable) as exc:
        linked_pico(start_error=OSError("address already in use"))
    assert "63901" in str(exc.value) and "H-004" in str(exc.value)


def test_an_unknown_side_is_a_usage_error() -> None:
    with pytest.raises(ValueError):
        linked_pico(side="third")


# --------------------------------------------------------------------------------------------------
# the stream
# --------------------------------------------------------------------------------------------------


def test_a_frame_becomes_a_wrist_pose_in_pico_bridge_units_and_order() -> None:
    """Metres and xyzw, carried through unchanged: the driver invents no convention (docs/sdks.md 7.1)."""
    driver, fake, clock = linked_pico()
    fake.latest = frame(1, position=(0.1, 0.2, 0.3), quat=(0.0, 0.0, 0.7071, 0.7071))
    sample = driver.read()
    assert sample.ts_ns == clock.ns
    assert isinstance(sample.payload, WristPose)
    assert sample.payload.position_m == pytest.approx([0.1, 0.2, 0.3])
    assert sample.payload.quat_xyzw == pytest.approx([0.0, 0.0, 0.7071, 0.7071])


def test_a_frame_is_stamped_once_per_seq_so_a_re_read_is_not_a_new_sample() -> None:
    """What makes stream_stats measure the headset's rate and not this loop's."""
    driver, fake, clock = linked_pico()
    fake.latest = frame(1)
    first = driver.read()
    clock.advance(0.001)
    assert driver.read() is first
    fake.latest = frame(2, position=(0.4, 0.5, 0.6))
    second = driver.read()
    assert second.ts_ns == clock.ns and second.ts_ns > first.ts_ns
    assert second.payload.position_m == pytest.approx([0.4, 0.5, 0.6])


def test_the_pose_is_not_transformed_here_but_the_pelvis_frame_is_one_call_away() -> None:
    """teleop/loop.py applies pico_to_g1_base itself; doing it here as well would double it (D-006)."""
    from teleop.retarget import pico_to_g1_base

    driver, fake, _clock = linked_pico()
    fake.latest = frame(1, position=(0.1, 0.2, 0.3))
    raw = driver.read().payload
    pelvis = driver.read_in_pelvis_frame().payload
    expected, _quat = pico_to_g1_base(raw.position_m, raw.quat_xyzw)
    assert pelvis.position_m == pytest.approx(expected)


def test_a_frame_without_a_controller_pose_says_which_controller() -> None:
    driver, fake, _clock = linked_pico()
    fake.latest = frame(1, left=False)
    with pytest.raises(PoseUnavailable) as exc:
        driver.read()
    assert "left controller" in str(exc.value)


def test_no_frame_at_all_times_out_naming_the_socket() -> None:
    driver, _fake, _clock = linked_pico(timeout_s=0.01)
    with pytest.raises(PoseUnavailable) as exc:
        driver.read()
    assert "63901" in str(exc.value) and "H-004" in str(exc.value)


def test_a_stream_that_goes_silent_is_refused_rather_than_served_stale() -> None:
    clock = Clock()
    driver, fake, _clock = linked_pico(clock, timeout_s=0.5)
    fake.latest = frame(1)
    driver.read()
    clock.advance(2.0)
    with pytest.raises(PoseUnavailable) as exc:
        driver.read()
    assert "went silent" in str(exc.value) and "frame_timeout_s" in str(exc.value)


def test_probe_reports_the_measured_rate_and_what_the_receiver_says() -> None:
    clock = Clock()
    driver, fake, _clock = linked_pico(clock)
    for seq in range(1, 12):
        clock.advance(1 / 120.0)  # 120 Hz
        fake.latest = frame(seq)
        driver.read()
    probe = driver.probe(window_s=1.0)
    assert isinstance(probe, PoseProbe)
    assert probe.samples == 11 and probe.pose_hz == pytest.approx(120.0, abs=0.5)
    assert probe.expected_hz == 120.0 and probe.port == 63901
    assert probe.connected and probe.device_sn == "PICO-TEST-0001"
    assert probe.latest_seq == 11 and probe.headset_ts_ns == 11_000


def test_close_stops_the_receiver_and_is_idempotent() -> None:
    driver, fake, _clock = linked_pico()
    driver.close()
    driver.close()
    assert fake.calls.count("close") == 1 and fake.closed
    with pytest.raises(PoseUnavailable) as exc:
        driver.read()
    assert "closed" in str(exc.value)


def test_the_context_manager_closes() -> None:
    fake = FakeBridge()
    with Pico(bridge_factory=lambda *a: fake, now_ns=Clock()) as driver:
        assert driver.port == 63901
    assert fake.closed


def test_a_sample_is_shaped_like_the_mock_so_a_consumer_cannot_tell_them_apart() -> None:
    driver, fake, _clock = linked_pico()
    fake.latest = frame(1)
    real = driver.read().payload
    mock = make("pose").read().payload
    assert real.position_m.shape == mock.position_m.shape == (3,)
    assert real.quat_xyzw.shape == mock.quat_xyzw.shape == (4,)


# --------------------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------------------


def test_the_config_carries_the_keys_the_driver_reads() -> None:
    """Section 7: every number the driver uses is in config/robot.yaml, none is a constant in code."""
    pico = config.load("robot")["teleop"]["pico"]
    assert pico["port"] == 63901 and pico["bind_host"] == "0.0.0.0"
    assert pico["input_hz"] > 0 and pico["frame_timeout_s"] > 0
    assert "teleop.pico.advertise_ip" in config.unmeasured("robot")
    assert "teleop.pico.input_hz" in config.unmeasured("robot")


# --------------------------------------------------------------------------------------------------
# stream_stats.py --stream pose
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


def test_stream_stats_on_the_mock_pose_reports_the_mock_rate() -> None:
    expected = float(config.load("robot")["mock"]["pose_hz"])
    done = run_tool("--backend", "mock", "--stream", "pose", "--seconds", "3", "--json")
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["stream"] == "pose" and report["policy_resolution"] is None
    assert abs(report["stats"]["fps"] - expected) <= 2.0, report["stats"]
    assert report["stats"]["drops"] == 0 and report["stats"]["frames"] > 100
    assert report["stats"]["expected_hz"] == expected


def test_stream_stats_pose_exits_3_while_the_headset_is_not_streaming() -> None:
    done = run_tool("--backend", "real", "--stream", "pose", "--seconds", "1")
    assert done.returncode == stream_stats.NO_STREAM
    assert "PICO" in done.stderr or "PicoBridge" in done.stderr


def test_stream_stats_refuses_a_device_override_for_the_pose() -> None:
    assert run_tool("--backend", "real", "--stream", "pose", "--device", "/dev/video0").returncode == 2


# --------------------------------------------------------------------------------------------------
# readonly: these need the headset and skip, naming the socket, when it is not streaming
# --------------------------------------------------------------------------------------------------


@pytest.mark.readonly
def test_a_real_controller_pose_arrives_and_is_shaped_like_a_mock_sample() -> None:
    with real_pico() as driver:
        sample = driver.read()
    mock = make("pose").read()
    assert sample.payload.position_m.shape == mock.payload.position_m.shape
    assert sample.payload.quat_xyzw.shape == mock.payload.quat_xyzw.shape
    assert float(np.linalg.norm(sample.payload.quat_xyzw)) == pytest.approx(1.0, abs=1e-3)


@pytest.mark.readonly
def test_real_probe_reports_the_headset_that_connected() -> None:
    with real_pico() as driver:
        probe = driver.probe(window_s=1.0)
    assert probe.connected and probe.frames > 0
    assert probe.pose_hz > 0
