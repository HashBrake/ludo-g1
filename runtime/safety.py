"""The single gate every motion command passes through (CLAUDE.md R1 and R3).

Three pieces, documented for humans in ``docs/safety.md``:

:class:`SessionGate`
    R1. Reads the human-written session file named by ``config/safety.yaml`` ``session.file``
    (``hardware/session.enable``) and says whether motion is allowed right now. Agents never write
    that file; ``tools/hardware_checks/enable_session.py`` is the only writer and a human is the only
    one who runs it.

:class:`Envelope`
    R3. Joint limits, waist clamp, workspace box, joint velocity limit, first-command step cap,
    command rate limit and the pinch scalar range and slew, all read from ``config/safety.yaml``.
    Out-of-limit joint targets and an out-of-range pinch are clamped and reported; a step, velocity,
    box, rate or non-finite violation raises :class:`SafetyViolation`.

:class:`Guard`
    The two together. ``Guard.admit`` is what a driver calls; there is no other way in, and there is
    no flag, environment variable or "dev mode" that skips the gate on hardware. ``simulated=True``
    skips the session gate only, never the envelope, and is set by ``drivers/mock`` alone.

Time: the session file is a human artifact and its timestamps are wall clock, so expiry is judged
against ``datetime.now``. Rate and velocity are judged against :func:`runtime.clock.now_ns`, which is
monotonic. The two clocks are never mixed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from runtime import clock, config
from runtime.log import get_logger
from runtime.types import JOINT_DIM, MotionCommand, RobotState

__all__ = [
    "REPO_ROOT",
    "Envelope",
    "Guard",
    "SafetyViolation",
    "SessionGate",
    "SessionStatus",
]

#: Repo root, used to resolve the repo-relative ``session.file`` path from the config.
REPO_ROOT: Path = Path(__file__).resolve().parent.parent

_log = get_logger("runtime.safety")

#: Forward kinematics of the 8 commanded joints to the workspace-box point (``workspace_box_m.point``
#: in the ``workspace_box_m.frame`` frame), in metres. :func:`runtime.fk.left_arm_fk` is the real one
#: and is what :meth:`Envelope.from_config` uses when none is passed; tests inject a mock. An envelope
#: built without one fails closed: every check raises.
FkFn = Callable[[np.ndarray], np.ndarray]


class SafetyViolation(RuntimeError):
    """A motion command was refused. ``rule`` names which rule refused it."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"[{rule}] {message}")
        self.rule = rule
        self.message = message


# --------------------------------------------------------------------------------------------------
# R1: the session gate
# --------------------------------------------------------------------------------------------------


class SessionStatus:
    """Whether motion is allowed right now, and why not when it is not.

    ``reason`` is always populated: on a valid session it says who enabled it and when it expires, so
    that a log line is self-contained.
    """

    __slots__ = ("valid", "reason", "enabled_by", "expires_at")

    def __init__(
        self,
        valid: bool,
        reason: str,
        enabled_by: str | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        self.valid = bool(valid)
        self.reason = reason
        self.enabled_by = enabled_by
        self.expires_at = expires_at

    def __repr__(self) -> str:
        return (
            f"SessionStatus(valid={self.valid}, reason={self.reason!r}, "
            f"enabled_by={self.enabled_by!r}, expires_at={self.expires_at!r})"
        )

    @property
    def seconds_left(self) -> float | None:
        """Seconds until the session expires, or None when there is no usable ``expires_at``."""
        if self.expires_at is None:
            return None
        return (self.expires_at - datetime.now(timezone.utc)).total_seconds()


def _parse_session_text(text: str) -> dict[str, str]:
    """Parse the 4.6 session file: ``key: value`` lines, blank lines and ``#`` comments ignored.

    Raises ValueError on a line that is not ``key: value`` or on a duplicate key. Deliberately not
    yaml: this file is four lines written by one script, and a yaml parser would happily accept a
    document that is nothing like it.
    """
    fields: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or not key or any(c.isspace() for c in key):
            raise ValueError(f"line {lineno}: expected 'key: value', got {raw!r}")
        if key in fields:
            raise ValueError(f"line {lineno}: duplicate key {key!r}")
        fields[key] = value.strip()
    if not fields:
        raise ValueError("file is empty")
    return fields


def _parse_stamp(name: str, value: str) -> datetime:
    """Parse one ISO 8601 timestamp; it must carry a UTC offset."""
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name}={value!r} is not an ISO 8601 timestamp: {exc}") from exc
    if stamp.tzinfo is None or stamp.tzinfo.utcoffset(stamp) is None:
        raise ValueError(f"{name}={value!r} has no UTC offset; write it as 2026-09-15T14:02:11+07:00")
    return stamp


