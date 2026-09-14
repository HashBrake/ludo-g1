"""Real camera driver and the read-only stream check (T-010; CLAUDE.md 5.1, 5.3, R1, R2).

Nothing here needs a hardware session: a camera is a sensor and reading it is allowed at any time
(R1). Nothing here *fails* when a camera is absent either -- the tests that want a real device are
marked ``readonly`` and skip with a reason naming the device or the config key that would supply
one, so this file is green on a laptop with nothing plugged in and green on the rig.

The hardware-free tests carry the weight: device resolution and discovery are exercised against a
temporary ``config/cameras.yaml`` and a synthetic node list, and the statistics of
``tools/hardware_checks/stream_stats.py`` are exercised against synthetic timestamp trains where
the right answer is known exactly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from drivers import make
from drivers.cameras import (
    CameraUnavailable,
    Selection,
    V4L2Camera,
    VideoNode,
    find_node,
    list_video_nodes,
    resolve_device,
)
from drivers.interfaces import CameraDriver
from drivers.mock import MockCamera
from runtime import clock, config
from tools.hardware_checks import stream_stats

REPO = Path(__file__).resolve().parents[1]

#: The streams T-010 owns. The palm camera belongs to the DexH15 driver in Phase 1 and is not
#: opened here, only used as the "nothing is configured" case.
STREAMS = ("top", "oblique")

PERIOD_NS = 1_000_000_000 // 30


def cameras_root(tmp_path: Path, **overrides: dict) -> Path:
    """A config directory holding ``cameras.yaml`` with the given per-stream keys replaced."""
    data = yaml.safe_load((REPO / "config" / "cameras.yaml").read_text(encoding="utf-8"))
    for stream, keys in overrides.items():
        data[stream].update(keys)
    (tmp_path / "cameras.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path


def open_camera(name: str) -> V4L2Camera:
    """Open a real stream or skip, naming what is missing. Used by every ``readonly`` test."""
    try:
        return V4L2Camera(name)
    except CameraUnavailable as exc:
        pytest.skip(f"no real {name} camera: {exc}")


def node(path: str, *, usb_id: str | None = "2bc5:1201", capture: bool = True, by_id: str | None = None) -> VideoNode:
    return VideoNode(path=path, card=f"card for {path}", usb_id=usb_id, by_id=by_id, is_capture=capture)


# --------------------------------------------------------------------------------------------------
# the contract: the real driver is interchangeable with the mock
# --------------------------------------------------------------------------------------------------


def test_v4l2_camera_satisfies_the_camera_driver_protocol() -> None:
    """Same protocol as MockCamera, so nothing downstream of a driver can tell them apart."""
    bare = V4L2Camera.__new__(V4L2Camera)  # no device needed to check the protocol's shape
    assert isinstance(bare, CameraDriver)
    assert isinstance(MockCamera("top"), CameraDriver)


def test_the_real_driver_has_no_write_call() -> None:
    """A camera is a sensor: there is no path from here to a motion command (R1)."""
    for forbidden in ("send_targets", "send_pinch", "admit"):
        assert not hasattr(V4L2Camera, forbidden)


# --------------------------------------------------------------------------------------------------
# device resolution and discovery
# --------------------------------------------------------------------------------------------------


def test_unconfigured_stream_is_unavailable_not_a_crash(tmp_path: Path) -> None:
    root = cameras_root(tmp_path, top={"device": config.UNMEASURED, "usb_id": config.UNMEASURED})
    with pytest.raises(CameraUnavailable) as exc:
        resolve_device("top", root=root)
    assert "top.device" in str(exc.value) and config.UNMEASURED in str(exc.value)


def test_configured_path_that_is_absent_is_named(tmp_path: Path) -> None:
    missing = str(tmp_path / "video-does-not-exist")
    root = cameras_root(tmp_path, top={"device": missing})
    with pytest.raises(CameraUnavailable) as exc:
        resolve_device("top", root=root)
    assert missing in str(exc.value)


def test_configured_path_wins_over_discovery(tmp_path: Path) -> None:
    """A by-id path in the config is the selector; discovery is only the fallback."""
    stand_in = tmp_path / "usb-fake-video-index0"
    stand_in.write_text("", encoding="utf-8")
    root = cameras_root(tmp_path, oblique={"device": str(stand_in)})
    selection = resolve_device("oblique", root=root)
    assert selection == Selection(device=str(stand_in), source="config/cameras.yaml")


def test_a_numeric_device_is_an_opencv_index(tmp_path: Path) -> None:
    root = cameras_root(tmp_path, top={"device": "2"})
    assert resolve_device("top", root=root).device == 2


def test_an_explicit_device_overrides_the_config(tmp_path: Path) -> None:
    root = cameras_root(tmp_path, top={"device": config.UNMEASURED})
    assert resolve_device("top", override="4", root=root) == Selection(device=4, source="explicit")
    with pytest.raises(CameraUnavailable):
        resolve_device("top", override=str(tmp_path / "nope"), root=root)


def test_discovery_picks_the_lowest_numbered_capture_node_of_the_declared_usb_id(monkeypatch) -> None:
    """The Ego's two interfaces enumerate in order, so the first capture node is the LEFT one."""
    monkeypatch.setattr(
        "drivers.cameras.list_video_nodes",
        lambda: [
            node("/dev/video0", usb_id="174f:11b4"),  # the laptop's own webcam: never matched
            node("/dev/video4", by_id="/dev/v4l/by-id/usb-ORBBEC-video-index0"),  # left
            node("/dev/video5", capture=False),  # metadata node of the same interface
            node("/dev/video6"),  # right
        ],
    )
    found = find_node("2BC5:1201")  # case-insensitive
    assert found is not None and found.path == "/dev/video4"
    assert found.selector == "/dev/v4l/by-id/usb-ORBBEC-video-index0"
    assert find_node("046d:0000") is None


