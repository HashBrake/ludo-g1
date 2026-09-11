"""Mock driver tests (T-006; CLAUDE.md R1, R2, R3, 5.1).

Nothing here touches hardware and nothing here is marked ``motion``: the mocks are simulated robots,
which R1 exempts from the session gate and from nothing else. Several tests assert exactly that --
that a mock still refuses a command the envelope refuses, with no session file anywhere in sight.

Time comes from a :class:`FakeClock` the test advances, never from ``time.sleep``: the whole file
runs in well under a second while exercising tens of seconds of stream.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from drivers import BACKENDS, DEVICES, make
from drivers.interfaces import (
    ArmDriver,
    CameraDriver,
    GloveDriver,
    GloveSample,
    HandDriver,
    HandState,
    PoseDriver,
    WristPose,
)
from drivers.mock import COUNTER_BITS, MockArm, MockCamera, MockGlove, MockHand, MockPose, Ticker, frame_index
from runtime import config
from runtime.safety import REPO_ROOT, SafetyViolation
from runtime.types import ARM_DOF, JOINT_DIM, MotionCommand

SECOND_NS = 1_000_000_000


class FakeClock:
    """A monotonic nanosecond clock the test moves by hand."""

    def __init__(self, ns: int = 0) -> None:
        self.ns = int(ns)

    def __call__(self) -> int:
        return self.ns

    def advance(self, seconds: float) -> int:
        self.ns += round(seconds * SECOND_NS)
        return self.ns


def command(joints=0.0, pinch: float = 0.0) -> MotionCommand:
    q = np.full(JOINT_DIM, joints, dtype=np.float64) if np.isscalar(joints) else np.asarray(joints, dtype=float)
    return MotionCommand(arm=q[:ARM_DOF], waist_yaw=float(q[ARM_DOF]), pinch=pinch)


def engage(arm: MockArm, clk, cmd: MotionCommand, *, over_s: float = 0.2) -> MotionCommand:
    """Send ``cmd`` the way a stream is allowed to: a hold first, then a step inside the limit.

    ``config/safety.yaml`` ``first_command_max_step_rad`` refuses a *first* command further than
    0.05 rad from the measured state (D-018, T-033), so a test that wants the arm somewhere else
    commands the arm's own state first -- zero motion -- and then walks there over ``over_s``, which
    the velocity rule judges against that hold. This is what ``teleop/loop.py``'s clutch does.
    """
    arm.send_targets(command(arm.read_state().payload.joints))
    clk.advance(over_s)
    return arm.send_targets(cmd)


# --------------------------------------------------------------------------------------------------
# the contract: every mock satisfies the protocol the real driver will satisfy
# --------------------------------------------------------------------------------------------------


def test_every_mock_satisfies_its_protocol() -> None:
    clk = FakeClock()
    assert isinstance(MockArm(now_ns=clk), ArmDriver)
    assert isinstance(MockHand(now_ns=clk), HandDriver)
    assert isinstance(MockGlove(now_ns=clk), GloveDriver)
    assert isinstance(MockPose(now_ns=clk), PoseDriver)
    assert isinstance(MockCamera("top", now_ns=clk), CameraDriver)


def test_sample_types_validate_their_shapes() -> None:
    with pytest.raises(ValueError, match="3 values"):
        WristPose(position_m=[0.0, 0.0], quat_xyzw=[0.0, 0.0, 0.0, 1.0])
    with pytest.raises(ValueError, match="4 values"):
        WristPose(position_m=[0.0, 0.0, 0.0], quat_xyzw=[0.0, 0.0, 1.0])
    with pytest.raises(ValueError, match="at least one value"):
        HandState(joints_rad=[], pinch=0.0)
    assert GloveSample(angles_deg=[1.0, 2.0], pinch=0.5).pinch == 0.5


# --------------------------------------------------------------------------------------------------
# the factory
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", DEVICES)
def test_factory_builds_every_device_on_the_mock_backend(name: str) -> None:
    driver = make(name, now_ns=FakeClock())
    assert driver is not None


#: Devices with no real backend yet. The cameras got theirs in T-010 (drivers/cameras.py), the arm
#: its read-only one in T-018 (drivers/g1_arm.py), the hand its read-only one in T-019
#: (drivers/dexh15.py), and the glove and the controller pose theirs in T-020 (drivers/pxcap.py,
#: drivers/pico.py) -- all without a session, which empties this list. tests/test_cameras.py,
#: test_g1_arm.py, test_dexh15.py, test_pxcap.py and test_pico.py cover those five sides.
ACTUATED = tuple(
    name for name in DEVICES if name not in ("top", "oblique", "palm", "arm", "hand", "glove", "pose")
)


def test_the_factory_has_a_real_backend_for_every_device() -> None:
    """Nothing is left on the ``NotImplementedError`` branch of ``make`` after T-020.

    What each real driver *does* is its own test file's business; all this asserts is that the
    factory no longer turns a device away for not existing. An absent device is the normal answer
    here and is skipped over: its own file covers the reason it gives.
    """
    assert ACTUATED == ()
    for name in DEVICES:
        driver = None
        try:
            driver = make(name, backend="real")
        except NotImplementedError as exc:  # pragma: no cover - a regression, not a normal path
            pytest.fail(f"make({name!r}, backend='real') is still unimplemented: {exc}")
        except Exception:
            continue  # the device is not attached
        finally:
            if driver is not None and hasattr(driver, "close"):
                driver.close()


def test_factory_rejects_unknown_names_and_backends() -> None:
    with pytest.raises(ValueError, match="unknown driver 'left_foot'"):
        make("left_foot")
    with pytest.raises(ValueError, match="unknown backend 'sim'"):
        make("arm", backend="sim")
    assert BACKENDS == ("mock", "real")


def test_factory_camera_names_come_from_the_camera_config() -> None:
    cameras = config.load("cameras")
    for name in ("top", "oblique", "palm"):
        assert name in DEVICES and name in cameras


# --------------------------------------------------------------------------------------------------
# the ticker
# --------------------------------------------------------------------------------------------------


def test_ticker_emits_each_grid_point_exactly_once() -> None:
    clk = FakeClock()
    ticker = Ticker(100.0, clk)
    assert ticker.ticks() == []
    clk.advance(0.025)
    assert ticker.ticks() == [10_000_000, 20_000_000]
    assert ticker.ticks() == []
    clk.advance(0.02)
    assert ticker.ticks() == [30_000_000, 40_000_000]
    assert ticker.sample() == (4, 40_000_000)


def test_ticker_rejects_a_non_positive_rate_and_a_backwards_clock() -> None:
    clk = FakeClock(SECOND_NS)
    with pytest.raises(ValueError, match="positive rate"):
        Ticker(0.0, clk)
    ticker = Ticker(30.0, clk)
    clk.ns = 0
    with pytest.raises(ValueError, match="clock went backwards"):
        ticker.sample()


# --------------------------------------------------------------------------------------------------
# acceptance 1: 10 s of mock arm state at 100 Hz is 1000 +/- 1 monotonic samples
# --------------------------------------------------------------------------------------------------


def test_mock_state_rate_is_the_rate_the_brief_specifies() -> None:
    """The mock's grid is CLAUDE.md 5.2's 100 Hz state rate, and the two config files agree."""
    assert config.load("robot")["mock"]["state_hz"] == config.load("training")["rates"]["state_hz"] == 100


def test_ten_seconds_of_arm_state_at_100_hz_is_1000_monotonic_samples() -> None:
    clk = FakeClock()
    arm = MockArm(now_ns=clk)
    hz = float(config.load("robot")["mock"]["state_hz"])
    clk.advance(10.0)
    samples = arm.poll()

    assert abs(len(samples) - round(10.0 * hz)) <= 1
    stamps = [s.ts_ns for s in samples]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
    assert all(s.ts_ns == s.payload.ts_ns for s in samples)
    periods = np.diff(stamps)
    assert np.all(periods == round(SECOND_NS / hz))


def test_poll_drains_and_read_state_does_not() -> None:
    clk = FakeClock()
    arm = MockArm(now_ns=clk)
    clk.advance(0.1)
    first = arm.poll()
    assert len(first) == 10
    assert arm.poll() == []
    latest = arm.read_state()
    assert latest.ts_ns == first[-1].ts_ns
    assert arm.read_state().ts_ns == latest.ts_ns


# --------------------------------------------------------------------------------------------------
# the arm's first-order lag
# --------------------------------------------------------------------------------------------------


def test_arm_state_lags_the_commanded_target_with_the_configured_time_constant() -> None:
    clk = FakeClock()
    arm = MockArm(now_ns=clk)
    tau = float(config.load("robot")["mock"]["arm_tau_s"])
    # Shoulder pitch backwards: the zero pose sits close to the front face of the workspace box, so
    # the direction that stays inside it is the negative one (D-010).
    target = command([-0.2, 0, 0, 0, 0, 0, 0, 0])

    clk.advance(0.1)
    engage(arm, clk, target)
    assert arm.read_state().payload.arm[0] == pytest.approx(0.0)  # not instantaneous

    clk.advance(tau)
    after_tau = arm.read_state().payload.arm[0]
    assert after_tau == pytest.approx(-0.2 * (1 - np.exp(-1.0)), rel=0.05)  # one time constant: ~63%

    clk.advance(10 * tau)
    assert arm.read_state().payload.arm[0] == pytest.approx(-0.2, abs=1e-3)  # settled


def test_arm_lag_is_monotone_and_never_overshoots() -> None:
    clk = FakeClock()
    arm = MockArm(now_ns=clk)
    clk.advance(0.1)
    engage(arm, clk, command([-0.2, 0, 0, 0, 0, 0, 0, 0], pinch=1.0))
    clk.advance(1.0)
    track = [s.payload.arm[0] for s in arm.poll()]  # the engaging hold holds it at 0.0 first
    assert all(b <= a for a, b in zip(track, track[1:], strict=False))
    assert min(track) >= -0.2


def test_two_mock_arms_on_the_same_clock_sequence_are_identical() -> None:
    def run() -> np.ndarray:
        clk = FakeClock()
        arm = MockArm(now_ns=clk)
        out = []
        for k in range(20):
            clk.advance(0.05)
            arm.send_targets(command([-0.01 * k, 0, 0, 0, 0, 0, 0, 0], pinch=0.5))
            out.append(arm.read_state().payload.to_vector())
        return np.asarray(out)

    assert np.array_equal(run(), run())


# --------------------------------------------------------------------------------------------------
# acceptance 2: the mock path goes through the guard
# --------------------------------------------------------------------------------------------------


def test_the_mock_arm_guard_is_simulated_and_needs_no_session() -> None:
    arm = MockArm(now_ns=FakeClock())
    assert arm.guard.simulated is True
    assert not arm.guard.session_status().valid  # no session file, and none is needed
    assert not (REPO_ROOT / "hardware" / "session.enable").exists()


def test_an_out_of_envelope_velocity_raises_safety_violation() -> None:
    clk = FakeClock()
    arm = MockArm(now_ns=clk)
    clk.advance(0.1)
    arm.send_targets(command(0.0))  # the stream engages at the state, so the jump is not the first
    clk.advance(0.02)
    with pytest.raises(SafetyViolation) as exc:
        arm.send_targets(command([2.5, 0, 0, 0, 0, 0, 0, 0]))
    assert exc.value.rule == "joint_velocity"


def test_a_target_that_walks_out_of_the_workspace_box_raises_safety_violation() -> None:
    """Ramped slowly enough to pass the velocity limit, so it is the box that refuses it."""
    clk = FakeClock()
    arm = MockArm(now_ns=clk)
    q = np.zeros(JOINT_DIM)
    with pytest.raises(SafetyViolation) as exc:
        for step in range(1, 100):
            clk.advance(0.02)
            q[0] = -0.02 * step
            arm.send_targets(command(q))
    assert exc.value.rule == "workspace_box"
    assert "left_wrist_yaw_link" in exc.value.message


def test_a_refused_command_does_not_move_the_simulated_arm() -> None:
    clk = FakeClock()
    arm = MockArm(now_ns=clk)
    clk.advance(0.1)
    engage(arm, clk, command([-0.1, 0, 0, 0, 0, 0, 0, 0]))
    clk.advance(1.0)
    settled = arm.read_state().payload.joints.copy()
    with pytest.raises(SafetyViolation):
        arm.send_targets(command([float("nan"), 0, 0, 0, 0, 0, 0, 0]))
    clk.advance(1.0)
    assert np.allclose(arm.read_state().payload.joints, settled, atol=1e-9)


def test_the_guard_counts_only_admitted_commands() -> None:
    clk = FakeClock()
    arm = MockArm(now_ns=clk)
    clk.advance(0.1)
    arm.send_targets(command(0.0))
    assert arm.guard.admitted == 1
    with pytest.raises(SafetyViolation):
        arm.send_targets(command(0.0))  # inside one rate-limit period
    assert arm.guard.admitted == 1


def test_joint_targets_outside_the_joint_limits_are_clamped_not_sent_raw() -> None:
    clk = FakeClock()
    arm = MockArm(now_ns=clk)
    lower = config.load("safety")["joint_limits_rad"]["waist_yaw_joint"][0]
    clamp = -float(config.load("safety")["waist_yaw_clamp_rad"])
    effective = max(lower, clamp)  # the tighter of the joint limit and the waist clamp
    clk.advance(0.1)
    # 0.6 rad of waist over 0.45 s is 1.33 rad/s, inside the velocity limit the hold is judged by.
    admitted = engage(arm, clk, command([0, 0, 0, 0, 0, 0, 0, effective - 0.4]), over_s=0.45)
    assert "waist_yaw_joint" in admitted.clamped
    assert admitted.waist_yaw == pytest.approx(effective)


# --------------------------------------------------------------------------------------------------
# acceptance 3: camera frame sizes and determinism
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [("top", (480, 640, 3)), ("oblique", (480, 640, 3)), ("palm", (240, 320, 3))],
)
def test_camera_frames_have_the_policy_shape_and_dtype(name: str, expected: tuple[int, int, int]) -> None:
    frame = MockCamera(name, now_ns=FakeClock()).grab().payload
    assert frame.shape == expected
    assert frame.dtype == np.uint8
    width, height = config.load("cameras")[name]["policy_resolution"]
    assert frame.shape[:2] == (height, width)


def test_camera_frames_advance_at_the_configured_fps_and_carry_a_readable_counter() -> None:
    clk = FakeClock()
    cam = MockCamera("top", now_ns=clk)
    fps = float(config.load("cameras")["top"]["fps"])
    first = cam.grab()
    assert frame_index(first.payload) == 0

    clk.advance(1.0)
    later = cam.grab()
    assert frame_index(later.payload) == round(fps)
    # The grid period is an integer number of nanoseconds, so a second of 30 fps is 30 periods and
    # not exactly 1e9 ns.
    assert abs(later.ts_ns - first.ts_ns - SECOND_NS) <= round(SECOND_NS / fps)
    assert not np.array_equal(first.payload, later.payload)


def test_camera_frames_are_a_pure_function_of_the_frame_index() -> None:
    cam = MockCamera("palm", now_ns=FakeClock())
    assert np.array_equal(cam.frame(7), cam.frame(7))
    assert not np.array_equal(cam.frame(7), cam.frame(8))
    assert frame_index(cam.frame(2**COUNTER_BITS - 1)) == 2**COUNTER_BITS - 1


def test_unknown_camera_name_names_the_known_ones() -> None:
    with pytest.raises(KeyError, match="oblique"):
        MockCamera("side", now_ns=FakeClock())


# --------------------------------------------------------------------------------------------------
# acceptance 4: the pinch synergy
# --------------------------------------------------------------------------------------------------


def test_pinch_zero_and_one_are_two_distinct_fifteen_joint_vectors_from_the_config() -> None:
    hand_cfg = config.load("hand")
    hand = MockHand(now_ns=FakeClock())
    open_pose = hand.synergy(0.0)
    closed_pose = hand.synergy(1.0)

    assert open_pose.shape == closed_pose.shape == (len(hand_cfg["joint_order"]),) == (15,)
    assert np.array_equal(open_pose, np.asarray(hand_cfg["mock"]["open_pose"], dtype=float))
    assert np.array_equal(closed_pose, np.asarray(hand_cfg["mock"]["closed_pose"], dtype=float))
    assert not np.allclose(open_pose, closed_pose)
    assert np.allclose(hand.synergy(0.5), 0.5 * (open_pose + closed_pose))


def test_the_real_synergy_poses_stay_unmeasured_and_the_mock_does_not_claim_them() -> None:
    """R5: the mock's stand-in poses must not read as a measurement of the real synergy."""
    hand_cfg = config.load("hand")
    assert hand_cfg["pinch"]["open_pose"] == config.UNMEASURED
    assert hand_cfg["pinch"]["closed_pose"] == config.UNMEASURED
    unmeasured = config.unmeasured("hand")
    assert "mock.open_pose" in unmeasured and "mock.closed_pose" in unmeasured