class SessionGate:
    """R1. Reports whether a human has enabled a motion session that is still running.

    The gate re-reads the file whenever its ``stat`` changes and re-judges expiry on every call, so a
    session that runs out mid-run stops the next command. It fails closed on everything: a missing
    file, an unreadable file, a file it cannot parse, a missing field, a checklist that is not
    confirmed, a window longer than ``session.max_seconds``, a not-yet-started or expired window.
    """

    def __init__(self, path: Path | str | None = None, *, config_root: Path | str | None = None) -> None:
        cfg = config.load("safety", root=config_root)["session"]
        configured = Path(str(cfg["file"]))
        if path is not None:
            self.path = Path(path)
        else:
            self.path = configured if configured.is_absolute() else REPO_ROOT / configured
        self.required_fields: tuple[str, ...] = tuple(str(f) for f in cfg["required_fields"])
        self.required_checklist_value = str(cfg["required_checklist_value"])
        self.max_seconds = float(cfg["max_seconds"])
        self.default_seconds = float(cfg["default_seconds"])
        self._cache_key: tuple[int, int, int] | None = None
        self._cache_fields: dict[str, str] | None = None
        self._cache_error: str | None = None

    def __repr__(self) -> str:
        return f"SessionGate(path={str(self.path)!r})"

    def _read_fields(self) -> tuple[dict[str, str] | None, str | None]:
        """``(fields, error)`` for the current file, re-reading only when its ``stat`` changed."""
        try:
            st = os.stat(self.path)
        except OSError as exc:
            self._cache_key = None
            self._cache_fields = None
            self._cache_error = None
            return None, f"cannot read session file {self.path}: {exc.strerror or exc}"
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
        if key != self._cache_key:
            self._cache_key = key
            try:
                text = self.path.read_text(encoding="utf-8")
                self._cache_fields = _parse_session_text(text)
                self._cache_error = None
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                self._cache_fields = None
                self._cache_error = f"session file {self.path} is unparsable: {exc}"
        return self._cache_fields, self._cache_error

    def status(self, now: datetime | None = None) -> SessionStatus:
        """Judge the session file as of ``now`` (default: wall clock, UTC)."""
        now = datetime.now(timezone.utc) if now is None else now
        fields, error = self._read_fields()
        if fields is None:
            return SessionStatus(False, error or f"cannot read session file {self.path}")

        missing = [f for f in self.required_fields if f not in fields]
        if missing:
            return SessionStatus(False, f"session file {self.path} is missing field(s): {', '.join(missing)}")

        enabled_by = fields.get("enabled_by") or None
        if not enabled_by:
            return SessionStatus(False, f"session file {self.path} has an empty enabled_by")

        checklist = fields.get("checklist", "")
        if checklist != self.required_checklist_value:
            return SessionStatus(
                False,
                f"session file {self.path} has checklist={checklist!r}, "
                f"required {self.required_checklist_value!r}",
                enabled_by,
            )

        try:
            enabled_at = _parse_stamp("enabled_at", fields["enabled_at"])
            expires_at = _parse_stamp("expires_at", fields["expires_at"])
        except ValueError as exc:
            return SessionStatus(False, f"session file {self.path}: {exc}", enabled_by)

        window = (expires_at - enabled_at).total_seconds()
        if window <= 0:
            return SessionStatus(
                False,
                f"session file {self.path}: expires_at is not after enabled_at ({window:.0f} s)",
                enabled_by,
                expires_at,
            )
        if window > self.max_seconds:
            return SessionStatus(
                False,
                f"session file {self.path}: window {window:.0f} s exceeds session.max_seconds "
                f"{self.max_seconds:.0f} s",
                enabled_by,
                expires_at,
            )
        if now < enabled_at:
            return SessionStatus(
                False,
                f"session file {self.path}: enabled_at is "
                f"{(enabled_at - now).total_seconds():.0f} s in the future",
                enabled_by,
                expires_at,
            )
        if now >= expires_at:
            return SessionStatus(
                False,
                f"session file {self.path}: expired {(now - expires_at).total_seconds():.0f} s ago",
                enabled_by,
                expires_at,
            )
        return SessionStatus(
            True,
            f"session enabled by {enabled_by}, {(expires_at - now).total_seconds():.0f} s left",
            enabled_by,
            expires_at,
        )


