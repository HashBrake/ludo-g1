"""The policy contract: what ``runtime/controller.py`` hands a policy and what it gets back (5.3).

One :class:`Observation` in, one :class:`ActionChunk` out, at the rates of CLAUDE.md 5.2: the
controller calls :meth:`Policy.act` at ``rates.policy_hz`` (10 Hz) and plays the returned chunk out
at ``rates.action_hz`` (30 Hz), never beyond ``diffusion.execute`` of its entries.

This module is the contract only. It holds no model, no weights and no device: the Diffusion Policy
and the ACT baseline of 5.7 arrive in ``policy/`` in Phase 3 and satisfy :class:`Policy` from the
outside, exactly as the mocks in ``drivers/mock/`` satisfy the driver protocols. The one
implementation here, :class:`HoldPolicy`, exists so that the orchestration can be tested before any
policy exists at all, and it moves nothing (R2 -- see its docstring).

Shapes, once, so that nothing downstream has to guess:

===========  ==========================  =======================================================
field        shape                       meaning
===========  ==========================  =======================================================
``top``      ``(h, w, 3)`` uint8         Brio, cropped to the board region (``config/cameras.yaml``)
``oblique``  ``(h, w, 3)`` uint8         Orbbec Ego left RGB
``palm``     ``(h, w, 3)`` uint8         DexH15 palm camera
``state``    ``(9,)`` float64            7 arm joints, waist yaw, pinch scalar (``action_order``)
``goal``     ``(2, h, w)`` float32       source and target heatmaps, in the ``top`` frame
``task_id``  ``(3,)`` float32            one-hot over ``observation.task_ids``
===========  ==========================  =======================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from engine.interface import Command
from runtime.types import ACTION_DIM

__all__ = ["ActionChunk", "HoldPolicy", "Observation", "Policy"]

#: Goal heatmap channels of CLAUDE.md 5.3: source cell, then target cell.
GOAL_CHANNELS = 2


def _image(value: object, what: str) -> np.ndarray:
    arr = np.asarray(value)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"Observation.{what} must be (h, w, 3), got shape {arr.shape}")
    return arr


@dataclass(frozen=True, eq=False)
class Observation:
    """Everything the policy sees at one instant, aligned on one clock (5.2, 5.3).

    ``ts_ns`` is the alignment instant on :func:`runtime.clock.now_ns`, not the timestamp of any one
    stream: each frame and the state are the nearest sample to it within the configured tolerance
    (``runtime.alignment_tolerance_ms`` of ``config/training.yaml``).
    """

    ts_ns: int
    top: np.ndarray
    oblique: np.ndarray
    palm: np.ndarray
    state: np.ndarray
    goal: np.ndarray
    task_id: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts_ns", int(self.ts_ns))
        for name in ("top", "oblique", "palm"):
            object.__setattr__(self, name, _image(getattr(self, name), name))
        state = np.asarray(self.state, dtype=np.float64).reshape(-1)
        if state.shape != (ACTION_DIM,):
            raise ValueError(f"Observation.state must hold {ACTION_DIM} values, got shape {state.shape}")
        object.__setattr__(self, "state", state)
        goal = np.asarray(self.goal, dtype=np.float32)
        want = (GOAL_CHANNELS, self.top.shape[0], self.top.shape[1])
        if goal.shape != want:
            raise ValueError(f"Observation.goal must be {want} (the `top` frame), got shape {goal.shape}")
        object.__setattr__(self, "goal", goal)
        task = np.asarray(self.task_id, dtype=np.float32).reshape(-1)
        if task.ndim != 1 or task.size == 0 or not np.isclose(task.sum(), 1.0) or np.any(task < 0):
            raise ValueError(f"Observation.task_id must be a one-hot vector, got {task!r}")
        object.__setattr__(self, "task_id", task)

    def __repr__(self) -> str:
        return (
            f"Observation(ts_ns={self.ts_ns}, top={self.top.shape}, oblique={self.oblique.shape}, "
            f"palm={self.palm.shape}, state={np.round(self.state, 4).tolist()}, "
            f"goal={self.goal.shape}, task_id={self.task_id.tolist()})"
        )


@dataclass(frozen=True, eq=False)
class ActionChunk:
    """``n`` absolute 9-D actions to be played out at ``hz`` (5.2, 5.3).

    The policy predicts ``diffusion.chunk`` (16) of them and the controller executes at most
    ``diffusion.execute`` (8) before the next chunk supersedes it -- the receding horizon of 5.2. The
    chunk carries its own rate so that a policy trained at a different ``action_hz`` cannot be played
    out at the wrong speed by accident.
    """

    actions: np.ndarray
    hz: float = 30.0

    def __post_init__(self) -> None:
        actions = np.asarray(self.actions, dtype=np.float64)
        if actions.ndim != 2 or actions.shape[1] != ACTION_DIM or actions.shape[0] == 0:
            raise ValueError(f"ActionChunk.actions must be (n, {ACTION_DIM}) with n >= 1, got {actions.shape}")
        actions.flags.writeable = False
        object.__setattr__(self, "actions", actions)
        hz = float(self.hz)
        if not hz > 0:
            raise ValueError(f"ActionChunk.hz must be positive, got {hz!r}")
        object.__setattr__(self, "hz", hz)

    def __len__(self) -> int:
        return int(self.actions.shape[0])

    def __getitem__(self, index: int) -> np.ndarray:
        return self.actions[index]

    def __repr__(self) -> str:
        return f"ActionChunk(n={len(self)}, dim={self.actions.shape[1]}, hz={self.hz:g})"


@runtime_checkable
class Policy(Protocol):
    """What ``runtime/controller.py`` requires of anything that drives the arm.

    The controller's cycle per command is::

        policy.reset(command)
        while not timed out:
            obs = ...                       # built from the drivers, aligned on one clock
            if policy.done(obs): break      # the policy's own termination signal (5.5)
            chunk = policy.act(obs)         # 10 Hz
            ...                             # played out at 30 Hz through the drivers

    :meth:`act` and :meth:`done` are read-only with respect to the robot: a policy never touches a
    driver itself. Only the controller sends, and only through the guard (R1, R3).
    """

    def reset(self, command: Command) -> None:
        """Start a new primitive execution. Any per-episode state is dropped here."""
        ...

    def act(self, observation: Observation) -> ActionChunk:
        """Predict the next chunk of absolute actions."""
        ...

    def done(self, observation: Observation) -> bool:
        """The policy's own termination signal for the current primitive (CLAUDE.md 5.5)."""
        ...