def test_discovery_is_used_when_the_device_is_still_a_placeholder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("drivers.cameras.list_video_nodes", lambda: [node("/dev/video4")])
    root = cameras_root(tmp_path, oblique={"device": config.UNMEASURED})
    selection = resolve_device("oblique", root=root)
    assert selection.device == "/dev/video4" and selection.source == "usb_id 2bc5:1201"


def test_discovery_that_finds_nothing_names_the_usb_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("drivers.cameras.list_video_nodes", lambda: [])
    root = cameras_root(tmp_path, oblique={"device": config.UNMEASURED})
    with pytest.raises(CameraUnavailable) as exc:
        resolve_device("oblique", root=root)
    assert "2bc5:1201" in str(exc.value)


def test_the_measured_oblique_capture_mode_downscales_to_the_policy_frame() -> None:
    """T-046: 1600x1200 captured -> (480, 640, 3) delivered, with no crop and no aspect change.

    Hardware-free: the capture-to-policy conversion is run on a synthetic frame the size the Ego
    actually negotiates, with the real ``config/cameras.yaml`` spec (resolution [1600, 1200],
    policy_resolution [640, 480]). The readonly twin of this,
    ``test_real_frames_arrive_at_the_policy_resolution[oblique]``, needs the camera attached.
    """
    spec = config.load("cameras")["oblique"]
    assert spec["resolution"] == [1600, 1200] and spec["policy_resolution"] == [640, 480]

    camera = V4L2Camera.__new__(V4L2Camera)  # no device: only the frame conversion is under test
    camera.name = "oblique"
    camera.crop = None
    camera.policy_resolution = tuple(spec["policy_resolution"])

    rng = np.random.default_rng(46)
    captured = rng.integers(0, 256, size=(1200, 1600, 3), dtype=np.uint8)
    frame = camera._to_policy(captured)
    assert frame.shape == (480, 640, 3)
    assert frame.dtype == np.uint8
    # 1600x1200 and 640x480 are both 4:3, so the downscale is a clean 2.5x with nothing cut off.
    assert captured.shape[1] / captured.shape[0] == frame.shape[1] / frame.shape[0]
    assert frame.shape == MockCamera("oblique").grab().payload.shape


def test_node_selector_prefers_the_stable_by_id_path() -> None:
    assert node("/dev/video4", by_id="/dev/v4l/by-id/x").selector == "/dev/v4l/by-id/x"
    assert node("/dev/video4").selector == "/dev/video4"


def test_listing_video_nodes_is_read_only_and_works_with_nothing_attached() -> None:
    """VIDIOC_QUERYCAP on an O_RDONLY handle: no streaming, and an empty /dev is not an error."""
    for found in list_video_nodes():
        assert found.path.startswith("/dev/video")
        assert isinstance(found.is_capture, bool)


def test_unknown_camera_name_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        resolve_device("nose", root=cameras_root(tmp_path))


# --------------------------------------------------------------------------------------------------
# depth: D-009 says there is none
# --------------------------------------------------------------------------------------------------


