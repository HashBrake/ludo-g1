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
from runtime import config
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


def test_the_factory_still_refuses_the_actuated_devices() -> None:
    # `arm` left this list in T-018: it has a real, read-only driver now (tests/test_g1_arm.py).
    for name in ("hand", "glove", "pose"):
        with pytest.raises(NotImplementedError):
            make(name, backend="real")


def test_the_factory_reports_an_absent_camera_as_unavailable() -> None:
    """`palm` is unconfigured on purpose (Phase 1, DexH15): the factory must not crash on it."""
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