# --------------------------------------------------------------------------------------------------
# R3: the envelope
# --------------------------------------------------------------------------------------------------


def _joint_names(robot: dict) -> tuple[str, ...]:
    """The 8 commanded joint names from ``config/robot.yaml``: the arm joints then waist yaw."""
    arm = [str(j["name"]) for j in robot["arm"]["joints"]]
    waist = [str(j["name"]) for j in robot["waist"]["joints"]]
    names = tuple(arm + waist[:1])
    action = tuple(str(n) for n in robot["action_order"])
    if action[:JOINT_DIM] != names:
        raise config.ConfigError(
            f"config/robot.yaml: action_order starts with {action[:JOINT_DIM]}, "
            f"but arm+waist joints are {names}"
        )
    return names


def _mechanical_ranges(robot: dict) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for entry in list(robot["arm"]["joints"]) + list(robot["waist"]["joints"]):
        lo, hi = entry["limit_rad"]
        out[str(entry["name"])] = (float(lo), float(hi))
    return out


class Envelope:
    """R3. Box, joint limits, waist clamp, rate, step and velocity limits, and the hand range.

    One envelope instance owns the rate-limit and velocity reference for one command stream; it is
    not thread-safe and is not shared between two drivers. :meth:`reset` drops the reference, which
    is what a driver does when it releases and re-takes the arm.
    """

    def __init__(
        self,
        *,
        names: tuple[str, ...],
        lower: np.ndarray,
        upper: np.ndarray,
        box_min: np.ndarray,
        box_max: np.ndarray,
        box_frame: str,
        box_point: str,
        velocity_limit_rad_s: float,
        command_rate_limit_hz: float,
        command_gap_reset_s: float,
        first_command_max_step_rad: float,
        watchdog_timeout_s: float,
        pinch_range: tuple[float, float],
        pinch_rate_limit_per_s: float,
        fk: FkFn | None = None,
    ) -> None:
        if len(names) != JOINT_DIM:
            raise config.ConfigError(f"envelope needs {JOINT_DIM} joint names, got {len(names)}")
        self.names = names
        self.lower = np.asarray(lower, dtype=np.float64)
        self.upper = np.asarray(upper, dtype=np.float64)
        self.box_min = np.asarray(box_min, dtype=np.float64)
        self.box_max = np.asarray(box_max, dtype=np.float64)
        self.box_frame = box_frame
        self.box_point = box_point
        self.velocity_limit_rad_s = float(velocity_limit_rad_s)
        self.command_rate_limit_hz = float(command_rate_limit_hz)
        self.command_gap_reset_s = float(command_gap_reset_s)
        self.first_command_max_step_rad = float(first_command_max_step_rad)
        self.watchdog_timeout_s = float(watchdog_timeout_s)
        self.pinch_range = (float(pinch_range[0]), float(pinch_range[1]))
        self.pinch_rate_limit_per_s = float(pinch_rate_limit_per_s)
        self.fk = fk
        self._last_ns: int | None = None
        self._last_joints: np.ndarray | None = None
        self._last_pinch: float | None = None
        if np.any(self.upper < self.lower):
            raise config.ConfigError("config/safety.yaml: a joint limit has upper < lower")
        if np.any(self.box_max <= self.box_min):
            raise config.ConfigError(
                f"config/safety.yaml: workspace box is empty after margin: {self.box_min} .. {self.box_max}"
            )
        if self.command_rate_limit_hz <= 0 or self.velocity_limit_rad_s <= 0:
            raise config.ConfigError("config/safety.yaml: rate and velocity limits must be positive")
        if self.first_command_max_step_rad <= 0:
            raise config.ConfigError("config/safety.yaml: first_command_max_step_rad must be positive")

    def __repr__(self) -> str:
        return (
            f"Envelope(point={self.box_point!r} in {self.box_frame!r}, "
            f"box={self.box_min.tolist()}..{self.box_max.tolist()}, "
            f"v<={self.velocity_limit_rad_s} rad/s, rate<={self.command_rate_limit_hz} Hz, "
            f"fk={'set' if self.fk is not None else 'MISSING'})"
        )

    # -- construction ------------------------------------------------------------------------------

    @classmethod
    def from_config(cls, fk: FkFn | None = None, *, root: Path | str | None = None) -> Envelope:
        """Build the envelope from ``config/safety.yaml``, cross-checked against ``config/robot.yaml``.

        ``fk=None`` means the real forward kinematics, :func:`runtime.fk.left_arm_fk` (T-011); pass
        one explicitly to override it, which is what the tests do. Constructing an :class:`Envelope`
        directly still defaults to no fk at all, and such an envelope fails closed.

        Refuses to build (``ConfigError``) when the two files disagree on the commanded joints or
        their order, or when a safety limit is wider than the mechanical range of its joint: the
        envelope may only ever be tighter than the hardware.
        """
        if fk is None:
            from runtime.fk import left_arm_fk  # imported here so mujoco is not pulled in to read a state

            fk = left_arm_fk
        safety = config.load("safety", root=root)
        robot = config.load("robot", root=root)
        names = _joint_names(robot)
        mechanical = _mechanical_ranges(robot)

        limits = safety["joint_limits_rad"]
        if tuple(str(k) for k in limits) != names:
            raise config.ConfigError(
                f"config/safety.yaml joint_limits_rad lists {tuple(limits)}, "
                f"config/robot.yaml lists {names}; they must match in name and order"
            )
        lower = np.empty(JOINT_DIM, dtype=np.float64)
        upper = np.empty(JOINT_DIM, dtype=np.float64)
        for i, name in enumerate(names):
            lo, hi = (float(v) for v in limits[name])
            if hi <= lo:
                raise config.ConfigError(f"config/safety.yaml: joint_limits_rad[{name}] has upper <= lower")
            mech_lo, mech_hi = mechanical[name]
            if lo < mech_lo or hi > mech_hi:
                raise config.ConfigError(
                    f"config/safety.yaml: joint_limits_rad[{name}] = [{lo}, {hi}] is wider than the "
                    f"mechanical range [{mech_lo}, {mech_hi}] in config/robot.yaml"
                )
            lower[i], upper[i] = lo, hi

        # The waist clamp is applied on top of the joint limit; the effective limit is the tighter.
        clamp = float(safety["waist_yaw_clamp_rad"])
        if clamp <= 0:
            raise config.ConfigError("config/safety.yaml: waist_yaw_clamp_rad must be positive")
        waist = names.index("waist_yaw_joint")
        lower[waist] = max(lower[waist], -clamp)
        upper[waist] = min(upper[waist], clamp)

        box = safety["workspace_box_m"]
        margin = float(box["margin_m"])
        if margin < 0:
            raise config.ConfigError("config/safety.yaml: workspace_box_m.margin_m must be >= 0")
        box_min = np.asarray([float(v) for v in box["min"]], dtype=np.float64) + margin
        box_max = np.asarray([float(v) for v in box["max"]], dtype=np.float64) - margin
        if box_min.shape != (3,) or box_max.shape != (3,):
            raise config.ConfigError("config/safety.yaml: workspace_box_m.min/max must hold 3 numbers")

        hand = safety["hand"]
        return cls(
            names=names,
            lower=lower,
            upper=upper,
            box_min=box_min,
            box_max=box_max,
            box_frame=str(box["frame"]),
            box_point=str(box["point"]),
            velocity_limit_rad_s=float(safety["joint_velocity_limit_rad_s"]),
            command_rate_limit_hz=float(safety["command_rate_limit_hz"]),
            command_gap_reset_s=float(safety["command_gap_reset_s"]),
            first_command_max_step_rad=float(safety["first_command_max_step_rad"]),
            watchdog_timeout_s=float(safety["watchdog_timeout_s"]),
            pinch_range=(float(hand["pinch_scalar_range"][0]), float(hand["pinch_scalar_range"][1])),
            pinch_rate_limit_per_s=float(hand["pinch_rate_limit_per_s"]),
            fk=fk,
        )

    # -- state -------------------------------------------------------------------------------------

    @property
    def min_period_s(self) -> float:
        """Shortest interval between two accepted commands."""
        return 1.0 / self.command_rate_limit_hz

    def reset(self) -> None:
        """Forget the last accepted command; the next one is checked against the measured state."""
        self._last_ns = None
        self._last_joints = None
        self._last_pinch = None

    # -- the check ---------------------------------------------------------------------------------

    def check(self, cmd: MotionCommand, state: RobotState, now_ns: int | None = None) -> MotionCommand:
        """Return the command to send, or raise :class:`SafetyViolation`.

        Order: command rate, finiteness, joint and waist clamping, pinch range and slew, the
        first-command step cap, joint velocity, workspace box. Clamping happens before the step,
        velocity and box checks, so what is checked is exactly what would be sent. The returned
        command's ``clamped`` names every field that was pulled in. The last accepted command is
        recorded only when every check passed.
        """
        now_ns = clock.now_ns() if now_ns is None else int(now_ns)

        # 1. rate limit
        if self._last_ns is not None:
            dt_s = (now_ns - self._last_ns) / 1e9
            if dt_s < self.min_period_s:
                raise self._reject(
                    "command_rate",
                    f"command {dt_s * 1e3:.3f} ms after the previous one; "
                    f"limit {self.command_rate_limit_hz:g} Hz = {self.min_period_s * 1e3:.3f} ms",
                )

        # 2. finiteness: NaN survives np.clip and would be sent as a joint target
        target = cmd.joints
        if not np.all(np.isfinite(target)) or not np.isfinite(cmd.pinch):
            raise self._reject(
                "non_finite", f"command holds a non-finite value: joints={target.tolist()}, pinch={cmd.pinch}"
            )
        if not np.all(np.isfinite(state.joints)):
            raise self._reject("non_finite", f"state holds a non-finite value: joints={state.joints.tolist()}")

        # 3. joint limits and the waist clamp (already folded into lower/upper by from_config)
        clipped = np.clip(target, self.lower, self.upper)
        clamped = [name for i, name in enumerate(self.names) if clipped[i] != target[i]]

        # 4. pinch range, then pinch slew against the last accepted pinch
        pinch = float(np.clip(cmd.pinch, self.pinch_range[0], self.pinch_range[1]))
        if pinch != cmd.pinch:
            clamped.append("pinch_scalar")
        if self._last_pinch is not None and self._last_ns is not None:
            dt_s = max((now_ns - self._last_ns) / 1e9, 0.0)
            step = self.pinch_rate_limit_per_s * dt_s
            limited = float(np.clip(pinch, self._last_pinch - step, self._last_pinch + step))
            if limited != pinch:
                pinch = limited
                clamped.append("pinch_rate")

        # 5. joint velocity, against the previous accepted command, or against the measured state when
        #    the previous command is older than command_gap_reset_s (config/safety.yaml).
        reference, dt_s, source, fresh = self._velocity_reference(state, now_ns)

        # 5a. a fresh reference means nobody is tracking this arm yet: the first command of a stream,
        #     or the first after a gap. It may not step the arm at all (D-018). The velocity rule
        #     alone would allow velocity_limit * gap_reset here, which is a lurch, not a step.
        if fresh:
            step = np.abs(clipped - reference)
            worst = int(np.argmax(step))
            if step[worst] > self.first_command_max_step_rad:
                raise self._reject(
                    "first_command_step",
                    f"{self.names[worst]} would step {step[worst]:.4f} rad from the measured state on a "
                    f"command with a fresh reference (limit first_command_max_step_rad "
                    f"{self.first_command_max_step_rad:g} rad); a stream engages from where the arm is",
                )
        speed = np.abs(clipped - reference) / dt_s
        worst = int(np.argmax(speed))
        if speed[worst] > self.velocity_limit_rad_s:
            raise self._reject(
                "joint_velocity",
                f"{self.names[worst]} would move {abs(clipped[worst] - reference[worst]):.4f} rad in "
                f"{dt_s * 1e3:.1f} ms = {speed[worst]:.3f} rad/s (limit {self.velocity_limit_rad_s:g} rad/s, "
                f"reference: {source})",
            )

        # 6. workspace box on the commanded point
        point = self._forward(clipped)
        if np.any(point < self.box_min) or np.any(point > self.box_max):
            raise self._reject(
                "workspace_box",
                f"{self.box_point} at {np.round(point, 4).tolist()} m is outside the box "
                f"{self.box_min.tolist()} .. {self.box_max.tolist()} in frame {self.box_frame}",
            )

        self._last_ns = now_ns
        self._last_joints = clipped
        self._last_pinch = pinch
        if clamped:
            _log.debug("safety_clamp", fields=tuple(clamped))
        return MotionCommand(arm=clipped[:-1], waist_yaw=float(clipped[-1]), pinch=pinch, clamped=tuple(clamped))

    def _velocity_reference(self, state: RobotState, now_ns: int) -> tuple[np.ndarray, float, str, bool]:
        """``(reference joints, dt seconds, description, fresh)`` for the velocity check.

        Normally the previous accepted command and the monotonic time since it. After a gap longer
        than ``command_gap_reset_s`` -- and for the first command of a stream -- the reference is the
        measured state, aged by exactly ``command_gap_reset_s``, and ``fresh`` is True: such a
        command is capped by ``first_command_max_step_rad`` instead, which is far tighter than the
        ``velocity_limit * gap_reset`` the velocity rule alone would allow (D-018).
        """
        if self._last_ns is not None and self._last_joints is not None:
            dt_s = (now_ns - self._last_ns) / 1e9
            if 0 < dt_s <= self.command_gap_reset_s:
                return self._last_joints, dt_s, f"previous accepted command {dt_s * 1e3:.1f} ms ago", False
        gap = self.command_gap_reset_s
        return state.joints, gap, f"measured state, fresh after {gap:g} s", True

    def _forward(self, joints: np.ndarray) -> np.ndarray:
        """The workspace-box point for ``joints``. Fails closed when fk is missing or misbehaves."""
        if self.fk is None:
            raise self._reject(
                "workspace_box",
                "no forward kinematics injected, so the workspace box cannot be checked "
                "(Envelope.from_config() injects runtime.fk.left_arm_fk by default)",
            )
        try:
            point = np.asarray(self.fk(joints), dtype=np.float64).reshape(-1)
        except SafetyViolation:
            raise
        except Exception as exc:
            raise self._reject("workspace_box", f"forward kinematics raised {type(exc).__name__}: {exc}") from exc
        if point.shape != (3,) or not np.all(np.isfinite(point)):
            raise self._reject("workspace_box", f"forward kinematics returned {point!r}, expected 3 finite metres")
        return point

    def _reject(self, rule: str, message: str) -> SafetyViolation:
        _log.warning("safety_reject", rule=rule, detail=message)
        return SafetyViolation(rule, message)