def test_depth_request_names_the_missing_package() -> None:
    with pytest.raises(NotImplementedError) as exc:
        V4L2Camera("oblique", depth=True)
    assert "pyorbbecsdk" in str(exc.value)


def test_the_factory_refuses_no_device_for_want_of_a_driver() -> None:
    # `arm` left this list in T-018, `hand` in T-019, and `glove` and `pose` in T-020: all four have
    # a real, read-only driver now (tests/test_g1_arm.py, test_dexh15.py, test_pxcap.py,
    # test_pico.py). An absent device raises its own Unavailable, never NotImplementedError.
    from drivers.pico import PoseUnavailable
    from drivers.pxcap import GloveUnavailable

    for name in ("glove", "pose"):
        try:
            driver = make(name, backend="real")
        except (GloveUnavailable, PoseUnavailable):
            continue  # not attached; the device's own test file covers that path
        driver.close()


def test_the_factory_reports_an_absent_camera_as_unavailable() -> None:
    """`palm` is unconfigured on purpose: the factory must not crash on it. It is the hand's own
    camera and goes through drivers/dexh15.py since T-019, but raises the same CameraUnavailable."""
    with pytest.raises(CameraUnavailable):
        make("palm", backend="real")


# --------------------------------------------------------------------------------------------------
# stream_stats.py: statistics on synthetic timestamp trains
# --------------------------------------------------------------------------------------------------


def test_stats_of_a_perfect_grid() -> None:
    ts = [k * PERIOD_NS for k in range(301)]
    s = stream_stats.stats(ts, 30.0)
    assert s["frames"] == 301
    assert s["drops"] == 0 and s["frames_missed"] == 0
    assert abs(s["fps"] - 30.0) < 0.01
    assert s["jitter_ms_p99"] < 0.01


def test_stats_counts_a_gap_as_a_drop() -> None:
    """One missing frame is a gap of two periods: one drop, one frame missed."""
    ts = [k * PERIOD_NS for k in range(50)] + [k * PERIOD_NS for k in range(51, 100)]
    s = stream_stats.stats(ts, 30.0)
    assert s["drops"] == 1 and s["frames_missed"] == 1
    assert s["jitter_ms_max"] == pytest.approx(PERIOD_NS / 1e6, abs=0.01)


def test_stats_counts_a_long_gap_as_several_missed_frames() -> None:
    ts = [0, PERIOD_NS, 2 * PERIOD_NS, 7 * PERIOD_NS]
    s = stream_stats.stats(ts, 30.0)
    assert s["drops"] == 1 and s["frames_missed"] == 4


