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

Two clocks end a primitive that does not end itself, and they are not the same clock (Phase 5):
``runtime.primitive_timeout_s`` bounds how long a primitive may take, and the :class:`Watchdog`
bounds how long it may achieve nothing -- ``runtime.watchdog_stall_s`` without an increase in
:meth:`board.perception.Perception.progress`. A watchdog halt stops sending policy actions, asks the
arm for exactly its own measured state (a hold, not a retreat: R2 forbids a pose of ours), and
reports ``policy_stalled`` to the engine, whose retry machinery takes it from there. Every execution,
halted or not, leaves one JSON line in ``data/logs/controller_<session>.trials.jsonl``
(:class:`~runtime.run_report.TrialLog`), which is what ``eval/run_eval.py`` cross-checks its own
numbers against.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from board.perception import FailureMode, MockPerception, Perception, state_delta
from drivers.interfaces import ArmDriver, CameraDriver, HandDriver
from engine.interface import Command, EngineClient, Outcome
from runtime import clock, config
from runtime.goal import GoalRenderer
from runtime.log import get_logger
from runtime.policy_api import ActionChunk, HoldPolicy, Observation, Policy
from runtime.run_report import Mark, RunSummary, TrialLog
from runtime.safety import REPO_ROOT, SafetyViolation
from runtime.types import MotionCommand

__all__ = ["Controller", "RunSummary", "Watchdog", "build", "main"]

#: Where the heartbeat and the per-trial log go (git-ignored, CLAUDE.md section 7).
LOG_DIR: Path = REPO_ROOT / "data" / "logs"
#: Samples kept per stream for alignment: seconds of history, far more than the tolerance needs.
BUFFER = 512
#: The streams an observation is built from (5.3). ``palm`` comes from the hand, not a camera driver.
STREAMS: tuple[str, ...] = ("top", "oblique", "palm", "state", "hand")


