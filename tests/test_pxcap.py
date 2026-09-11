"""The real PxCap Pro driver: read-only encoder frames and the two binding routes (T-020, R1, R2).

The glove is not attached (agents/HARDWARE_NEEDED.md H-004), so the weight here is carried by
``FakeGlove``: an object with the method names, the return conventions and the frame shape of
``pxcappro.PxCapPro`` as the manual documents them (``third_party/pxcap_pro_sdk.md`` 5.2.1, 5.2.2)
and as the vendored binding actually exposes them. ``pxhandsdk`` is not installed (Q-005), so the
fake is plain Python rather than the SDK's own types --
:func:`test_the_vendored_binding_matches_the_shapes_this_driver_reads` is what pins the fake to the
real thing, by loading the vendored cp310 binding in a subprocess and comparing attribute names.

The tests that need the actual glove are marked ``readonly`` and skip with a reason naming the
serial device, the same discipline as ``tests/test_dexh15.py``. Nothing in this file can move
anything: the glove is an input device and this driver never names one of the SDK's writing verbs.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from drivers import make
from drivers.interfaces import GloveDriver, GloveSample
from drivers.pxcap import (
    BUNDLE_BINDING,
    BUNDLE_LIBRARY,
    GloveProbe,
    GloveUnavailable,
    PxCap,
    load_binding,
    resolve_port,
)
from runtime import config
from tools.hardware_checks import stream_stats

REPO = Path(__file__).resolve().parents[1]

#: A serial node that certainly exists and is certainly not a glove. The fake never opens it; the
#: name only has to be a real path, because `resolve_port` checks that an explicit port exists.
FAKE_PORT = "/dev/null"

#: Everything in the PxCapPro API that changes the device's persistent state: encoder zeroing,
#: sensor zeroing, the SN write, the static-magnet check and the firmware upgrade. None of them may
#: appear anywhere in drivers/pxcap.py (R1, R2).
WRITE_VERBS = re.compile(
    r"set_encoder_calibration|set_sensor_calibration|set_sn|set_static_magnet_check|"
    r"set_upgrade_online_address|set_upgrade_offline_package|upgrade_firmware"
)


class Clock:
    """The injectable monotonic clock of docs/drivers.md: nanoseconds, advanced by hand."""

    def __init__(self, ns: int = 1_000_000) -> None:
        self.ns = ns

    def advance(self, seconds: float) -> None:
        self.ns += int(seconds * 1e9)

    def __call__(self) -> int:
        return self.ns


class Values:
    """``PxCapProEncoderAngles`` / ``PxCapProEncoderRaw``: one ``values`` list of 17 entries."""

    def __init__(self, values: list) -> None:
        self.values = values


class Frame:
    """``PxCapProCollectionData``: angles, raw encoders and the two host-side stamps."""

    def __init__(self, angles: list, counts: list, host_ns: int, unix_ns: int) -> None:
        self.joint_angles = Values(angles)
        self.encoder_raw = Values(counts)
        self.timestamp_monotonic_ns = host_ns
        self.timestamp_unix_ns = unix_ns


class Versions:
    """``PxCapProVersionInfo``: the main controller's firmware version and the sensors'."""

    def __init__(self, controller: str) -> None:
        self.controller_version = controller
        self.sensor_version_count = 0
        self.sensor_versions = []


class FakeGlove:
    """The read half of ``pxcappro.PxCapPro``: 0 is success, a read returns ``(rc, payload)``."""

    def __init__(self, *, connect_rc: int = 0, start_rc: int = 0, rc: int = 0) -> None:
        self.calls: list[str] = []
        self.connect_rc, self.start_rc, self.rc = connect_rc, start_rc, rc
        self.port: str | None = None
        self.frequency_hz: int | None = None
        self.collecting = False
        self._callback: Any = None

    # -- session
    def connect_device(self, port: str) -> int:
        self.calls.append(f"connect_device({port})")
        self.port = port
        return self.connect_rc

    def disconnect_device(self) -> int:
        self.calls.append("disconnect_device")
        return 0

    def is_connected(self) -> tuple[int, bool]:
        return self.rc, self.port is not None

    def get_last_error(self) -> tuple[int, str]:
        return self.rc, "the device is not connected"

    # -- identity
    def get_sdk_version(self) -> tuple[int, str]:
        self.calls.append("get_sdk_version")
        return self.rc, "1.0.8 20260806 17:08"

    def get_sn(self) -> tuple[int, str]:
        self.calls.append("get_sn")
        return self.rc, "PXP-TEST-0001"

    def get_firmware_versions(self) -> tuple[int, Versions]:
        self.calls.append("get_firmware_versions")
        return self.rc, Versions("c1.2.3")

    # -- reading
    def get_encoder_angles(self) -> tuple[int, Values]:
        self.calls.append("get_encoder_angles")
        return self.rc, Values([0.0] * 17)

    def start_collection(self, frequency_hz: int, callback: Any) -> int:
        self.calls.append(f"start_collection({frequency_hz})")
        self.frequency_hz, self._callback = frequency_hz, callback
        self.collecting = self.start_rc == 0
        return self.start_rc

    def stop_collection(self) -> int:
        self.calls.append("stop_collection")
        self.collecting = False
        return 0

    # -- the test's own handle on the stream
    def emit(self, angles: list | None = None, host_ns: int = 0, unix_ns: int = 0) -> None:
        """Deliver one collection frame, the way the SDK's own thread would."""
        values = [float(v) for v in (angles if angles is not None else range(17))]
        self._callback(Frame(values, [int(v) for v in range(len(values))], host_ns, unix_ns))