def test_stats_ignores_jitter_below_the_drop_threshold() -> None:
    ts = [0, PERIOD_NS, 2 * PERIOD_NS + PERIOD_NS // 4]
    assert stream_stats.stats(ts, 30.0)["drops"] == 0


def test_frames_lost_counts_loss_and_drops_counts_late_delivery() -> None:
    """The T-047 distinction: a stream can be full of gaps and have lost nothing (D-025).

    Late-and-burst: three frames are delivered in one clump after a three-period stall, so the
    window holds every frame it should (``frames_lost`` 0) while the gap is still a drop.
    """
    late = [k * PERIOD_NS for k in range(97)]
    late += [99 * PERIOD_NS + k * 200_000 for k in range(3)]  # the stall's backlog, in a burst
    late += [k * PERIOD_NS for k in range(100, 300)]
    s = stream_stats.stats(late, 30.0)
    assert s["frames"] == 300
    assert s["drops"] == 1 and s["frames_missed"] == 2
    assert s["frames_lost"] == 0

    # One frame that never came: the same drop count, but a frame short of the window's span.
    lost = [k * PERIOD_NS for k in range(50)] + [k * PERIOD_NS for k in range(51, 300)]
    s = stream_stats.stats(lost, 30.0)
    assert s["drops"] == 1 and s["frames_missed"] == 1
    assert s["frames_lost"] == 1
    assert stream_stats.stats([k * PERIOD_NS for k in range(300)], 30.0)["frames_lost"] == 0


def test_stats_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        stream_stats.stats([0], 30.0)
    with pytest.raises(ValueError):
        stream_stats.stats([0, PERIOD_NS, PERIOD_NS], 30.0)
    with pytest.raises(ValueError):
        stream_stats.stats([0, PERIOD_NS], 0.0)


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "tools/hardware_checks/stream_stats.py", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_stream_stats_on_the_mock_reports_30_hz_and_no_drops() -> None:
    """T-010 acceptance: `--backend mock --seconds 5` is 30 Hz +/- 1 with 0 drops, no hardware."""
    done = run_tool("--backend", "mock", "--camera", "top", "--seconds", "5", "--json")
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert abs(report["stats"]["fps"] - 30.0) <= 1.0, report["stats"]
    assert report["stats"]["drops"] == 0
    assert report["stats"]["frames"] > 100
    assert report["policy_resolution"] == [640, 480]


def test_stream_stats_exits_3_when_there_is_no_camera() -> None:
    done = run_tool("--backend", "real", "--camera", "palm", "--seconds", "1")
    assert done.returncode == stream_stats.NO_CAMERA
    assert "palm.device" in done.stderr


def test_stream_stats_rejects_bad_usage() -> None:
    assert run_tool("--seconds", "0").returncode == 2
    assert run_tool("--backend", "mock", "--device", "/dev/video0").returncode == 2


# --------------------------------------------------------------------------------------------------
# the kernel buffer timestamp (T-047, D-025): a scripted capture, no hardware
# --------------------------------------------------------------------------------------------------


class FakeCapture:
    """A ``cv2.VideoCapture`` stand-in with a scripted ``CAP_PROP_POS_MSEC`` per frame.

    ``read()`` hands out a frame already at the policy resolution (so no resize runs) and moves to
    the next scripted kernel timestamp, which ``get(CAP_PROP_POS_MSEC)`` then reports -- the order
    the driver relies on: the property describes the frame just read.
    """

    def __init__(self, pos_msec: list[float], size: tuple[int, int] = (640, 480)) -> None:
        self.pos_msec = pos_msec
        self.index = -1
        self.released = False
        self._frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)

    def isOpened(self) -> bool:  # noqa: N802 - the OpenCV spelling
        return True

    def set(self, prop: int, value: float) -> bool:
        return True

    def get(self, prop: int) -> float:
        if prop == cv2.CAP_PROP_POS_MSEC and 0 <= self.index < len(self.pos_msec):
            return self.pos_msec[self.index]
        return 0.0

    def read(self) -> tuple[bool, np.ndarray]:
        self.index += 1
        assert self.index < len(self.pos_msec), "the test asked for more frames than it scripted"
        return True, self._frame.copy()

    def release(self) -> None:
        self.released = True


def scripted_camera(monkeypatch, tmp_path: Path, pos_msec: list[float], arrivals: list[int]) -> V4L2Camera:
    """An ``oblique`` camera whose capture and whose arrival clock are both scripted by the test."""
    device = tmp_path / "video-scripted"
    device.write_text("", encoding="utf-8")
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: FakeCapture(pos_msec))
    times = iter(arrivals)
    return V4L2Camera(
        "oblique",
        device=str(device),
        now_ns=lambda: next(times),
        config_root=cameras_root(tmp_path),
    )


def late_and_burst(n: int = 300) -> tuple[list[int], list[float], list[int]]:
    """A clean 30 Hz capture grid delivered with one three-period stall and a catch-up burst.

    This is the Ego's measured behaviour in miniature (D-025): the kernel stamps the frames on time,
    user space collects three of them in a clump after a stall, and nothing is actually lost.
    Returns ``(kernel_ns, pos_msec, arrival_ns)``.
    """
    kernel = [k * PERIOD_NS for k in range(n)]
    arrivals = [t + 3_000_000 for t in kernel]  # the 2-3 ms delivery lag of a quiet host
    stall = [k for k in (97, 98, 99) if k < n]  # a short train has no room for the stall
    for k in stall:  # stalled until frame 99 landed, then delivered back to back
        arrivals[k] = 99 * PERIOD_NS + 3_000_000 + (k - 97) * 200_000
    pos_msec = [clock.to_monotonic_ns(t) / 1e6 for t in kernel]
    return kernel, pos_msec, arrivals


def grab_all(camera: V4L2Camera, n: int) -> list[stream_stats.Sample]:
    return [stream_stats.to_sample(camera.grab(), camera) for _ in range(n)]


def test_frames_are_stamped_with_the_kernel_buffer_timestamp(monkeypatch, tmp_path: Path) -> None:
    """The point of T-047: the frame carries when it was captured, not when it was collected."""
    kernel, pos_msec, arrivals = late_and_burst()
    camera = scripted_camera(monkeypatch, tmp_path, pos_msec, arrivals)
    samples = grab_all(camera, len(kernel))

    assert [s.ts_ns for s in samples] == kernel
    assert [s.arrival_ns for s in samples] == arrivals
    assert camera.kernel_stamps == len(kernel) and camera.arrival_stamps == 0
    assert {s.source for s in samples} == {"kernel"}