class HoldPolicy:
    """A test double that commands **no motion at all**, and is never deployed (R2).

    Every action it returns is the current measured state, so the arm is asked to stay exactly where
    it already is. It holds no trajectory, no waypoint list and no pose: there is nothing here to
    tune and nothing here that could move the robot towards a goal.

    It exists for one reason -- so that the orchestration of ``runtime/controller.py`` (rates,
    chunking, the guard path, timeouts, perception and the engine's report/retry cycle) can be
    tested before any learned policy exists. It is refused on any backend other than ``mock`` by
    :func:`runtime.controller.build`, and it will be deleted when Phase 3 delivers a real policy.
    """

    def __init__(self, chunk: int = 16, hz: float = 30.0) -> None:
        if chunk < 1:
            raise ValueError(f"HoldPolicy needs chunk >= 1, got {chunk}")
        self.chunk = int(chunk)
        self.hz = float(hz)
        self.command: Command | None = None
        self.calls = 0

    def __repr__(self) -> str:
        return f"HoldPolicy(chunk={self.chunk}, hz={self.hz:g}, calls={self.calls})"

    def reset(self, command: Command) -> None:
        self.command = command
        self.calls = 0

    def act(self, observation: Observation) -> ActionChunk:
        """``chunk`` copies of the measured state: hold still."""
        self.calls += 1
        return ActionChunk(actions=np.repeat(observation.state[None, :], self.chunk, axis=0), hz=self.hz)

    def done(self, observation: Observation) -> bool:
        """Never. Holding still never completes a primitive, so the controller's timeout ends it."""
        return False
