"""The one structlog logger for the project; every event carries the monotonic timestamp (section 7).

Usage::

    from runtime.log import get_logger

    log = get_logger("teleop.recorder", session="s042")
    log.info("episode_start", task_id="move", src="track-17")

Every event dict gets ``ts_ns`` (``runtime.clock.now_ns()``) and ``ts_s`` (the same value in seconds,
for reading). Output is key=value lines on stderr by default, or JSON when ``json=True`` so that
long-running processes writing to ``data/logs/`` stay machine-readable.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from runtime.clock import now_ns

__all__ = ["configure", "get_logger"]

_configured = False


def _add_monotonic_ts(_logger: Any, _method: str, event_dict: dict) -> dict:
    """Stamp the event with the process-wide monotonic clock."""
    ts = now_ns()
    event_dict["ts_ns"] = ts
    event_dict["ts_s"] = round(ts / 1e9, 6)
    return event_dict


def configure(level: int = logging.INFO, json: bool = False) -> None:
    """Configure structlog process-wide. Idempotent per (level, json); safe to call repeatedly."""
    global _configured
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _add_monotonic_ts,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound logger, configuring structlog on first use."""
    if not _configured:
        configure()
    log = structlog.get_logger(name) if name else structlog.get_logger()
    if initial_values:
        log = log.bind(**initial_values)
    return log