def test_the_kernel_stamp_is_clean_while_arrival_shows_the_drop(monkeypatch, tmp_path: Path) -> None:
    """Jitter < 1 ms on the kernel stamp, the gap still counted on arrival, nothing lost on either."""
    kernel, pos_msec, arrivals = late_and_burst()
    camera = scripted_camera(monkeypatch, tmp_path, pos_msec, arrivals)
    samples = grab_all(camera, len(kernel))

    on_kernel = stream_stats.stats([s.ts_ns for s in samples], 30.0)
    assert on_kernel["jitter_ms_p99"] < 1.0
    assert on_kernel["drops"] == 0 and on_kernel["frames_lost"] == 0

    report = stream_stats.stamp_report(samples, 30.0)
    assert report["stamp_source"] == {"kernel": len(kernel)}
    assert report["stats_arrival"]["drops"] == 1
    assert report["stats_arrival"]["frames_missed"] == 2
    assert report["stats_arrival"]["frames_lost"] == 0
    # The delivery jitter the kernel stamp sees through: two whole periods of it, on arrival only.
    assert report["stats_arrival"]["jitter_ms_max"] == pytest.approx(2 * PERIOD_NS / 1e6, abs=0.01)
    assert on_kernel["jitter_ms_max"] < 1.0

    lag = report["arrival_minus_kernel_ms"]
    assert lag["n"] == len(kernel)
    assert lag["p50"] == pytest.approx(3.0, abs=0.01)
    # The stalled frame was collected two periods and 3 ms after it was captured -- still believed,
    # because that is well inside MAX_KERNEL_LAG_NS.
    assert lag["max"] == pytest.approx(2 * PERIOD_NS / 1e6 + 3.0, abs=0.01)


def test_a_device_that_reports_no_kernel_stamp_falls_back_to_arrival(monkeypatch, tmp_path: Path) -> None:
    kernel, _, arrivals = late_and_burst(n=20)
    camera = scripted_camera(monkeypatch, tmp_path, [0.0] * len(kernel), arrivals)
    samples = grab_all(camera, len(kernel))

    assert [s.ts_ns for s in samples] == arrivals
    assert {s.source for s in samples} == {"arrival"}
    assert all(s.kernel_ns is None for s in samples)
    assert camera.arrival_stamps == len(kernel) and camera.kernel_stamps == 0
    report = stream_stats.stamp_report(samples, 30.0)
    assert report["stamp_source"] == {"arrival": len(kernel)}
    assert report["arrival_minus_kernel_ms"] is None


def test_a_kernel_stamp_on_another_clock_is_rejected_and_kept(monkeypatch, tmp_path: Path) -> None:
    """A backend reporting a stream position or a wall clock is more than 100 ms from arrival."""
    kernel, _, arrivals = late_and_burst(n=20)
    elsewhere = [(clock.to_monotonic_ns(t) + 5 * 60 * 10**9) / 1e6 for t in kernel]
    camera = scripted_camera(monkeypatch, tmp_path, elsewhere, arrivals)
    samples = grab_all(camera, len(kernel))

    assert [s.ts_ns for s in samples] == arrivals
    assert {s.source for s in samples} == {"arrival"}
    # The rejected value is still reported, so a read-only check can show why it was rejected.
    assert all(s.kernel_ns is not None for s in samples)
    lag = stream_stats.stamp_report(samples, 30.0)["arrival_minus_kernel_ms"]
    assert lag["max"] < -1000.0  # five minutes in the future, in milliseconds


def test_a_kernel_stamp_that_stands_still_falls_back_after_the_first_frame(monkeypatch, tmp_path: Path) -> None:
    """Non-monotonic is a fallback: the emitted stamps must never go backwards (StreamBuffer)."""
    kernel, _, arrivals = late_and_burst(n=20)
    stuck = [clock.to_monotonic_ns(kernel[0]) / 1e6] * len(kernel)
    camera = scripted_camera(monkeypatch, tmp_path, stuck, arrivals)
    samples = grab_all(camera, len(kernel))

    assert samples[0].source == "kernel" and samples[0].ts_ns == kernel[0]
    assert {s.source for s in samples[1:]} == {"arrival"}
    assert camera.kernel_stamps == 1 and camera.arrival_stamps == len(kernel) - 1
    stamps = [s.ts_ns for s in samples]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)