def test_send_pinch_goes_through_the_guard_and_returns_the_admitted_synergy() -> None:
    clk = FakeClock()
    hand = MockHand(now_ns=clk)
    assert hand.guard.simulated is True

    clk.advance(1.0)
    closed = hand.send_pinch(1.0)
    assert np.array_equal(closed, hand.synergy(1.0))
    assert hand.read_state().payload.pinch == pytest.approx(1.0)
    assert np.array_equal(hand.read_state().payload.joints_rad, closed)

    clk.advance(1.0)
    opened = hand.send_pinch(0.0)
    assert np.array_equal(opened, hand.synergy(0.0))


def test_send_pinch_clamps_out_of_range_and_rate_limits_the_slew() -> None:
    clk = FakeClock()
    hand = MockHand(now_ns=clk)
    slew = float(config.load("safety")["hand"]["pinch_rate_limit_per_s"])

    clk.advance(1.0)
    hand.send_pinch(5.0)  # clamped to the top of pinch_scalar_range
    assert hand.read_state().payload.pinch == pytest.approx(1.0)

    clk.advance(0.1)
    hand.send_pinch(0.0)  # slew-limited: at most slew * 0.1 away from 1.0
    assert hand.read_state().payload.pinch == pytest.approx(1.0 - slew * 0.1)


