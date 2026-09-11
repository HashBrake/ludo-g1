"""The operator's console during data collection: what to do next, and the keys that record it.

The engine says which primitive to execute and between which cells (CLAUDE.md 5.5); the operator has
to see that before moving and has to say afterwards whether it worked (5.6). This module is those two
things and nothing else: a state machine over one :class:`~teleop.recorder.Recorder` and one
:class:`~engine.interface.EngineClient`, plus a frame showing the board with the two goal cells marked.

The state machine, one command at a time::

    idle --(a command from the engine)--> armed --s--> recording --x--> stopped --y/n--> idle
                                           `--a--> idle                  `--p--> perturbed toggles

``y``/``n`` mark the episode and close it, ``a`` discards it, and each of those reports an
:class:`~engine.interface.Outcome`, because the engine hands out exactly one command per report (5.5)
and it is the engine, not this UI, that decides on a retry.

**Nothing here moves anything and nothing here generates a target** (R2). The teleop loop hands its
already-admitted action to :meth:`OperatorUI.tick`; this file never builds a :class:`MotionCommand`,
never touches a driver, and reads the board camera only to draw it. docs/teleop.md has the keys, the
window mode, what the frame shows and how to run it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path

import cv2
import numpy as np

from drivers.interfaces import CameraDriver
from engine.interface import Cell, Command, EngineClient, Outcome
from runtime import clock, config
from runtime.goal import GoalRenderer
from runtime.log import get_logger
from runtime.types import MotionCommand
from teleop.recorder import Recorder

__all__ = ["ABORTED", "MARKED_FAILURE", "OperatorUI", "UIState"]

#: ``Outcome.failure_mode`` for an episode the operator threw away. CLAUDE.md 6.5 has no label for
#: it because it is a collection event, not a robot failure mode, and eval never produces one.
ABORTED = "operator_aborted"
#: ``Outcome.failure_mode`` for an episode that ran to the end and that the operator marked failed.
MARKED_FAILURE = "operator_marked_failure"


class UIState(Enum):
    """Where the operator is in one command's cycle."""

    IDLE = "idle"            # no command in hand; the engine has nothing to give
    ARMED = "armed"          # a command is displayed, nothing is being recorded yet
    RECORDING = "recording"  # an episode is open and frames are being written
    STOPPED = "stopped"      # the episode is open but finished; waiting for the operator's verdict