class Watchdog:
    """Halts a primitive that is achieving nothing (CLAUDE.md Phase 5, 6.5 "policy stalls").

    It is a few numbers sampled inside the controller's own tick: no thread, no timer, nothing that
    can fire while the loop is elsewhere. Once per ``interval_ns`` it reads a progress signal from
    perception; it remembers the last time that signal *increased*; when ``stall_ns`` has passed
    without an increase, :meth:`stalled` is true and the loop stops.

    Only the direction of the signal is read, never its magnitude, so nothing has to be calibrated
    and a perception that can only say "something changed" is already enough. The tie with the
    primitive timeout is deliberate: the loop tests :meth:`stalled` before its own deadline, so a
    primitive that ends with no progress for ``stall_ns`` is reported as a stall rather than as a
    plain timeout, which is the more specific of the two true statements.
    """

    def __init__(self, interval_ns: int, stall_ns: int, started_ns: int) -> None:
        self.interval_ns = int(interval_ns)
        self.stall_ns = int(stall_ns)
        self.started_ns = int(started_ns)
        self.last_increase_ns = int(started_ns)
        self.next_sample_ns = int(started_ns)
        self.value = 0.0
        self.samples = 0

    def __repr__(self) -> str:
        return f"Watchdog(value={self.value:.3f}, samples={self.samples}, stall_s={self.stall_ns / 1e9:g})"

    def sample(self, now_ns: int, read: Callable[[], float]) -> None:
        """Read the progress signal if this tick is on the sampling grid, and note any increase."""
        if now_ns < self.next_sample_ns:
            return
        self.next_sample_ns = now_ns + self.interval_ns
        self.samples += 1
        value = float(read())
        if value > self.value:
            self.value, self.last_increase_ns = value, now_ns

    def stalled(self, now_ns: int) -> bool:
        """Has the signal failed to increase for ``stall_ns``?"""
        return now_ns - self.last_increase_ns >= self.stall_ns

    def verdict(self, now_ns: int) -> dict[str, Any]:
        """What the watchdog saw, for the trial log and the ``primitive_end`` line."""
        return {"stalled": self.stalled(now_ns), "samples": self.samples, "progress": round(self.value, 4),
                "s_since_increase": round((now_ns - self.last_increase_ns) / 1e9, 3)}


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
        self.watchdog_interval_ns = round(float(training["runtime"]["watchdog_interval_s"]) * 1e9)
        self.watchdog_stall_ns = round(float(training["runtime"]["watchdog_stall_s"]) * 1e9)
        self.session = session or datetime.now().strftime("%Y%m%dT%H%M%S")
        directory = Path(LOG_DIR if log_dir is None else log_dir)
        self.heartbeat_path = directory / f"controller_{self.session}.heartbeat"
        self.trials = TrialLog(directory / f"controller_{self.session}.trials.jsonl")
        self.summary = RunSummary()
        self.watchdog = Watchdog(self.watchdog_interval_ns, self.watchdog_stall_ns, self._now())
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

    def execute_command(self, command: Command, deadline_ns: int | None = None,
                        before: dict[str, Any] | None = None) -> str:
        """Run one primitive. Why it stopped: ``policy_done``, ``watchdog``, ``timeout``, ``run_deadline``.

        ``before`` is the board as the primitive started, against which the watchdog measures
        progress; it is read from the engine when the caller does not pass one.
        """
        self._policy.reset(command)
        start = self._now()
        timeout_at = start + self.timeout_ns
        end = timeout_at if deadline_ns is None else min(timeout_at, deadline_ns)
        stopped = "timeout" if end == timeout_at else "run_deadline"
        board = self._engine.board_state() if before is None else before
        self.watchdog = Watchdog(self.watchdog_interval_ns, self.watchdog_stall_ns, start)
        next_policy = next_action = next_beat = start
        chunk: ActionChunk | None = None
        index = 0
        while True:
            now = self._now()
            # Before the deadline test, so that a primitive which reached both ends at once is
            # reported as the stall it is rather than as a plain timeout.
            if self.watchdog.stalled(now):
                stopped = "watchdog"
                break
            if now >= end:
                break
            self.watchdog.sample(now, lambda: self._perception.progress(command, board, self._engine.board_state()))
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
                       elapsed_s=round((self._now() - start) / 1e9, 3), **self.watchdog.verdict(self._now()))
        return stopped

    def hold(self) -> None:
        """Ask arm and hand for exactly their own measured state, through the guard.

        What a halted primitive gets instead of more policy actions. It is a *reading*, not a pose:
        R2 forbids this module from owning a joint target, so the only target it may ever construct
        is the one the robot is already at.
        """
        arm, hand = self._arm.read_state().payload, self._hand.read_state().payload
        self.send(np.concatenate([arm.arm, [arm.waist_yaw, hand.pinch]]))
        # The hold occupies one action slot: without the wait the next primitive's first action would
        # arrive in the same instant and the guard's rate limit would (rightly) refuse it.
        self._sleep_until(self._now() + self.action_period_ns)

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

    def _outcome(self, command: Command, before: dict[str, Any], stopped: str) -> Outcome:
        """What is reported to the engine: perception's verdict, or the watchdog's over it.

        A watchdog halt is not perception's to judge -- the primitive was stopped, not finished --
        so the loop labels it ``policy_stalled`` itself (6.5) and hands the arm a hold first. The
        delta is still whatever the board shows, so a stall that did change something says so.
        """
        after = self._engine.board_state()
        if stopped != "watchdog":
            return self._perception.verify(command, before, after)
        self.hold()
        self._log.warning("watchdog_halt", primitive=command.primitive.value,
                          **self.watchdog.verdict(self._now()))
        return Outcome(success=False, observed_state_delta=state_delta(before, after),
                       failure_mode=FailureMode.POLICY_STALLED.value)

    def run(self, seconds: float | None = None, max_commands: int | None = None) -> RunSummary:
        """Play commands until the engine runs out, the time is up, or ``max_commands`` is reached.

        ``seconds`` and ``max_commands`` bound *this* call; the returned :class:`RunSummary` is the
        controller's own and accumulates over every run it has made.
        """
        start = self._now()
        end = None if seconds is None else start + round(seconds * 1e9)
        s, played = self.summary, 0
        self._log.info("run_start", seconds=seconds, max_commands=max_commands,
                       heartbeat=str(self.heartbeat_path), trials=str(self.trials.path))
        while (end is None or self._now() < end) and (max_commands is None or played < max_commands):
            command = self._engine.next_command()
            if command is None:
                self._log.info("engine_exhausted")
                break
            before = self._engine.board_state()
            mark = Mark.of(s, self._now())
            stopped = self.execute_command(command, end, before)
            outcome = self._outcome(command, before, stopped)
            self._engine.report(outcome)
            s.commands, played = s.commands + 1, played + 1
            s.primitives[command.primitive.value] += 1
            s.stopped_by[stopped] += 1
            s.succeeded += int(outcome.success)
            if not outcome.success:
                s.failure_modes[outcome.failure_mode or "unlabelled"] += 1
            now = self._now()
            self.trials.record(session=self.session, index=s.commands - 1, ts_ns=now, command=command,
                               outcome=outcome, stopped_by=stopped, cost=mark.cost(s, now),
                               watchdog=self.watchdog.verdict(now))
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
    print(f"  trial log          {controller.trials.path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