def test_send_pinch_refuses_a_non_finite_scalar() -> None:
    clk = FakeClock()
    hand = MockHand(now_ns=clk)
    clk.advance(1.0)
    with pytest.raises(SafetyViolation) as exc:
        hand.send_pinch(float("nan"))
    assert exc.value.rule == "non_finite"


def test_palm_frame_is_the_palm_camera() -> None:
    hand = MockHand(now_ns=FakeClock())
    height, width = hand.palm_frame().payload.shape[:2]
    assert [width, height] == config.load("cameras")["palm"]["policy_resolution"]


# --------------------------------------------------------------------------------------------------
# input streams: glove and controller pose
# --------------------------------------------------------------------------------------------------


def test_glove_emits_the_configured_channel_count_in_degrees_at_the_configured_rate() -> None:
    clk = FakeClock()
    glove = MockGlove(now_ns=clk)
    channels = config.load("training")["observation"]["extra_recorded"]["glove_channels"]
    amplitude = float(config.load("hand")["mock"]["glove_angle_amplitude_deg"])
    hz = float(config.load("hand")["glove"]["input_hz"])

    first = glove.read()
    assert first.payload.angles_deg.shape == (channels,) == (17,)
    assert np.all(np.abs(first.payload.angles_deg) <= amplitude + 1e-9)

    clk.advance(1.0)
    later = glove.read()
    assert later.ts_ns - first.ts_ns == round(SECOND_NS)
    assert glove.sample_at(later.ts_ns).pinch == later.payload.pinch  # pure function of the stamp
    samples = [glove.sample_at(round(k * SECOND_NS / hz)).pinch for k in range(200)]
    low, high = config.load("hand")["pinch"]["scalar_range"]
    assert min(samples) >= low and max(samples) <= high
    assert max(samples) - min(samples) > 0.5  # it really sweeps the range