def test_a_stream_without_a_last_stamp_reports_one_train(monkeypatch, tmp_path: Path) -> None:
    """MockCamera is untouched by T-047: one stamp, and the report says so rather than inventing."""
    samples = [stream_stats.to_sample(MockCamera("oblique").grab(), MockCamera("oblique")) for _ in range(5)]
    assert {s.source for s in samples} == {"driver"}
    report = stream_stats.stamp_report(samples, 30.0)
    assert report["stamp_source"] == {"driver": 5}
    assert report["stats_arrival"] is None and report["arrival_minus_kernel_ms"] is None


# --------------------------------------------------------------------------------------------------
# readonly: these need a camera and skip, naming it, when there is none
# --------------------------------------------------------------------------------------------------


@pytest.mark.readonly
@pytest.mark.parametrize("name", STREAMS)
def test_real_frames_arrive_at_the_policy_resolution(name: str) -> None:
    expected = tuple(config.load("cameras")[name]["policy_resolution"])
    with open_camera(name) as camera:
        frames = [camera.grab() for _ in range(30)]
    for stamped in frames:
        frame = stamped.payload
        assert frame.dtype == np.uint8
        assert (frame.shape[1], frame.shape[0]) == expected
        assert frame.shape[2] == 3
    stamps = [s.ts_ns for s in frames]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)


@pytest.mark.readonly
@pytest.mark.parametrize("name", STREAMS)
def test_probe_reports_what_the_device_negotiated(name: str) -> None:
    with open_camera(name) as camera:
        probe = camera.probe()
    assert probe.width > 0 and probe.height > 0
    assert probe.fps > 0
    assert probe.policy_resolution == tuple(config.load("cameras")[name]["policy_resolution"])


@pytest.mark.readonly
@pytest.mark.parametrize("name", STREAMS)
def test_a_real_frame_is_shaped_exactly_like_a_mock_frame(name: str) -> None:
    """The point of the policy-resolution resize: a recorder cannot tell the two backends apart."""
    with open_camera(name) as camera:
        real = camera.grab()
    mock = MockCamera(name).grab()
    assert real.payload.shape == mock.payload.shape
    assert real.payload.dtype == mock.payload.dtype


@pytest.mark.readonly
@pytest.mark.parametrize("name", STREAMS)
def test_real_frames_carry_a_monotonic_kernel_stamp_close_to_arrival(name: str) -> None:
    """T-047 on the device: the V4L2 buffer timestamp is on our clock, and it is the one we use."""
    with open_camera(name) as camera:
        stamps = []
        for _ in range(60):
            camera.grab()
            assert camera.last_stamp is not None
            stamps.append(camera.last_stamp)

    assert camera.kernel_stamps + camera.arrival_stamps == len(stamps)
    lags_ms = sorted((s.arrival_ns - s.kernel_ns) / 1e6 for s in stamps if s.kernel_ns is not None)
    assert lags_ms, "the V4L2 backend reported no buffer timestamp at all"
    # Same clock, one origin apart: the frame is captured before read() returns it, and the typical
    # distance is the few milliseconds of delivery lag. The median, not the max: a single stalled
    # delivery on a loaded host is exactly what this stamp exists to survive, and it must not turn
    # into a failing test (D-025).
    assert min(lags_ms) >= -1.0, f"kernel stamp ahead of arrival by {-min(lags_ms):.1f} ms"
    assert lags_ms[len(lags_ms) // 2] <= 100.0, f"kernel stamp p50 {lags_ms[len(lags_ms) // 2]:.1f} ms from arrival"
    assert camera.kernel_stamps >= 0.9 * len(stamps), (
        f"only {camera.kernel_stamps}/{len(stamps)} frames used the kernel stamp"
    )
    emitted = [s.ts_ns for s in stamps]
    assert emitted == sorted(emitted) and len(set(emitted)) == len(emitted)


@pytest.mark.readonly
def test_the_factory_builds_a_real_camera() -> None:
    try:
        camera = make("oblique", backend="real")
    except CameraUnavailable as exc:
        pytest.skip(f"no real oblique camera: {exc}")
    try:
        assert isinstance(camera, V4L2Camera)
        assert isinstance(camera, CameraDriver)
    finally:
        camera.close()


@pytest.mark.readonly
def test_a_closed_camera_refuses_to_grab() -> None:
    camera = open_camera("oblique")
    camera.close()
    camera.close()  # idempotent
    with pytest.raises(CameraUnavailable):
        camera.grab()
