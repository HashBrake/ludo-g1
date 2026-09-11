"""The control loop of CLAUDE.md 5.5: engine command in, learned actions out, outcome reported back.

``cmd = engine.next_command()`` -> ``policy.reset(cmd)`` -> observe, act, send until the policy says
it is done or the timeout fires -> ``perception.verify`` -> ``engine.report(outcome)``. Recovery and
retries are the engine's business (5.5); this loop never invents a command of its own and never
produces a motion target of its own (R2): every number it sends came out of the policy.

Rates (5.2, ``config/training.yaml``): the policy is asked for a chunk at ``rates.policy_hz`` (10 Hz)
and the chunk is played out at ``rates.action_hz`` (30 Hz), never past ``diffusion.execute`` (8) of
its 16 entries -- the receding horizon. Every send goes through ``drivers/``, which admit through
:meth:`runtime.safety.Guard.admit` (R1, R3); a :class:`~runtime.safety.SafetyViolation` is counted and
logged and the loop continues, because the envelope is the limiter and not a crash. No threads, and
time comes from injected callables, so the tests run the loop on a fake clock. See docs/controller.md.
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from board.perception import MockPerception, Perception
from drivers.interfaces import ArmDriver, CameraDriver, HandDriver
from engine.interface import Command, EngineClient
from runtime import clock, config
from runtime.goal import GoalRenderer
from runtime.log import get_logger
from runtime.policy_api import ActionChunk, HoldPolicy, Observation, Policy
from runtime.safety import REPO_ROOT, SafetyViolation
from runtime.types import MotionCommand

__all__ = ["Controller", "RunSummary", "build", "main"]

#: Where heartbeats go (git-ignored, CLAUDE.md section 7).
LOG_DIR: Path = REPO_ROOT / "data" / "logs"
#: Samples kept per stream for alignment: seconds of history, far more than the tolerance needs.
BUFFER = 512
#: The streams an observation is built from (5.3). ``palm`` comes from the hand, not a camera driver.
STREAMS: tuple[str, ...] = ("top", "oblique", "palm", "state", "hand")


def _counts(counter: Counter) -> str:
    """``a=1, b=2`` for one printed summary line, or ``none``."""
    return ", ".join(f"{k}={v}" for k, v in sorted(counter.items())) or "none"


@dataclass
class RunSummary:
    """What one run did. Every number here is counted, never described (R5)."""

    commands: int = 0
    succeeded: int = 0
    policy_calls: int = 0
    actions_sent: int = 0
    refused: int = 0
    align_failures: int = 0
    elapsed_s: float = 0.0
    primitives: Counter = field(default_factory=Counter)
    failure_modes: Counter = field(default_factory=Counter)
    stopped_by: Counter = field(default_factory=Counter)

    @property
    def policy_hz(self) -> float:
        """Measured rate of the 10 Hz loop: policy calls per second of run time."""
        return self.policy_calls / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def action_hz(self) -> float:
        """Measured rate of the 30 Hz action stream."""
        return self.actions_sent / self.elapsed_s if self.elapsed_s > 0 else 0.0

    def lines(self) -> list[str]:
        """The printed summary of a run, one fact per line."""
        return [
            f"elapsed            {self.elapsed_s:.2f} s",
            f"commands executed  {self.commands} ({_counts(self.primitives)})",
            f"outcomes           {self.succeeded} success, {self.commands - self.succeeded} failure",
            f"failure modes      {_counts(self.failure_modes)}",
            f"stopped by         {_counts(self.stopped_by)}",
            f"policy calls       {self.policy_calls} = {self.policy_hz:.2f} Hz",
            f"actions sent       {self.actions_sent} = {self.action_hz:.2f} Hz",
            f"safety refusals    {self.refused}",
            f"alignment failures {self.align_failures}",
        ]


def _sleep_until(ts_ns: int) -> None:
    """Real-time pacing. The tests inject a fake clock that jumps instead of sleeping."""
    delta_s = (ts_ns - clock.now_ns()) / 1e9
    if delta_s > 0:
        time.sleep(delta_s)


def _bump(grid_ns: int, period_ns: int, now_ns: int) -> int:
    """The next grid point strictly after ``now``, which must be read *after* the step's work.

    A step that over-ran its period drops the grid points it missed rather than firing a burst of
    catch-up commands into the rate limiter.
    """
    grid_ns += period_ns
    return grid_ns if grid_ns > now_ns else now_ns + period_ns


class Controller:
    """One 10 Hz loop over one engine, one policy and one set of drivers.

    Everything it talks to is injected, so the same object runs against mocks on a fake clock and
    against the Phase 1 drivers on the real one. It owns no device and opens no connection.
    """

    def __init__(
        self,
        *,
        arm: ArmDriver,
        hand: HandDriver,
        cameras: Mapping[str, CameraDriver],
        engine: EngineClient,
        policy: Policy,
        perception: Perception,
        goal: GoalRenderer | None = None,
        now_ns: Callable[[], int] = clock.now_ns,
        sleep_until: Callable[[int], None] = _sleep_until,
        session: str | None = None,
        log_dir: Path | str | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        missing = [n for n in ("top", "oblique") if n not in cameras]
        if missing:
            raise ValueError(f"Controller needs the {missing} camera(s); got {sorted(cameras)}")
        training = config.load("training", root=config_root)
        self._arm, self._hand, self._cameras = arm, hand, dict(cameras)
        self._engine, self._policy, self._perception = engine, policy, perception
        self._goal = GoalRenderer(config_root=config_root) if goal is None else goal
        self._now, self._sleep_until = now_ns, sleep_until
        self.policy_period_ns = round(1e9 / float(training["rates"]["policy_hz"]))
        self.action_period_ns = round(1e9 / float(training["rates"]["action_hz"]))
        self.execute = int(training["diffusion"]["execute"])
        self.timeout_ns = round(float(training["runtime"]["primitive_timeout_s"]) * 1e9)
        self.tolerance_ns = round(float(training["runtime"]["alignment_tolerance_ms"]) * 1e6)
        self.heartbeat_ns = round(float(training["runtime"]["heartbeat_s"]) * 1e9)
        self.session = session or datetime.now().strftime("%Y%m%dT%H%M%S")
        self.heartbeat_path = Path(LOG_DIR if log_dir is None else log_dir) / f"controller_{self.session}.heartbeat"
        self.summary = RunSummary()
        self._goal_cache: tuple[Command, np.ndarray, np.ndarray] | None = None
        self._streams = {name: clock.StreamBuffer(name, BUFFER) for name in STREAMS}
        self._log = get_logger("runtime.controller", session=self.session)

    def __repr__(self) -> str:
        return f"Controller(session={self.session!r}, policy={self._policy!r}, engine={type(self._engine).__name__})"

    def sample(self) -> None:
        """Poll every stream once, dropping a sample the stream has already given.

        Called right before each :meth:`observe` (its only consumer), so every stream then holds a
        sample no older than its own frame period. Read-only: allowed with no session, always (R1).
        """
        got: dict[str, clock.Stamped] = {name: cam.grab() for name, cam in self._cameras.items()}
        got["palm"], got["state"] = self._hand.palm_frame(), self._arm.read_state()
        got["hand"] = self._hand.read_state()
        for name, stamped in got.items():
            buf = self._streams[name]
            if not len(buf) or stamped.ts_ns > buf.latest().ts_ns:
                buf.push_stamped(stamped)

    def observe(self, command: Command, ts_ns: int) -> Observation | None:
        """The observation of 5.3 at ``ts_ns``, or None when the streams do not align in tolerance."""
        try:
            at = clock.align(self._streams, ts_ns, self.tolerance_ns)
        except clock.AlignmentError as exc:
            self.summary.align_failures += 1
            self._log.warning("observation_align_failed", detail=str(exc))
            return None
        arm, hand = at["state"].payload, at["hand"].payload
        goal, task_id = self._goal_for(command)
        return Observation(ts_ns=ts_ns, top=at["top"].payload, oblique=at["oblique"].payload,
                           palm=at["palm"].payload, goal=goal, task_id=task_id,
                           state=np.concatenate([arm.arm, [arm.waist_yaw, hand.pinch]]))

    def _goal_for(self, command: Command) -> tuple[np.ndarray, np.ndarray]:
        """Goal channels and task one-hot, rendered once per command: they depend on nothing else."""
        if self._goal_cache is None or self._goal_cache[0] is not command:
            self._goal_cache = (command, self._goal.render(command), self._goal.task_one_hot(command.primitive))
        return self._goal_cache[1], self._goal_cache[2]

    def send(self, action: np.ndarray) -> None:
        """Send one 9-D policy action to arm and hand; both admit through the guard (R1, R3)."""
        cmd = MotionCommand.from_action(action)
        sends = (("arm", lambda: self._arm.send_targets(cmd)), ("hand", lambda: self._hand.send_pinch(cmd.pinch)))
        for what, call in sends:
            try:
                call()
            except SafetyViolation as exc:
                self.summary.refused += 1
                self._log.warning("safety_refused", device=what, rule=exc.rule, detail=exc.message)
        self.summary.actions_sent += 1

    def execute_command(self, command: Command, deadline_ns: int | None = None) -> str:
        """Run one primitive. Returns why it stopped: ``policy_done``, ``timeout`` or ``run_deadline``."""
        self._policy.reset(command)
        start = self._now()
        timeout_at = start + self.timeout_ns
        end = timeout_at if deadline_ns is None else min(timeout_at, deadline_ns)
        stopped = "timeout" if end == timeout_at else "run_deadline"
        next_policy = next_action = next_beat = start
        chunk: ActionChunk | None = None
        index = 0
        while (now := self._now()) < end:
            if now >= next_policy:
                self.sample()
                observation = self.observe(command, now)
                if observation is not None:
                    if self._policy.done(observation):
                        stopped = "policy_done"
                        break
                    chunk, index = self._policy.act(observation), 0
                    self.summary.policy_calls += 1
                next_policy = _bump(next_policy, self.policy_period_ns, self._now())
            if chunk is not None and index < self.execute:
                self.send(chunk[index])
                index += 1
            if now >= next_beat:
                self._beat(command)
                next_beat = _bump(next_beat, self.heartbeat_ns, self._now())
            next_action = _bump(next_action, self.action_period_ns, self._now())
            self._sleep_until(next_action)
        self._log.info("primitive_end", primitive=command.primitive.value, stopped_by=stopped,
                       elapsed_s=round((self._now() - start) / 1e9, 3))
        return stopped

    def _beat(self, command: Command | None) -> None:
        """One heartbeat line to ``data/logs/``. Failing to write one never stops the loop."""
        s = self.summary
        line = (f"ts_ns={self._now()} session={self.session} commands={s.commands} "
                f"primitive={command.primitive.value if command else 'idle'} policy_calls={s.policy_calls} "
                f"actions={s.actions_sent} refused={s.refused}\n")
        try:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            with self.heartbeat_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            self._log.warning("heartbeat_failed", path=str(self.heartbeat_path), detail=str(exc))

    def run(self, seconds: float | None = None, max_commands: int | None = None) -> RunSummary:
        """Play commands until the engine runs out, the time is up, or ``max_commands`` is reached.

        ``seconds`` and ``max_commands`` bound *this* call; the returned :class:`RunSummary` is the
        controller's own and accumulates over every run it has made.
        """
        start = self._now()
        end = None if seconds is None else start + round(seconds * 1e9)
        s, played = self.summary, 0
        self._log.info("run_start", seconds=seconds, max_commands=max_commands, heartbeat=str(self.heartbeat_path))
        while (end is None or self._now() < end) and (max_commands is None or played < max_commands):
            command = self._engine.next_command()
            if command is None:
                self._log.info("engine_exhausted")
                break
            before = self._engine.board_state()
            stopped = self.execute_command(command, end)
            outcome = self._perception.verify(command, before, self._engine.board_state())
            self._engine.report(outcome)
            s.commands, played = s.commands + 1, played + 1
            s.primitives[command.primitive.value] += 1
            s.stopped_by[stopped] += 1
            s.succeeded += int(outcome.success)
            if not outcome.success:
                s.failure_modes[outcome.failure_mode or "unlabelled"] += 1
            self._log.info("command_done", primitive=command.primitive.value, success=outcome.success,
                           failure_mode=outcome.failure_mode, stopped_by=stopped,
                           src=None if command.src is None else command.src.id,
                           dst=None if command.dst is None else command.dst.id)
        s.elapsed_s += (self._now() - start) / 1e9
        self._log.info("run_end", commands=played, policy_hz=round(s.policy_hz, 3))
        return s


def build(backend: str = "mock", *, seed: int = 0, policy: Policy | None = None, **kwargs) -> Controller:
    """Wire a controller onto one backend: drivers, the stub engine and the placeholder pieces.

    ``backend="real"`` raises from :func:`drivers.make` until the Phase 1 drivers exist, and the
    default :class:`~runtime.policy_api.HoldPolicy` is refused on any backend but ``mock`` (R2): a
    real robot is never driven by a placeholder.
    """
    from drivers import make  # local: importing a driver costs nothing until one is built
    from engine.stub import StubEngine

    if policy is None:
        if backend != "mock":
            raise NotImplementedError(
                f"no learned policy exists yet (Phase 3, CLAUDE.md section 6); backend={backend!r} needs one, and "
                "HoldPolicy is a test double that must never be deployed (R2)"
            )
        policy = HoldPolicy()
    passthrough = {k: kwargs[k] for k in ("config_root", "now_ns") if kwargs.get(k) is not None}
    return Controller(
        arm=make("arm", backend, **passthrough),
        hand=make("hand", backend, **passthrough),
        cameras={name: make(name, backend, **passthrough) for name in ("top", "oblique")},
        engine=StubEngine(seed),
        policy=policy,
        perception=MockPerception(),
        **kwargs,
    )


def main(argv: list[str] | None = None) -> int:
    """``python -m runtime.controller --backend mock --seconds 60``."""
    parser = argparse.ArgumentParser(description="Run the 10 Hz control loop (CLAUDE.md 5.5).")
    parser.add_argument("--backend", default="mock", help="mock (default) or real; real refuses until Phase 1")
    parser.add_argument("--seconds", type=float, default=60.0, help="run length in seconds (default 60)")
    parser.add_argument("--max-commands", type=int, default=None, help="stop after this many commands")
    parser.add_argument("--seed", type=int, default=0, help="stub engine seed (default 0)")
    parser.add_argument("--session", default=None, help="session id, used for the heartbeat file name")
    parser.add_argument("--json-logs", action="store_true", help="JSON log lines instead of key=value")
    args = parser.parse_args(argv)

    from runtime.log import configure

    configure(json=args.json_logs)
    try:
        controller = build(args.backend, seed=args.seed, session=args.session)
    except NotImplementedError as exc:
        print(f"cannot run on backend {args.backend!r}: {exc}")
        return 2
    summary = controller.run(seconds=args.seconds, max_commands=args.max_commands)
    print(f"\ncontroller run summary (session {controller.session}, backend {args.backend})")
    for line in summary.lines():
        print(f"  {line}")
    print(f"  heartbeat          {controller.heartbeat_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
