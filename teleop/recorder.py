"""Episode capture to a LeRobot dataset on one clock (CLAUDE.md 5.2, 5.3, 5.6; D-011).

One :class:`Recorder` owns one recording session. The caller polls the devices as fast as the
fastest of them runs and hands the recorder the action it just sent, once per dataset period, on the
frame grid the recorder hands back (:meth:`Recorder.next_grid_ns`)::

    rec = Recorder("20260912T0900", arm=arm, hand=hand, cameras=cams, glove=glove, operator="alois")
    rec.start_episode(command)                   # the command comes from the engine (5.5)
    while running:
        if now >= next_tick:                     # rates.dataset_hz, 30 Hz
            admitted = arm.send_targets(target)  # the Guard is what decides (R1, R3)
            hand.send_pinch(admitted.pinch)
            rec.tick(admitted)                   # polls, then writes one dataset frame
            next_tick = rec.next_grid_ns(now) or now + rec.period_ns
        else:
            rec.poll()                           # rates.state_hz or faster: read-only, always allowed
    rec.mark_success(); rec.stop_episode()
    rec.close()                                  # flushes lerobot's parquet writers

**Nothing here moves anything.** The recorder only reads streams and writes files; the action it
stores is the command the caller already sent, after :meth:`runtime.safety.Guard.admit` clamped it,
so the dataset holds what the robot was actually told and never a target that was refused (R2, R5).

Why it polls faster than it writes, why the frame grid is the board camera's and starts one lag
behind, and why the goal heatmaps are not a per-frame feature: docs/teleop.md, "The recorder".
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from lerobot.datasets.lerobot_dataset import CODEBASE_VERSION, LeRobotDataset

from drivers.interfaces import ArmDriver, CameraDriver, GloveDriver, HandDriver
from engine.interface import Command
from runtime import clock, config
from runtime.goal import GoalRenderer
from runtime.log import get_logger
from runtime.safety import REPO_ROOT
from runtime.types import MotionCommand

__all__ = ["SIDECAR", "STREAMS", "EpisodeMeta", "Recorder"]

#: Every stream a frame is built from. ``palm`` comes from the hand driver, not a camera driver, and
#: ``action`` is the admitted command, which is a stream like any other and is aligned like one.
STREAMS: tuple[str, ...] = ("top", "oblique", "palm", "state", "hand", "glove", "action")
#: Samples kept per stream. Alignment never looks further back than the lag plus one device period.
BUFFER = 32
#: Per-episode metadata (5.6), one JSON object per line, next to the dataset. lerobot v3.0 has no
#: free-form per-episode field, so this sidecar is where the fields of 5.6 that it does not model go.
SIDECAR = "episodes_meta.jsonl"


@dataclass
class EpisodeMeta:
    """One episode's metadata (CLAUDE.md 5.6) plus the measurements the dataset card reports."""

    episode_index: int
    task_id: str
    task_index: int
    src_cell: str | None
    dst_cell: str | None
    horse_id: str | None
    success: bool = False
    perturbed: bool = False
    operator: str = "unknown"
    latency_config_hash: str = ""
    safety_config_hash: str = ""
    board_config_hash: str = ""
    frames: int = 0
    started_ts_ns: int = 0
    ended_ts_ns: int = 0
    goal: dict[str, Any] = field(default_factory=dict)
    skew_p50_ms: float = 0.0
    skew_p99_ms: float = 0.0
    samples: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    skipped_ticks: int = 0
    align_failures: int = 0


