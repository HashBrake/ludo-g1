"""Shared reference-window / realtime-buffer configuration.

Parsed once from the top-level config and consumed by both
``SimulationLoop`` and the process-isolated sim2real runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from teleopit.runtime.common import (
    cfg_get,
    parse_alpha,
    parse_nonnegative_int,
)


@dataclass(frozen=True)
class ReferenceConfig:
    retarget_buffer_enabled: bool
    retarget_buffer_window_s: float
    reference_delay_s: float | None
    reference_debug_log: bool
    realtime_buffer_warmup_steps: int
    reference_velocity_smoothing_alpha: float
    reference_anchor_velocity_smoothing_alpha: float
    # Deadbands applied to finite-diff anchor velocities BEFORE smoothing.
    # 0.0 (default) = disabled, identical to pre-2026-08-17 behavior.
    # Purpose: tracker jitter on a standing pilot otherwise reaches the policy
    # as small nonzero root velocity and draws corrective steps (audit RC6).
    anchor_lin_vel_deadband: float
    anchor_ang_vel_deadband: float
    # Gain on the pilot's root XY displacement (anchored per mocap session).
    # 1.0 = off; 1.143 neutralizes GMR's fixed 0.875 pelvis under-scaling
    # (audit 2026-08-17 RC4, "robot moves less than me").
    root_xy_gain: float


def _resolve_delay(cfg: Any, *, provider_fps: float | None) -> float | None:
    """Select reference delay: retarget_buffer_delay_s > realtime_input_delay_s > 1/fps."""
    raw = cfg_get(cfg, "retarget_buffer_delay_s", None)
    if raw in (None, "", "null"):
        raw = cfg_get(cfg, "realtime_input_delay_s", None)
    if raw not in (None, "", "null"):
        return float(raw)
    if provider_fps is not None:
        return 1.0 / max(provider_fps, 1.0)
    return None


def parse_reference_config(
    cfg: Any,
    *,
    provider_fps: float | None = None,
) -> ReferenceConfig:
    """Parse reference-window / realtime-buffer config from *cfg*.

    Parameters
    ----------
    cfg:
        Top-level config (dict, DictConfig, or attribute-bearing object).
    provider_fps:
        Realtime input provider FPS.  When given and no explicit delay is
        configured, ``reference_delay_s`` defaults to ``1/provider_fps``.
        Pass ``None`` (the default) for offline / simulation paths where
        no such fallback is desired.
    """
    retarget_buffer_enabled = bool(cfg_get(cfg, "retarget_buffer_enabled", True))
    retarget_buffer_window_s = float(cfg_get(cfg, "retarget_buffer_window_s", 0.5))
    if retarget_buffer_window_s <= 0.0:
        raise ValueError("retarget_buffer_window_s must be > 0")

    reference_debug_log = bool(cfg_get(cfg, "reference_debug_log", False))
    reference_delay_s = _resolve_delay(cfg, provider_fps=provider_fps)

    warmup = parse_nonnegative_int(
        cfg_get(cfg, "realtime_buffer_warmup_steps", 0),
        field_name="realtime_buffer_warmup_steps",
        default=0,
    )

    vel_alpha = parse_alpha(
        cfg_get(cfg, "reference_velocity_smoothing_alpha", 1.0),
        field_name="reference_velocity_smoothing_alpha",
        default=1.0,
    )
    anchor_vel_alpha = parse_alpha(
        cfg_get(cfg, "reference_anchor_velocity_smoothing_alpha", 1.0),
        field_name="reference_anchor_velocity_smoothing_alpha",
        default=1.0,
    )

    root_xy_gain = float(cfg_get(cfg, "root_xy_gain", 1.0))
    if not (0.5 <= root_xy_gain <= 2.0):
        raise ValueError("root_xy_gain must be in [0.5, 2.0]")
    lin_deadband = float(cfg_get(cfg, "anchor_lin_vel_deadband", 0.0))
    ang_deadband = float(cfg_get(cfg, "anchor_ang_vel_deadband", 0.0))
    if lin_deadband < 0.0 or ang_deadband < 0.0:
        raise ValueError("anchor velocity deadbands must be >= 0")

    return ReferenceConfig(
        retarget_buffer_enabled=retarget_buffer_enabled,
        retarget_buffer_window_s=retarget_buffer_window_s,
        reference_delay_s=reference_delay_s,
        reference_debug_log=reference_debug_log,
        realtime_buffer_warmup_steps=warmup,
        reference_velocity_smoothing_alpha=vel_alpha,
        reference_anchor_velocity_smoothing_alpha=anchor_vel_alpha,
        anchor_lin_vel_deadband=lin_deadband,
        anchor_ang_vel_deadband=ang_deadband,
        root_xy_gain=root_xy_gain,
    )