def glove_root(tmp_path: Path, **overrides: Any) -> Path:
    """A config directory whose ``hand.yaml`` points the glove at a node that exists."""
    data = yaml.safe_load((REPO / "config" / "hand.yaml").read_text(encoding="utf-8"))
    data["glove"]["port"] = FAKE_PORT
    data["glove"].update(overrides)
    (tmp_path / "hand.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path


def linked_glove(tmp_path: Path, clock: Clock | None = None, **kwargs: Any) -> tuple[PxCap, FakeGlove, Clock]:
    """A driver on a connected fake, on a config that resolves the port to :data:`FAKE_PORT`."""
    fake = FakeGlove(**{k: kwargs.pop(k) for k in ("connect_rc", "start_rc", "rc") if k in kwargs})
    tick = Clock() if clock is None else clock
    root = kwargs.pop("config_root", None) or glove_root(tmp_path)
    return PxCap(session_factory=lambda: fake, now_ns=tick, config_root=root, **kwargs), fake, tick


def real_glove() -> PxCap:
    """Open the real glove or skip, naming the serial device. Used by every ``readonly`` test."""
    try:
        return PxCap()
    except GloveUnavailable as exc:
        pytest.skip(f"no PxCap Pro glove: {exc}")


# --------------------------------------------------------------------------------------------------
# R1/R2: this driver cannot change anything, on the device or off it
# --------------------------------------------------------------------------------------------------


def test_the_module_never_names_a_verb_that_changes_the_device() -> None:
    """Calibration, SN write and firmware upgrade appear nowhere in the module (R1, R2)."""
    source = (REPO / "drivers" / "pxcap.py").read_text(encoding="utf-8")
    hits = [line for line in source.splitlines() if WRITE_VERBS.search(line)]
    assert hits == [], hits


def test_the_driver_satisfies_the_glove_driver_protocol() -> None:
    """Same protocol as MockGlove, so nothing downstream of a driver can tell them apart."""
    assert isinstance(PxCap.__new__(PxCap), GloveDriver)
    assert not hasattr(PxCap, "send_pinch") and not hasattr(PxCap, "send_targets")


def test_the_driver_builds_no_guard_and_holds_no_session(tmp_path: Path) -> None:
    """An input device has no motion path to guard; R3 has nothing to say here."""
    from drivers import pxcap

    glove, _fake, _clock = linked_glove(tmp_path)
    assert not hasattr(glove, "guard")
    assert not hasattr(pxcap, "Guard") and not hasattr(pxcap, "safety")


def test_importing_the_module_loads_no_binding_and_opens_nothing() -> None:
    """The binding is loaded inside load_binding, never at module import, and sys.path is untouched."""
    probe = (
        "import sys; before = list(sys.path); import drivers.pxcap as m; "
        "print('pxcappro' in sys.modules, 'pxhandsdk' in sys.modules, sys.path == before)"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO, capture_output=True, text=True, check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "False False True", done.stdout


# --------------------------------------------------------------------------------------------------
# finding the glove
# --------------------------------------------------------------------------------------------------


def test_an_explicit_port_must_exist() -> None:
    with pytest.raises(GloveUnavailable) as exc:
        resolve_port("/dev/no-such-glove")
    assert "/dev/no-such-glove" in str(exc.value)


def test_the_configured_port_wins_over_discovery(tmp_path: Path) -> None:
    assert resolve_port(None, glove_root(tmp_path)) == FAKE_PORT


def test_a_configured_port_that_is_not_there_names_the_key_and_the_hardware_item(tmp_path: Path) -> None:
    root = glove_root(tmp_path, port="/dev/serial/by-id/usb-Paxini-if00")
    with pytest.raises(GloveUnavailable) as exc:
        resolve_port(None, root)
    assert "glove.port" in str(exc.value) and "H-004" in str(exc.value)


def test_an_unmeasured_port_falls_back_to_usb_id_discovery(tmp_path: Path, monkeypatch: Any) -> None:
    from drivers import pxcap

    root = glove_root(tmp_path, port=config.UNMEASURED, usb_id="2fe3:0100")
    monkeypatch.setattr(pxcap, "find_port", lambda usb_id: f"/dev/serial/by-id/{usb_id}")
    assert resolve_port(None, root) == "/dev/serial/by-id/2fe3:0100"

    monkeypatch.setattr(pxcap, "find_port", lambda usb_id: None)
    with pytest.raises(GloveUnavailable) as exc:
        resolve_port(None, root)
    assert "2fe3:0100" in str(exc.value) and "H-004" in str(exc.value)


def test_with_neither_key_the_reason_names_both_and_the_nodes_searched(tmp_path: Path) -> None:
    root = glove_root(tmp_path, port=config.UNMEASURED, usb_id=config.UNMEASURED)
    with pytest.raises(GloveUnavailable) as exc:
        resolve_port(None, root)
    message = str(exc.value)
    assert "glove.port" in message and "glove.usb_id" in message and "/dev/ttyACM*" in message


def test_the_real_config_is_still_unmeasured_so_a_check_skips_rather_than_fails() -> None:
    """Until H-004 is done, `make("glove", backend="real")` must fail with a reason, not a traceback."""
    glove = config.load("hand")["glove"]
    if glove["port"] != config.UNMEASURED:
        pytest.skip("the glove port is configured (H-004 resolved)")
    with pytest.raises(GloveUnavailable):
        make("glove", backend="real")


# --------------------------------------------------------------------------------------------------
# opening the glove and starting the stream
# --------------------------------------------------------------------------------------------------


def test_it_connects_to_the_resolved_port_and_collects_at_the_configured_rate(tmp_path: Path) -> None:
    glove, fake, _clock = linked_glove(tmp_path)
    assert fake.port == FAKE_PORT
    assert fake.frequency_hz == int(config.load("hand")["glove"]["input_hz"])
    assert fake.collecting
    # Identity is read before the collection starts: the SDK refuses those calls during one.
    assert fake.calls.index("get_sdk_version") < fake.calls.index(f"start_collection({fake.frequency_hz})")
    assert glove.sdk_version == "1.0.8 20260806 17:08" and glove.serial_number == "PXP-TEST-0001"
    assert glove.controller_version == "c1.2.3"


def test_a_refused_connection_names_the_port_and_the_hardware_item(tmp_path: Path) -> None:
    with pytest.raises(GloveUnavailable) as exc:
        linked_glove(tmp_path, connect_rc=100)
    assert FAKE_PORT in str(exc.value) and "H-004" in str(exc.value)
    assert "not connected" in str(exc.value)  # the SDK's own last error is quoted


def test_a_refused_collection_closes_the_link_it_opened(tmp_path: Path) -> None:
    fake = FakeGlove(start_rc=4000)
    with pytest.raises(GloveUnavailable) as exc:
        PxCap(session_factory=lambda: fake, now_ns=Clock(), config_root=glove_root(tmp_path))
    assert "start_collection" in str(exc.value)
    assert "disconnect_device" in fake.calls and not fake.collecting


def test_identity_that_the_glove_declines_to_answer_is_empty_not_a_crash(tmp_path: Path) -> None:
    glove, _fake, _clock = linked_glove(tmp_path, rc=4000)
    assert (glove.sdk_version, glove.serial_number, glove.controller_version) == ("", "", "")


# --------------------------------------------------------------------------------------------------
# the stream
# --------------------------------------------------------------------------------------------------


def test_a_frame_arrives_as_seventeen_degrees_stamped_when_the_callback_ran(tmp_path: Path) -> None:
    clock = Clock()
    glove, fake, _clock = linked_glove(tmp_path, clock)
    clock.advance(0.02)
    fake.emit(angles=[float(i) for i in range(17)], host_ns=123, unix_ns=456)
    sample = glove.read()
    assert sample.ts_ns == clock.ns
    assert isinstance(sample.payload, GloveSample)
    assert sample.payload.angles_deg.shape == (17,)
    assert sample.payload.angles_deg[3] == 3.0
    assert not sample.payload.angles_deg.flags.writeable


def test_the_pinch_scalar_is_nan_while_the_distance_model_is_unmeasured(tmp_path: Path) -> None:
    """nan, not 0.0, which would read as 'the hand is open' (the same rule as HandState.pinch)."""
    glove, fake, _clock = linked_glove(tmp_path)
    assert not glove.pinch_measurable
    fake.emit()
    assert np.isnan(glove.read().payload.pinch)
    assert np.isnan(glove.tip_distance_m(np.zeros(17)))


def test_a_calibrated_distance_model_gives_a_real_scalar_through_retarget(tmp_path: Path) -> None:
    """Once Phase 1 fits the affine model the scalar is teleop.retarget.pinch_from_glove's."""
    from teleop.retarget import pinch_from_glove

    root = glove_root(
        tmp_path,
        pinch_distance={
            "model": "affine_tip_channels",
            "thumb_channel": "thumb_tip_channel",   # channel 0
            "index_channel": "index_tip_channel",   # channel 2
            "offset_mm": 90.0,
            "thumb_mm_per_deg": -0.5,
            "index_mm_per_deg": -0.5,
        },
    )
    glove, fake, _clock = linked_glove(tmp_path, config_root=root)
    assert glove.pinch_measurable
    angles = np.zeros(17)
    angles[0], angles[2] = 40.0, 40.0  # 90 - 0.5*40 - 0.5*40 = 50 mm between the tips
    assert glove.tip_distance_m(angles) == pytest.approx(0.050)
    fake.emit(angles=list(angles))
    assert glove.read().payload.pinch == pytest.approx(pinch_from_glove(0.050, root=root))
    assert 0.0 < glove.read().payload.pinch < 1.0


def test_full_state_carries_the_raw_counts_and_the_sdk_host_stamps(tmp_path: Path) -> None:
    glove, fake, _clock = linked_glove(tmp_path)
    fake.emit(host_ns=1234, unix_ns=5678)
    frame = glove.full_state().payload
    assert frame.encoder_counts.shape == (17,) and frame.encoder_counts.dtype == np.int64
    assert (frame.host_monotonic_ns, frame.host_unix_ns) == (1234, 5678)
    assert not frame.encoder_counts.flags.writeable


def test_poll_drains_every_frame_oldest_first_and_then_empties(tmp_path: Path) -> None:
    clock = Clock()
    glove, fake, _clock = linked_glove(tmp_path, clock)
    for i in range(5):
        clock.advance(0.02)
        fake.emit(angles=[float(i)] * 17)
    drained = glove.poll()
    assert [s.payload.angles_deg[0] for s in drained] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert [s.ts_ns for s in drained] == sorted(s.ts_ns for s in drained)
    assert glove.poll() == []


def test_a_frame_of_the_wrong_length_is_refused_and_names_the_config_key(tmp_path: Path) -> None:
    """The channel order is a hypothesis; a frame that disagrees is not reshaped silently."""
    glove, fake, _clock = linked_glove(tmp_path)
    fake.emit(angles=[0.0] * 15)
    with pytest.raises(GloveUnavailable) as exc:
        glove.read()
    assert "15 angles" in str(exc.value) and "glove.encoder_channels" in str(exc.value)


def test_no_frame_at_all_times_out_naming_the_port(tmp_path: Path) -> None:
    glove, _fake, _clock = linked_glove(tmp_path, timeout_s=0.01)
    with pytest.raises(GloveUnavailable) as exc:
        glove.read()
    assert FAKE_PORT in str(exc.value) and "H-004" in str(exc.value)


def test_a_stream_that_goes_silent_is_refused_rather_than_served_stale(tmp_path: Path) -> None:
    clock = Clock()
    glove, fake, _clock = linked_glove(tmp_path, clock, timeout_s=0.5)
    fake.emit()
    glove.read()
    clock.advance(2.0)
    with pytest.raises(GloveUnavailable) as exc:
        glove.read()
    assert "went silent" in str(exc.value) and "frame_timeout_s" in str(exc.value)


def test_probe_reports_the_measured_rate_and_the_glove_that_answered(tmp_path: Path) -> None:
    clock = Clock()
    glove, fake, _clock = linked_glove(tmp_path, clock)
    for _ in range(11):
        clock.advance(0.02)  # 50 Hz
        fake.emit()
    probe = glove.probe(window_s=1.0)
    assert isinstance(probe, GloveProbe)
    assert probe.samples == 11 and probe.input_hz == pytest.approx(50.0, abs=0.1)
    assert probe.expected_hz == 50.0 and probe.channels == 17
    assert probe.port == FAKE_PORT and probe.serial_number == "PXP-TEST-0001"
    assert probe.pinch_measurable is False


def test_close_stops_the_collection_then_disconnects_and_is_idempotent(tmp_path: Path) -> None:
    glove, fake, _clock = linked_glove(tmp_path)
    glove.close()
    glove.close()
    assert fake.calls.count("stop_collection") == 1 and fake.calls.count("disconnect_device") == 1
    assert fake.calls.index("stop_collection") < fake.calls.index("disconnect_device")
    with pytest.raises(GloveUnavailable) as exc:
        glove.read()
    assert "closed" in str(exc.value)


def test_the_context_manager_closes(tmp_path: Path) -> None:
    fake = FakeGlove()
    with PxCap(session_factory=lambda: fake, now_ns=Clock(), config_root=glove_root(tmp_path)) as glove:
        assert glove.port == FAKE_PORT
    assert not fake.collecting


def test_a_sample_is_shaped_like_the_mock_so_a_consumer_cannot_tell_them_apart(tmp_path: Path) -> None:
    glove, fake, _clock = linked_glove(tmp_path)
    fake.emit()
    real = glove.read().payload
    mock = make("glove").read().payload
    assert real.angles_deg.shape == mock.angles_deg.shape
    assert isinstance(real.pinch, float) and isinstance(mock.pinch, float)


# --------------------------------------------------------------------------------------------------
# the binding routes (Q-005)
# --------------------------------------------------------------------------------------------------


def test_an_unknown_route_is_a_usage_error() -> None:
    with pytest.raises(ValueError):
        load_binding("guess")


def test_the_pxhandsdk_route_says_so_when_the_deb_is_not_installed() -> None:
    try:
        import pxhandsdk  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("the pxhandsdk deb is installed (Q-005 answered); this is the not-installed path")
    with pytest.raises(GloveUnavailable) as exc:
        load_binding("pxhandsdk")
    assert "Q-005" in str(exc.value)


def test_the_vendored_binding_matches_the_shapes_this_driver_reads() -> None:
    """Route (b) end to end, in a subprocess: the bundle's cp310 binding loads with no glove attached.

    It also pins ``FakeGlove`` to the real API: every method the driver calls exists on the real
    ``PxCapPro``, and the real ``PxCapProCollectionData`` carries the four attributes the collection
    callback reads. The subprocess keeps the vendored ``libpxcappro_sdk.so`` out of the pytest
    process, where the DexH15 SDK's own libraries also live.
    """
    if not BUNDLE_LIBRARY.exists() or not BUNDLE_BINDING.is_dir():
        pytest.skip(f"the vendored PxCapPro bundle is not on disk ({BUNDLE_LIBRARY})")
    probe = textwrap.dedent(
        """
        import json
        from drivers.pxcap import load_binding

        px = load_binding("bundle")
        session, frame = px.PxCapPro(), px.PxCapProCollectionData()
        print(json.dumps({
            "sdk_version": session.get_sdk_version()[1],
            "angles": len(frame.joint_angles.values),
            "counts": len(frame.encoder_raw.values),
            "session": sorted(n for n in dir(session) if not n.startswith("_")),
            "frame": sorted(n for n in dir(frame) if not n.startswith("_")),
            "no_device": session.get_encoder_angles()[0],
        }))
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO, capture_output=True, text=True, check=False, timeout=180
    )
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout.splitlines()[-1])
    assert got["angles"] == 17 and got["counts"] == 17
    assert got["sdk_version"].startswith("1.0.8")
    assert got["no_device"] != 0  # a read with nothing plugged in fails, it does not invent a frame
    called = ("connect_device", "disconnect_device", "get_sdk_version", "get_sn",
              "get_firmware_versions", "get_last_error", "start_collection", "stop_collection")
    assert set(called) <= set(got["session"]), sorted(set(called) - set(got["session"]))
    read = ("joint_angles", "encoder_raw", "timestamp_monotonic_ns", "timestamp_unix_ns")
    assert set(read) <= set(got["frame"]), sorted(set(read) - set(got["frame"]))
    assert set(called) <= set(dir(FakeGlove)), "FakeGlove must offer everything the driver calls"


# --------------------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------------------


def test_the_config_carries_the_keys_the_driver_reads() -> None:
    """Section 7: every number the driver uses is in config/hand.yaml, none is a constant in code."""
    glove = config.load("hand")["glove"]
    assert len(glove["encoder_channels"]) == 17
    assert len(set(glove["encoder_channels"])) == 17
    assert glove["input_hz"] > 0 and glove["frame_timeout_s"] > 0
    assert glove["pinch_distance"]["thumb_channel"] in glove["encoder_channels"]
    assert glove["pinch_distance"]["index_channel"] in glove["encoder_channels"]


def test_the_distance_model_is_still_unmeasured_and_reported_as_such() -> None:
    unmeasured = config.unmeasured("hand")
    for key in ("offset_mm", "thumb_mm_per_deg", "index_mm_per_deg"):
        assert f"glove.pinch_distance.{key}" in unmeasured
    assert "glove.port" in unmeasured and "glove.usb_id" in unmeasured


# --------------------------------------------------------------------------------------------------
# stream_stats.py --stream glove
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


def test_stream_stats_on_the_mock_glove_reports_the_configured_input_rate() -> None:
    expected = float(config.load("hand")["glove"]["input_hz"])
    done = run_tool("--backend", "mock", "--stream", "glove", "--seconds", "3", "--json")
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["stream"] == "glove" and report["policy_resolution"] is None
    assert abs(report["stats"]["fps"] - expected) <= 1.0, report["stats"]
    assert report["stats"]["drops"] == 0 and report["stats"]["frames"] > 100
    assert report["stats"]["expected_hz"] == expected


def test_stream_stats_glove_exits_3_while_the_glove_is_unplugged() -> None:
    if config.load("hand")["glove"]["port"] != config.UNMEASURED:
        pytest.skip("the glove port is configured (H-004 resolved); this is the unplugged path")
    done = run_tool("--backend", "real", "--stream", "glove", "--seconds", "1")
    assert done.returncode == stream_stats.NO_STREAM
    assert "glove.port" in done.stderr


def test_stream_stats_refuses_a_device_override_for_the_glove() -> None:
    assert run_tool("--backend", "real", "--stream", "glove", "--device", "/dev/ttyACM0").returncode == 2


# --------------------------------------------------------------------------------------------------
# readonly: these need the glove and skip, naming the device, when it is not there
# --------------------------------------------------------------------------------------------------


@pytest.mark.readonly
def test_real_encoder_frames_arrive_and_are_shaped_like_a_mock_sample() -> None:
    with real_glove() as glove:
        first = glove.read()
        frame = glove.full_state()
    mock = make("glove").read()
    assert first.payload.angles_deg.shape == mock.payload.angles_deg.shape
    assert frame.payload.encoder_counts.shape == first.payload.angles_deg.shape
    assert first.ts_ns > 0


@pytest.mark.readonly
def test_real_probe_reports_the_glove_that_answered() -> None:
    with real_glove() as glove:
        probe = glove.probe(window_s=1.0)
    assert probe.channels == 17 and probe.sdk_version
    assert probe.input_hz > 0