class Recorder:
    """Records LeRobot episodes for one session into ``<root>/<session_id>/``."""

    def __init__(
        self,
        session_id: str,
        *,
        arm: ArmDriver,
        hand: HandDriver,
        cameras: Mapping[str, CameraDriver],
        glove: GloveDriver,
        operator: str = "unknown",
        now_ns: Callable[[], int] = clock.now_ns,
        root: Path | str | None = None,
        config_root: Path | str | None = None,
        use_videos: bool | None = None,
    ) -> None:
        missing = [n for n in ("top", "oblique") if n not in cameras]
        if missing:
            raise ValueError(f"Recorder needs the {missing} camera(s); got {sorted(cameras)}")
        self.session_id, self.operator = session_id, operator
        self._arm, self._hand, self._cameras, self._glove = arm, hand, dict(cameras), glove
        self._now, self._config_root = now_ns, config_root
        self._training = config.load("training", root=config_root)
        self._robot = config.load("robot", root=config_root)
        self._hand_cfg = config.load("hand", root=config_root)
        self._cameras_cfg = config.load("cameras", root=config_root)
        rec = self._training["recorder"]
        self.fps = int(self._training["rates"]["dataset_hz"])
        self.period_ns = round(1e9 / self.fps)
        self.lag_ns = int(rec["alignment_lag_periods"]) * self.period_ns
        self.tolerance_ns = round(float(rec["alignment_tolerance_ms"]) * 1e6)
        self.rates_hz = self._rates()
        self.drop_gap_ns = {n: round(float(rec["drop_gap_periods"]) * 1e9 / hz) for n, hz in self.rates_hz.items()}
        self.task_ids: tuple[str, ...] = tuple(str(t) for t in self._training["observation"]["task_ids"])
        self.hashes = {name: config.config_hash(name, root=config_root) for name in ("robot", "safety", "board")}
        base = Path(root) if root is not None else REPO_ROOT / str(self._training["dataset"]["root"])
        self.root = base / session_id
        self._goal = GoalRenderer(config_root=config_root)
        self._log = get_logger("teleop.recorder", session=session_id)
        self.episodes: list[EpisodeMeta] = []
        self._meta: EpisodeMeta | None = None
        self._reset_streams()
        self.dataset = LeRobotDataset.create(
            f"ludo-g1/{session_id}", fps=self.fps, features=self._features(), root=self.root,
            robot_type=str(self._robot["model"]), image_writer_threads=int(rec["image_writer_threads"]),
            use_videos=bool(rec["use_videos"]) if use_videos is None else bool(use_videos),
            metadata_buffer_size=1,  # write each episode's metadata as it is saved, not in batches
        )

    def __repr__(self) -> str:
        return f"Recorder(session={self.session_id!r}, root={str(self.root)!r}, episodes={len(self.episodes)})"

    @property
    def episode(self) -> EpisodeMeta | None:
        """The open episode's metadata, or None when no episode is open (read-only inspection)."""
        return self._meta

    # -- what the configs say each stream is (5.2, 5.3) ----------------------------------------------

    def _rates(self) -> dict[str, float]:
        """Nominal rate of every stream, from the config of the device that produces it."""
        rates = {n: float(self._cameras_cfg[n]["fps"]) for n in ("top", "oblique", "palm")}
        rates["state"] = float(self._training["rates"]["state_hz"])
        rates["hand"] = float(self._hand_cfg["device"]["command_hz"])
        rates["glove"] = float(self._hand_cfg["glove"]["input_hz"])
        rates["action"] = float(self.fps)
        return rates

    def _features(self) -> dict[str, dict]:
        """The LeRobot feature spec of CLAUDE.md 5.3, sized from the configs the drivers obey."""
        obs = self._training["observation"]
        names = [str(n) for n in self._robot["action_order"]]
        feats: dict[str, dict] = {}
        for cam in ("top", "oblique", "palm"):
            w, h = (int(v) for v in self._cameras_cfg[cam]["policy_resolution"])
            want = tuple(int(v) for v in obs["images"][cam])
            if (w, h) != want:
                raise config.ConfigError(
                    f"config/cameras.yaml {cam}.policy_resolution {(w, h)} does not match "
                    f"config/training.yaml observation.images.{cam} {want}"
                )
            feats[f"observation.images.{cam}"] = {
                "dtype": "image", "shape": (3, h, w), "names": ["channels", "height", "width"],
            }
        feats["observation.state"] = {"dtype": "float32", "shape": (int(obs["state_dim"]),), "names": names}
        feats["observation.hand_joints"] = {
            "dtype": "float32", "shape": (int(obs["extra_recorded"]["dexh15_joints"]),),
            "names": [str(n) for n in self._hand_cfg["joint_order"]],
        }
        feats["observation.glove"] = {
            "dtype": "float32", "shape": (int(obs["extra_recorded"]["glove_channels"]),), "names": None,
        }
        feats["task_id"] = {"dtype": "int64", "shape": (1,), "names": None}
        feats["action"] = {"dtype": "float32", "shape": (int(self._training["action"]["dim"]),), "names": names}
        return feats

    # -- stream plumbing -----------------------------------------------------------------------------

    def _reset_streams(self) -> None:
        latency = self._robot["latency"]
        # A sample stamped at ts describes an event `L` earlier, so its timestamp moves back by L
        # (runtime/clock.py shift). `action` is a command, not an observation: it is not shifted.
        keys = {"top": "camera_top_ms", "oblique": "camera_oblique_ms", "palm": "camera_palm_ms",
                "state": "arm_ms", "hand": "hand_ms", "glove": "glove_ms"}
        self.shifts_ns = {n: -round(float(latency[k]) * 1e6) for n, k in keys.items()} | {"action": 0}
        self._origin_ns: int | None = None
        self._raw = {n: clock.StreamBuffer(n, BUFFER) for n in STREAMS}
        self._seen: dict[str, list[int]] = {n: [] for n in STREAMS}
        self._targets: list[int] = []

    def _push(self, name: str, ts_ns: int, payload: Any) -> None:
        buf = self._raw[name]
        if len(buf) and ts_ns <= buf.latest().ts_ns:
            return  # the device has not produced a new sample since the last poll
        buf.push(ts_ns, payload)
        self._seen[name].append(ts_ns + self.shifts_ns[name])

    def poll(self) -> None:
        """Read every device once and buffer whatever is new.

        Read-only: allowed with no session, always (R1). Call it at least as often as the fastest
        device produces samples (``rates.state_hz``); :meth:`tick` calls it too, so a caller that
        only ticks still records, with the coarser skew the dataset card then reports.
        """
        got = {name: cam.grab() for name, cam in self._cameras.items() if name in STREAMS}
        got["palm"], got["state"] = self._hand.palm_frame(), self._arm.read_state()
        got["hand"], got["glove"] = self._hand.read_state(), self._glove.read()
        for name, stamped in got.items():
            self._push(name, stamped.ts_ns, stamped.payload)

    def _aligned(self, target_ns: int) -> dict[str, clock.Stamped] | None:
        shifted = {n: clock.shift(buf, self.shifts_ns[n]) for n, buf in self._raw.items()}
        try:
            return clock.align(shifted, target_ns, self.tolerance_ns)
        except clock.AlignmentError as exc:
            self._require_open().align_failures += 1
            self._log.warning("frame_align_failed", detail=str(exc))
            return None

    # -- the episode ---------------------------------------------------------------------------------

    def start_episode(self, command: Command) -> EpisodeMeta:
        """Open an episode for one engine command. Raises if one is already open."""
        if self._meta is not None:
            raise RuntimeError(f"episode {self._meta.episode_index} is still open; call stop_episode() first")
        task = command.primitive.value
        if task not in self.task_ids:
            raise config.ConfigError(f"config/training.yaml observation.task_ids {self.task_ids} lacks {task!r}")
        self._reset_streams()
        self._next_index = 0
        self._task_text = " ".join(
            [task] + ([f"from {command.src.id}"] if command.src else [])
            + ([f"to {command.dst.id}"] if command.dst else [])
        )
        self._meta = EpisodeMeta(
            episode_index=self.dataset.meta.total_episodes, task_id=task, task_index=self.task_ids.index(task),
            src_cell=None if command.src is None else command.src.id,
            dst_cell=None if command.dst is None else command.dst.id,
            horse_id=command.horse_id, operator=self.operator, started_ts_ns=self._now(),
            latency_config_hash=self.hashes["robot"], safety_config_hash=self.hashes["safety"],
            board_config_hash=self.hashes["board"],
            goal={"src_px": self._goal.pixel(command.src), "dst_px": self._goal.pixel(command.dst),
                  "sigma_px": self._goal.sigma_px, "frame": [self._goal.width, self._goal.height]},
        )
        self._log.info("episode_start", index=self._meta.episode_index, task=self._task_text)
        return self._meta

    def tick(self, action: MotionCommand) -> bool:
        """Poll, then write at most one frame: the grid point one alignment lag behind now.

        Returns True when a frame was written. False means no grid point is ripe yet (the caller is
        ticking faster than ``rates.dataset_hz``) or the streams did not align inside
        ``recorder.alignment_tolerance_ms``, which is counted in the episode metadata.
        """
        meta = self._require_open()
        now = self._now()
        self._push("action", now, action)
        self.poll()
        if self._origin_ns is None and not self._start_grid(now):
            return False
        index = (now - self._origin_ns - self.lag_ns) // self.period_ns
        if index < self._next_index:
            return False
        # Grid points the caller ticked straight past: their samples are already out of the buffers.
        meta.skipped_ticks += int(index - self._next_index)
        self._next_index = index + 1
        at = self._aligned(target := self._origin_ns + index * self.period_ns)
        if at is None:
            return False
        arm, hand, glove = at["state"].payload, at["hand"].payload, at["glove"].payload
        cmd: MotionCommand = at["action"].payload
        self.dataset.add_frame({
            "observation.images.top": at["top"].payload,
            "observation.images.oblique": at["oblique"].payload,
            "observation.images.palm": at["palm"].payload,
            "observation.state": np.concatenate([arm.arm, [arm.waist_yaw, hand.pinch]]).astype(np.float32),
            "observation.hand_joints": np.asarray(hand.joints_rad, dtype=np.float32),
            "observation.glove": np.asarray(glove.angles_deg, dtype=np.float32),
            "task_id": np.array([meta.task_index], dtype=np.int64),
            "action": cmd.to_action().astype(np.float32),
            "task": self._task_text,
        })
        self._targets.append(target)
        meta.frames, meta.ended_ts_ns = meta.frames + 1, target
        return True

    def _start_grid(self, first_action_ns: int) -> bool:
        """Phase-lock the frame grid to the board camera, starting at the first command.

        A 30 Hz stream is at best half a period away from an arbitrary 30 Hz grid, so a grid pinned to
        whenever the operator pressed start would put 16.7 ms of skew on ``top`` -- the frame the goal
        channels and the whole board state live in -- for no reason. Starting at the first camera grid
        point at or after the first command keeps every target inside the span the operator commanded.
        Returns False while the board camera has not delivered a frame yet.
        """
        if not len(self._raw["top"]):
            return False
        top_ns = self._raw["top"].latest().ts_ns + self.shifts_ns["top"]
        self._origin_ns = top_ns if top_ns >= first_action_ns else top_ns + self.period_ns
        return True

    def next_grid_ns(self, after_ns: int) -> int | None:
        """The first frame-grid point strictly after ``after_ns``; None before the grid exists.

        The teleop loop issues its commands on this grid (see the module docstring): a command issued
        between two grid points is that much further from the observation it is recorded with.
        """
        if self._origin_ns is None:
            return None
        return self._origin_ns + ((after_ns - self._origin_ns) // self.period_ns + 1) * self.period_ns

    def mark_success(self, success: bool = True) -> None:
        """Operator verdict for the open episode (5.6)."""
        self._require_open().success = bool(success)

    def mark_perturbed(self, perturbed: bool = True) -> None:
        """Record that a second person disturbed the scene during the open episode (5.6)."""
        self._require_open().perturbed = bool(perturbed)

    def stop_episode(self, *, keep: bool = True) -> EpisodeMeta | None:
        """Close the open episode, write it, refresh the dataset card. None when nothing was kept."""
        meta = self._require_open()
        self._meta = None
        if not keep or meta.frames == 0:
            self.dataset.episode_buffer = None
            self._log.info("episode_discarded", index=meta.episode_index, frames=meta.frames)
            return None
        meta.samples = {n: len(ts) for n, ts in self._seen.items()}
        meta.dropped = self._drops()
        skew = clock.skew_stats(self._stat_buffers(), instants=self._targets)
        meta.skew_p50_ms, meta.skew_p99_ms = skew.p50_ms, skew.p99_ms
        self.dataset.save_episode()
        self.episodes.append(meta)
        with (self.root / SIDECAR).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(meta), sort_keys=True) + "\n")
        (self.root / "README.md").write_text(self._card(), encoding="utf-8")
        self._log.info("episode_saved", index=meta.episode_index, frames=meta.frames,
                       skew_p99_ms=round(meta.skew_p99_ms, 3), dropped=meta.dropped)
        return meta

    def close(self) -> None:
        """Flush lerobot's parquet writers. Without this the dataset cannot be loaded back."""
        self.dataset.finalize()

    def _require_open(self) -> EpisodeMeta:
        if self._meta is None:
            raise RuntimeError("no episode is open; call start_episode() first")
        return self._meta

    # -- measurements (R5: every number on the card is counted, never described) ----------------------

    def _stat_buffers(self) -> dict[str, clock.StreamBuffer]:
        """Timestamp-only buffers over the whole episode, for :func:`runtime.clock.skew_stats`."""
        out: dict[str, clock.StreamBuffer] = {}
        for name, stamps in self._seen.items():
            out[name] = buf = clock.StreamBuffer(name, max(len(stamps), 1))
            for ts in stamps:
                buf.push(ts, None)
        return out

    def _drops(self) -> dict[str, int]:
        """Samples missing per stream: a gap over ``drop_gap_periods`` of the stream's own period.

        The yardstick is the rate the device is specified to run at (``_rates``), so this counts what
        the device failed to deliver *and* what a caller polling more slowly than the device failed to
        pick up. Both are losses as far as the dataset is concerned, and both belong on the card.
        """
        out: dict[str, int] = {}
        for name, stamps in self._seen.items():
            period_ns, missing = round(1e9 / self.rates_hz[name]), 0
            for a, b in zip(stamps, stamps[1:], strict=False):
                if b - a > self.drop_gap_ns[name]:
                    missing += max(round((b - a) / period_ns) - 1, 1)
            out[name] = missing
        return out

    def _card(self) -> str:
        """The dataset card of CLAUDE.md 5.6, rewritten after every saved episode."""
        eps, rows = self.episodes, []
        for e in eps:
            dropped = ", ".join(f"{k}={v}" for k, v in e.dropped.items() if v) or "none"
            rows.append(
                f"| {e.episode_index} | {e.task_id} | {e.src_cell or '-'} | {e.dst_cell or '-'} | {e.frames} | "
                f"{e.success} | {e.perturbed} | {e.skew_p50_ms:.3f} | {e.skew_p99_ms:.3f} | {dropped} |"
            )
        summary = {
            "operator": self.operator,
            "episodes": len(eps),
            "frames": sum(e.frames for e in eps),
            "dataset rate": f"{self.fps} Hz",
            "stream rate / latency shift (ms) / samples": ", ".join(
                f"{n}={self.rates_hz[n]:g} Hz/{-self.shifts_ns[n] / 1e6:g}/{sum(e.samples.get(n, 0) for e in eps)}"
                for n in STREAMS
            ),
            "alignment": f"lag {self.lag_ns / 1e6:.1f} ms, tolerance {self.tolerance_ns / 1e6:.1f} ms",
            "UNMEASURED in config/robot.yaml": ", ".join(config.unmeasured("robot", root=self._config_root)) or "none",
            "latency_config_hash (robot)": f"`{self.hashes['robot']}`",
            "safety_config_hash": f"`{self.hashes['safety']}`",
            "board_config_hash": f"`{self.hashes['board']}`",
        }
        return "\n".join([
            f"# LUDO-G1 session `{self.session_id}`", "",
            f"LeRobot dataset `{CODEBASE_VERSION}`, written by `teleop/recorder.py` (CLAUDE.md 5.6, D-011).",
            "Generated automatically: every number below is measured, not described (R5).", "",
            "| | |", "|---|---|", *(f"| {k} | {v} |" for k, v in summary.items()), "",
            "## Episodes", "",
            "| # | task | src | dst | frames | success | perturbed | skew p50 ms | skew p99 ms | dropped |",
            "|---|---|---|---|---|---|---|---|---|---|", *rows, "",
            f"Skew is the worst per-frame stream offset from the alignment instant ({len(self._targets)} instants",
            "in the last episode), per `runtime/clock.py`. `dropped` counts samples missing from a stream, judged",
            "against that stream's own nominal rate above.", "",
            f"Per-episode metadata (CLAUDE.md 5.6) is in `{SIDECAR}`, one JSON object per episode. The goal",
            "heatmaps are not stored per frame: the cells and their pixels are in that sidecar and",
            "`runtime/goal.py` re-renders them at training time (docs/teleop.md).", "",
        ])
