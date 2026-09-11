"""The teleop loop: operator in, robot out, one tick per 30 Hz frame (CLAUDE.md 5.2, 5.3, Phase 2).

This is the path every training episode comes from, so it is deliberately small. One tick, in order:
read the Pico pose and the glove (read-only, allowed with no session, R1); express the pose in the
pelvis frame with :func:`teleop.retarget.pico_to_g1_base`; solve the 8 commanded joints with
:class:`teleop.retarget.ArmIK` seeded from the arm's *measured* state, so the solver tracks the robot
and not its own last answer; send that plus the glove's pinch scalar through ``arm.send_targets`` and
``hand.send_pinch``, which admit through :meth:`runtime.safety.Guard.admit` (R1, R3); hand the
**admitted** command to the operator UI, which gives it to the recorder while an episode is open, so
the dataset holds what the robot was told and never a target that was refused (R5).

A :class:`~runtime.safety.SafetyViolation` is counted by rule, logged, and the tick continues: the
envelope is the limiter, not a crash. Nothing here is a trajectory of its own -- every number sent
came out of the operator's hand through the IK (R2) -- and nothing here touches the human-written
session file (R1): on hardware the guard is what decides, and with no session it decides no.

No threads: time comes from the injected ``now_ns`` and ``sleep_until``, so the tests run a 30 s
session on a fake clock in a second. The grid is the recorder's own
(:meth:`teleop.recorder.Recorder.next_grid_ns`, phase-locked to the board camera) when a recorder is
attached, and a plain period otherwise. docs/teleop.md, "The teleop loop", has the wiring, the
engage-without-a-clutch measurement, and what is still missing (T-020, T-021).
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from drivers.interfaces import ArmDriver, CameraDriver, GloveDriver, HandDriver, PoseDriver
from engine.interface import EngineClient
from runtime import clock, config
from runtime.log import configure, get_logger
from runtime.safety import SafetyViolation
from runtime.types import ARM_DOF, MotionCommand
from teleop.operator_ui import OperatorUI
from teleop.recorder import Recorder
from teleop.retarget import ArmIK, pico_to_g1_base

__all__ = ["LoopStats", "TeleopLoop", "build", "main"]


def _sleep_until(ts_ns: int) -> None:
    """Real-time pacing. The tests inject a fake clock that jumps instead of sleeping."""
    delta_s = (ts_ns - clock.now_ns()) / 1e9
    if delta_s > 0:
        time.sleep(delta_s)


@dataclass
class LoopStats:
    """What one run did. Every number here is counted, never described (R5)."""

    ticks: int = 0
    sent: int = 0
    frames: int = 0
    elapsed_s: float = 0.0
    refused: Counter = field(default_factory=Counter)
    #: One entry per tick: the wall time :meth:`teleop.retarget.ArmIK.solve` took, milliseconds.
    ik_ms: list[float] = field(default_factory=list)

    @property
    def hz(self) -> float:
        """Measured tick rate of the run."""
        return self.ticks / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def ik_mean_ms(self) -> float:
        return float(np.mean(self.ik_ms)) if self.ik_ms else 0.0

    @property
    def ik_p99_ms(self) -> float:
        return float(np.percentile(self.ik_ms, 99)) if self.ik_ms else 0.0

    def lines(self) -> list[str]:
        """The printed summary of a run, one fact per line."""
        by_rule = ", ".join(f"{k}={v}" for k, v in sorted(self.refused.items())) or "none"
        return [
            f"elapsed          {self.elapsed_s:.2f} s",
            f"ticks            {self.ticks} = {self.hz:.2f} Hz",
            f"commands sent    {self.sent}",
            f"frames recorded  {self.frames}",
            f"safety refusals  {sum(self.refused.values())} ({by_rule})",
            f"ik solve         mean {self.ik_mean_ms:.3f} ms, p99 {self.ik_p99_ms:.3f} ms",
        ]


class TeleopLoop:
    """One operator, one arm, one hand, one clock. Everything it talks to is injected.

    ``recorder`` and ``ui`` are optional: without them the loop is a live teleop link that records
    nothing. Given a ``recorder``, an ``engine`` and ``cameras`` but no ``ui``, it builds the
    :class:`~teleop.operator_ui.OperatorUI` that puts the two together, because that is the only
    wiring the operator console has. ``cameras`` is otherwise polled once a tick when no recorder is
    attached, so every stream still runs at the grid rate; the recorder polls them itself.
    """

    def __init__(
        self,
        *,
        pose_driver: PoseDriver,
        glove_driver: GloveDriver,
        arm: ArmDriver,
        hand: HandDriver,
        cameras: Mapping[str, CameraDriver] | None = None,
        engine: EngineClient | None = None,
        recorder: Recorder | None = None,
        ui: OperatorUI | None = None,
        now_ns: Callable[[], int] = clock.now_ns,
        sleep_until: Callable[[int], None] = _sleep_until,
        ik: ArmIK | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        self._pose, self._glove, self._arm, self._hand = pose_driver, glove_driver, arm, hand
        self._cameras = dict(cameras or {})
        self._now, self._sleep_until, self._config_root = now_ns, sleep_until, config_root
        self.period_ns = round(1e9 / float(config.load("training", root=config_root)["rates"]["action_hz"]))
        self.ik = ArmIK(root=config_root) if ik is None else ik
        self.recorder = recorder
        if ui is None and recorder is not None and engine is not None and self._cameras:
            ui = OperatorUI(engine=engine, recorder=recorder, cameras=self._cameras,
                            now_ns=now_ns, config_root=config_root)
        self.ui = ui
        self.stats = LoopStats()
        self.last_admitted: MotionCommand | None = None  # the last command the guard admitted
        self._log = get_logger("teleop.loop")

    def __repr__(self) -> str:
        return (f"TeleopLoop(hz={1e9 / self.period_ns:g}, recorder={self.recorder is not None}, "
                f"ui={self.ui is not None}, ticks={self.stats.ticks})")

    def tick(self) -> MotionCommand | None:
        """Read the operator, solve, send, record. Returns the admitted command, or None if refused."""
        pose = self._pose.read().payload
        glove = self._glove.read().payload
        position, quat = pico_to_g1_base(pose.position_m, pose.quat_xyzw, root=self._config_root)
        measured = self._arm.read_state().payload.joints
        started_ns = time.perf_counter_ns()
        joints = self.ik.solve(position, quat, measured)
        self.stats.ik_ms.append((time.perf_counter_ns() - started_ns) / 1e6)
        self.stats.ticks += 1
        admitted = self._send(MotionCommand(arm=joints[:ARM_DOF], waist_yaw=joints[ARM_DOF], pinch=glove.pinch))
        self._observe(admitted)
        return admitted

    def _send(self, cmd: MotionCommand) -> MotionCommand | None:
        """Both devices, both through their own guard (R1, R3). A refusal is counted, never fatal."""
        try:
            admitted = self._arm.send_targets(cmd)
        except SafetyViolation as exc:
            self._refused("arm", exc)
            return None
        try:
            self._hand.send_pinch(admitted.pinch)
        except SafetyViolation as exc:
            self._refused("hand", exc)
        self.stats.sent += 1
        self.last_admitted = admitted
        return admitted

    def _refused(self, device: str, exc: SafetyViolation) -> None:
        self.stats.refused[exc.rule] += 1
        self._log.warning("safety_refused", device=device, rule=exc.rule, detail=exc.message)

    def _observe(self, admitted: MotionCommand | None) -> None:
        """Give the admitted command to the UI (hence the recorder), or just keep the streams warm."""
        wrote = False
        if admitted is not None:
            if self.ui is not None:  # the UI passes it on only while an episode is recording
                wrote = self.ui.tick(admitted)
            elif self.recorder is not None and self.recorder.episode is not None:
                wrote = self.recorder.tick(admitted)
        self.stats.frames += int(wrote)
        if wrote:
            return
        # Read-only, allowed with no session (R1): the buffers the next frame is aligned from.
        if self.recorder is not None:
            self.recorder.poll()
        else:
            for camera in self._cameras.values():
                camera.grab()

    def _next_grid_ns(self, grid_ns: int, now_ns: int) -> int:
        """The next tick instant: the recorder's frame grid when it has one, else one period on.

        An over-running tick drops the instants it missed rather than firing catch-up commands into
        the rate limiter (the rule ``runtime/controller.py`` follows too)."""
        if self.recorder is not None:
            ripe = self.recorder.next_grid_ns(now_ns)
            if ripe is not None:
                return ripe
        grid_ns += self.period_ns
        return grid_ns if grid_ns > now_ns else now_ns + self.period_ns

    def run(self, seconds: float) -> LoopStats:
        """Tick on the grid for ``seconds``. The returned stats accumulate over every run."""
        start = grid_ns = self._now()
        end = start + round(seconds * 1e9)
        self._log.info("run_start", seconds=seconds, hz=round(1e9 / self.period_ns, 3))
        while self._now() < end:
            self.tick()
            grid_ns = self._next_grid_ns(grid_ns, self._now())
            self._sleep_until(grid_ns)
            if self.ui is not None and self.ui.quit_requested:
                break
        self.stats.elapsed_s += (self._now() - start) / 1e9
        self._log.info("run_end", ticks=self.stats.ticks, hz=round(self.stats.hz, 3),
                       sent=self.stats.sent, refused=sum(self.stats.refused.values()))
        return self.stats


def build(backend: str = "mock", **kwargs) -> TeleopLoop:
    """Wire a loop onto one backend. ``backend="real"`` raises from :func:`drivers.make` (Phase 1)."""
    from drivers import make

    passthrough = {k: kwargs.pop(k) for k in ("config_root", "now_ns") if kwargs.get(k) is not None}
    made = {n: make(n, backend, **passthrough) for n in ("pose", "glove", "arm", "hand", "top", "oblique")}
    return TeleopLoop(pose_driver=made["pose"], glove_driver=made["glove"], arm=made["arm"], hand=made["hand"],
                      cameras={n: made[n] for n in ("top", "oblique")}, **passthrough, **kwargs)


def main(argv: list[str] | None = None) -> int:
    """``python -m teleop.loop --backend mock --seconds 10``."""
    parser = argparse.ArgumentParser(description="Run the 30 Hz teleop loop (CLAUDE.md 5.2, Phase 2).")
    parser.add_argument("--backend", default="mock", help="mock (default) or real; real refuses until Phase 1")
    parser.add_argument("--seconds", type=float, default=10.0, help="run length in seconds (default 10)")
    parser.add_argument("--json-logs", action="store_true", help="JSON log lines instead of key=value")
    args = parser.parse_args(argv)
    configure(json=args.json_logs)
    try:
        loop = build(args.backend)
    except NotImplementedError as exc:
        print(f"cannot run on backend {args.backend!r}: {exc}")
        return 2
    stats = loop.run(args.seconds)
    print(f"\nteleop loop summary (backend {args.backend})")
    for line in stats.lines():
        print(f"  {line}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