# --------------------------------------------------------------------------------------------------
# The two together
# --------------------------------------------------------------------------------------------------


class Guard:
    """The only door to the robot: session gate (R1) plus envelope (R3).

    ``simulated=True`` is for ``drivers/mock`` and for simulated robots (R1); it skips the session
    gate and nothing else. It is keyword-only and there is deliberately no other way to reach that
    behaviour: no environment variable, no config key, no "dev mode".
    """

    def __init__(self, gate: SessionGate, envelope: Envelope, *, simulated: bool = False) -> None:
        self.gate = gate
        self.envelope = envelope
        self.simulated = bool(simulated)
        self._admitted = 0

    def __repr__(self) -> str:
        return f"Guard(gate={self.gate!r}, simulated={self.simulated}, admitted={self._admitted})"

    @classmethod
    def from_config(
        cls,
        fk: FkFn | None = None,
        *,
        simulated: bool = False,
        root: Path | str | None = None,
        session_path: Path | str | None = None,
    ) -> Guard:
        """Build gate and envelope from ``config/`` in one call."""
        return cls(
            SessionGate(session_path, config_root=root),
            Envelope.from_config(fk, root=root),
            simulated=simulated,
        )

    @property
    def admitted(self) -> int:
        """How many commands this guard has let through."""
        return self._admitted

    def session_status(self) -> SessionStatus:
        """The current session status. On a simulated robot no session is needed and none is faked."""
        return self.gate.status()

    def admit(self, cmd: MotionCommand, state: RobotState, now_ns: int | None = None) -> MotionCommand:
        """Return the command a driver may send, or raise :class:`SafetyViolation`.

        The session gate runs first (unless ``simulated``), then the envelope, always.
        """
        if not self.simulated:
            status = self.gate.status()
            if not status.valid:
                _log.warning("safety_reject", rule="session_gate", detail=status.reason)
                raise SafetyViolation("session_gate", status.reason)
        out = self.envelope.check(cmd, state, now_ns)
        self._admitted += 1
        return out