def test_pose_is_metres_and_a_unit_quaternion_in_xyzw_order() -> None:
    clk = FakeClock()
    pose_driver = MockPose(now_ns=clk)
    radius = float(config.load("robot")["mock"]["pose_radius_m"])

    first = pose_driver.read().payload
    assert np.allclose(first.quat_xyzw, [0.0, 0.0, 0.0, 1.0])  # identity at t=0: w is last
    assert np.linalg.norm(first.position_m) <= radius + 1e-9

    clk.advance(1.0)
    later = pose_driver.read().payload
    assert not np.allclose(first.position_m, later.position_m)
    assert np.linalg.norm(later.quat_xyzw) == pytest.approx(1.0)
    assert np.linalg.norm(later.position_m[:2]) == pytest.approx(radius)


def test_pose_stream_runs_at_the_configured_rate() -> None:
    clk = FakeClock()
    pose_driver = MockPose(now_ns=clk)
    hz = float(config.load("robot")["mock"]["pose_hz"])
    first = pose_driver.read()
    clk.advance(1.0 / hz)
    assert pose_driver.read().ts_ns - first.ts_ns == round(SECOND_NS / hz)
    clk.advance(1.0)
    assert abs(pose_driver.read().ts_ns - first.ts_ns - SECOND_NS) <= 2 * round(SECOND_NS / hz)


# --------------------------------------------------------------------------------------------------
# audit: only the mocks build a simulated guard, and no mock hides a hard-coded trajectory
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("pattern", ["Guard(", "Guard.from_config("])
def test_only_drivers_mock_constructs_a_guard(pattern: str) -> None:
    """Acceptance: ``grep -rn 'Guard(' drivers/ | grep -v mock`` is empty, and the same for the
    classmethod the mocks actually use, so that the criterion cannot be met by spelling alone."""
    hits = subprocess.run(
        ["grep", "-rn", pattern, "drivers/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    assert [line for line in hits if "mock" not in line] == []


def test_every_mock_actuator_builds_its_guard_simulated() -> None:
    for path in (Path("drivers/mock/g1_arm.py"), Path("drivers/mock/dexh15.py")):
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert "Guard.from_config(simulated=True" in text, path
        assert "simulated=False" not in text, path


def test_mocks_import_nothing_from_tools_hardware_checks() -> None:
    """R2: scripted motion lives only in tools/hardware_checks/ and never reaches a driver."""
    for path in sorted((REPO_ROOT / "drivers").rglob("*.py")):
        assert "hardware_checks" not in path.read_text(encoding="utf-8"), path