class OperatorUI:
    """One operator session over one engine and one recorder.

    Everything is injected, so the headless tests run the whole state machine on mocks and a fake
    clock. ``headless=True`` (the default) never opens a window: :meth:`render` returns the frame as
    an array and :meth:`run_window` refuses.
    """

    def __init__(
        self,
        *,
        engine: EngineClient,
        recorder: Recorder,
        cameras: Mapping[str, CameraDriver],
        now_ns: Callable[[], int] = clock.now_ns,
        headless: bool = True,
        goal: GoalRenderer | None = None,
        config_root: Path | str | None = None,
    ) -> None:
        if "top" not in cameras:
            raise ValueError(f"OperatorUI needs the 'top' camera to draw the board; got {sorted(cameras)}")
        self._ui = ui = config.load("training", root=config_root)["operator_ui"]
        self._actions: dict[str, Callable[[], bool]] = {
            "start": self.start, "stop": self.stop, "mark_success": lambda: self.mark(True),
            "mark_failure": lambda: self.mark(False), "toggle_perturbed": self.toggle_perturbed,
            "abort": self.abort, "quit": self.quit,
        }
        self._keys: dict[str, str] = {str(key).lower(): str(action) for action, key in ui["keys"].items()}
        if sorted(self._keys.values()) != sorted(self._actions) or any(len(c) != 1 for c in self._keys):
            raise config.ConfigError(f"config/training.yaml operator_ui.keys must bind each of "
                                     f"{sorted(self._actions)} to its own single character, got {ui['keys']}")
        self._engine, self.recorder, self._top = engine, recorder, cameras["top"]
        self._now, self.headless = now_ns, bool(headless)
        self._goal = GoalRenderer(config_root=config_root) if goal is None else goal
        self._log = get_logger("teleop.operator_ui", session=recorder.session_id)
        self.state, self.command = UIState.IDLE, None
        self.perturbed = self.quit_requested = False
        self.started_ns: int | None = None
        self.stopped_ns: int | None = None
        self.aborted = 0
        self._arm()

    def __repr__(self) -> str:
        return (f"OperatorUI(state={self.state.value}, episodes={len(self.recorder.episodes)}, "
                f"aborted={self.aborted}, headless={self.headless})")

    @property
    def elapsed_s(self) -> float:
        """Seconds since the open episode started; 0 when none has."""
        return 0.0 if self.started_ns is None else ((self.stopped_ns or self._now()) - self.started_ns) / 1e9

    def _arm(self) -> None:
        """Take the next command from the engine, or go idle when it has none."""
        self.command: Command | None = self._engine.next_command()
        self.perturbed = False
        self.started_ns = self.stopped_ns = None
        self.state = UIState.ARMED if self.command is not None else UIState.IDLE
        self._log.info("armed" if self.command else "engine_exhausted", **self._command_fields())

    def _ignored(self, what: str) -> bool:
        """Log a key press the current state has no meaning for, and refuse it."""
        self._log.warning(f"{what}_ignored", state=self.state.value)
        return False

    def _command_fields(self) -> dict[str, str | None]:
        cmd = self.command
        return {"primitive": None if cmd is None else cmd.primitive.value,
                "src": None if cmd is None or cmd.src is None else cmd.src.id,
                "dst": None if cmd is None or cmd.dst is None else cmd.dst.id}

    def _report(self, success: bool, failure_mode: str | None) -> None:
        """One outcome per command the engine handed out (5.5). The engine decides what follows."""
        self._engine.report(Outcome(success=success, observed_state_delta={}, failure_mode=failure_mode))
        self._log.info("reported", success=success, failure_mode=failure_mode, **self._command_fields())

    def start(self) -> bool:
        """``s``: open an episode for the armed command. Re-arms first if the engine had been empty."""
        if self.state is UIState.IDLE:
            self._arm()
        if self.state is not UIState.ARMED or self.command is None:
            return self._ignored("start")
        self.recorder.start_episode(self.command)
        self.started_ns, self.stopped_ns = self._now(), None
        self.state = UIState.RECORDING
        return True

    def stop(self) -> bool:
        """``x``: stop writing frames. The episode stays open until the operator marks it."""
        if self.state is not UIState.RECORDING:
            return self._ignored("stop")
        self.stopped_ns, self.state = self._now(), UIState.STOPPED
        return True

    def mark(self, success: bool) -> bool:
        """``y``/``n``: the operator's verdict (5.6). Writes the episode and reports the outcome."""
        if self.state is UIState.RECORDING:
            self.stop()
        if self.state is not UIState.STOPPED:
            return self._ignored("mark")
        self.recorder.mark_success(success)
        meta = self.recorder.stop_episode()
        self._log.info("episode_marked", success=success, perturbed=self.perturbed,
                       frames=0 if meta is None else meta.frames, kept=meta is not None)
        self._report(success, None if success else MARKED_FAILURE)
        self._arm()
        return True

    def toggle_perturbed(self) -> bool:
        """``p``: a second person disturbed the scene during this episode (5.6). Toggles."""
        if self.state not in (UIState.RECORDING, UIState.STOPPED):
            return self._ignored("perturbed")
        self.perturbed = not self.perturbed
        self.recorder.mark_perturbed(self.perturbed)
        return True

    def abort(self) -> bool:
        """``a``: throw the episode away (nothing is written) and give the command back as failed."""
        if self.state in (UIState.RECORDING, UIState.STOPPED):
            self.recorder.stop_episode(keep=False)
        elif self.state is not UIState.ARMED:
            return self._ignored("abort")
        self.aborted += 1
        self._log.info("episode_aborted", **self._command_fields())
        self._report(False, ABORTED)
        self._arm()
        return True

    def quit(self) -> bool:
        """``q``: abort anything open and ask :meth:`run_window` to stop."""
        if self.state is not UIState.IDLE:
            self.abort()
        self.quit_requested = True
        return True

    def handle_key(self, key: int | str) -> UIState:
        """Apply one key press (a character or a ``cv2.waitKey`` code) and return the new state."""
        char = chr(key & 0xFF) if isinstance(key, int) else str(key)[:1]
        action = self._keys.get(char.lower())
        if action is not None:
            self._actions[action]()
        return self.state

    def tick(self, action: MotionCommand) -> bool:
        """Hand the recorder the action the teleop loop just sent. False unless a frame was written."""
        return self.recorder.tick(action) if self.state is UIState.RECORDING else False

    def _marker(self, frame: np.ndarray, cell: Cell | None, which: str) -> tuple[float, float] | None:
        """Draw one goal cell where ``runtime/goal.py`` says it is. Returns that pixel, or None."""
        px = self._goal.pixel(cell)
        if px is None:
            return None
        centre, color = (int(round(px[0])), int(round(px[1]))), tuple(int(v) for v in self._ui[f"{which}_color_bgr"])
        cv2.circle(frame, centre, int(self._ui["marker_radius_px"]), color, int(self._ui["marker_thickness_px"]))
        cv2.circle(frame, centre, int(self._ui["marker_dot_px"]), color, -1)
        return px

    def lines(self) -> list[str]:
        """The three banner lines: the command, the episode, the keys."""
        f, episode = self._command_fields(), self.recorder.episode
        return [
            f"{(f['primitive'] or 'no command').upper()}   src {f['src'] or '-'} (green)"
            f"   dst {f['dst'] or '-'} (magenta)",
            f"{self.state.value.upper()}   {self.elapsed_s:5.1f} s   {0 if episode is None else episode.frames} frames"
            f"   perturbed {'YES' if self.perturbed else 'no'}   kept {len(self.recorder.episodes)}"
            f"   aborted {self.aborted}",
            "  ".join(f"{c}={a.replace('mark_', '').replace('toggle_', '')}" for c, a in self._keys.items()),
        ]

    def render(self) -> np.ndarray:
        """The board frame with the two goal markers, over a text banner. ``(h + banner, w, 3)``.

        The markers are drawn *in* the image and the text *below* it: the operator sees the board
        exactly as the recorder stores it, plus two circles, and never through text.
        """
        frame = np.ascontiguousarray(self._top.grab().payload.copy())
        if self.command is not None:
            self._marker(frame, self.command.src, "src")
            self._marker(frame, self.command.dst, "dst")
        banner = np.full((int(self._ui["banner_height_px"]), frame.shape[1], 3),
                         self._ui["banner_color_bgr"], dtype=frame.dtype)
        for i, line in enumerate(self.lines()):
            cv2.putText(banner, line, (6, 20 + 24 * i), cv2.FONT_HERSHEY_SIMPLEX, float(self._ui["font_scale"]),
                        tuple(int(v) for v in self._ui["text_color_bgr"]), int(self._ui["font_thickness"]),
                        cv2.LINE_AA)
        return np.vstack([frame, banner])

    def run_window(self, step: Callable[[], MotionCommand | None] | None = None, delay_ms: int = 1) -> int:
        """The windowed mode: render, show, read one key, repeat until ``q``. Returns episodes kept.

        ``step`` is one iteration of the teleop loop: it sends whatever it sends and returns the
        action admitted, or None. This method never builds one (R2). Untested: it needs a display.
        """
        if self.headless:
            raise RuntimeError("run_window() needs headless=False")
        name = str(self._ui["window_name"])
        try:
            while not self.quit_requested:
                action = None if step is None else step()
                self.recorder.poll() if action is None else self.tick(action)  # read-only when idle (R1)
                cv2.imshow(name, self.render())
                self.handle_key(cv2.waitKey(delay_ms))
        finally:
            cv2.destroyWindow(name)
        return len(self.recorder.episodes)
