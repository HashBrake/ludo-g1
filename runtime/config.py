"""Loading, validating and hashing the yaml files under ``config/`` (CLAUDE.md section 7).

Configuration lives in yaml, never as constants in code. Every module that needs a number reads it
through :func:`load`; every dataset, checkpoint and eval result records the :func:`config_hash` of
the files it was produced under (5.6); and anything that is not yet a real measurement is tagged so
that :func:`unmeasured` can list it.

Placeholder convention (documented for humans in ``docs/config.md``):

* a leaf whose value is the literal string ``UNMEASURED``;
* a numeric (or otherwise typed) leaf ``x`` with a sibling key ``x_status: UNMEASURED``.

The second form exists so that a placeholder can still be a usable number: ``fps: 30`` next to
``fps_status: UNMEASURED`` loads as ``30`` and is still reported by :func:`unmeasured`.

No I/O beyond reading the yaml files happens here, and nothing in this module knows about devices.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "CONFIG_DIR",
    "NAMES",
    "REQUIRED_KEYS",
    "STATUS_SUFFIX",
    "STATUS_VALUES",
    "UNMEASURED",
    "ConfigError",
    "config_hash",
    "load",
    "unmeasured",
]

CONFIG_DIR: Path = Path(__file__).resolve().parent.parent / "config"

UNMEASURED = "UNMEASURED"
STATUS_SUFFIX = "_status"
#: The only values a ``<key>_status`` sibling may take. MEASURED means a human or a Phase 1 script
#: put a real number there; anything else is a typo that would silently hide a placeholder.
STATUS_VALUES = frozenset({"UNMEASURED", "MEASURED"})

#: Required keys per config file, as dotted paths through mappings. A path may not traverse a list:
#: list contents are checked by the per-file tests, not by the schema.
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "robot": (
        "model",
        "limits_source",
        "arm.side",
        "arm.dof",
        "arm.joints",
        "waist.joints",
        "action_order",
        "network.laptop_ip",
        "network.robot_ip",
        "network.dds_domain_id",
        "network.dds_interface",
        "topics.state",
        "topics.command",
        "topics.arm_sdk_weight_index",
        "control.command_hz",
        "control.kp",
        "control.kd",
        "control.weight_ramp_s",
        "latency.arm_ms",
        "latency.hand_ms",
        "latency.method",
    ),
    "cameras": (
        "defaults.driver",
        "top.device",
        "top.resolution",
        "top.fps",
        "top.crop",
        "top.policy_resolution",
        "oblique.device",
        "oblique.resolution",
        "oblique.fps",
        "oblique.policy_resolution",
        "palm.device",
        "palm.resolution",
        "palm.fps",
        "palm.policy_resolution",
    ),
    "safety": (
        "session.file",
        "session.default_seconds",
        "session.max_seconds",
        "session.required_fields",
        "session.required_checklist_value",
        "workspace_box_m.frame",
        "workspace_box_m.min",
        "workspace_box_m.max",
        "workspace_box_m.point",
        "workspace_box_m.margin_m",
        "joint_limits_rad",
        "waist_yaw_clamp_rad",
        "joint_velocity_limit_rad_s",
        "command_rate_limit_hz",
        "command_gap_reset_s",
        "first_command_max_step_rad",
        "watchdog_timeout_s",
        "hand.pinch_scalar_range",
        "hand.pinch_rate_limit_per_s",
    ),
    "board": (
        "size_mm",
        "frame",
        "board_origin_in_base",
        "apriltags.family",
        "apriltags.size_mm",
        "apriltags.ids",
        "layout_status",
        "layout.grid",
        "layout.cell_pitch_mm",
        "layout.colors",
        "layout.starts",
        "layout.home_entries",
        "layout.track_length",
        "layout.home_length",
        "layout.base_size",
        "layout.cells",
    ),
    "hand": (
        "device.port",
        "device.baud",
        "device.slave_address",
        "device.command_hz",
        "joint_order",
        "joint_order_status",
        "motor_order",
        "joint_limits_rad",
        "pinch.scalar_range",
        "pinch.open_pose",
        "pinch.closed_pose",
        "pinch.idle_curl_pose",
        "pinch.grasp_threshold",
        "glove.source",
        "glove.input_hz",
    ),
    "training": (
        "rates.camera_hz",
        "rates.state_hz",
        "rates.dataset_hz",
        "rates.policy_hz",
        "rates.action_hz",
        "observation.images",
        "observation.state_dim",
        "observation.goal_channels",
        "observation.task_ids",
        "action.dim",
        "action.hz",
        "diffusion.chunk",
        "diffusion.execute",
        "diffusion.inference_steps",
        "act.chunk",
        "augmentation.geometric_on_top",
        "dataset.format",
        "dataset.root",
        "compute.train_target",
    ),
}

#: Every config file this project has. Audits iterate over this, not over a directory listing.
NAMES: tuple[str, ...] = tuple(sorted(REQUIRED_KEYS))


class ConfigError(ValueError):
    """A config file is missing, unparseable, or does not satisfy its schema."""


def _path(name: str, root: Path | str | None) -> Path:
    if name not in REQUIRED_KEYS:
        raise ConfigError(f"unknown config {name!r}; known configs: {', '.join(NAMES)}")
    base = CONFIG_DIR if root is None else Path(root)
    return base / f"{name}.yaml"


def _walk(node: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Yield ``(dotted_path, value)`` for every leaf of ``node``, in document order.

    Mappings extend the path with ``.key``, sequences with ``[i]``. A mapping or sequence with no
    children is itself a leaf (an empty list is a value, not an absence).
    """
    out: list[tuple[str, Any]] = []
    if isinstance(node, dict) and node:
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_walk(value, child))
    elif isinstance(node, list) and node:
        for i, value in enumerate(node):
            out.extend(_walk(value, f"{prefix}[{i}]"))
    else:
        out.append((prefix, node))
    return out


def _get(data: dict, dotted: str) -> tuple[bool, Any]:
    """Look a dotted path up through mappings only. Returns ``(found, value)``."""
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _check_status_keys(name: str, data: dict) -> None:
    """Every ``<key>_status`` must carry a known status and must annotate an existing sibling."""
    for path, value in _walk(data):
        if not path.rsplit(".", 1)[-1].endswith(STATUS_SUFFIX):
            continue
        if value not in STATUS_VALUES:
            raise ConfigError(
                f"config/{name}.yaml: key {path!r} has status {value!r}; "
                f"allowed: {', '.join(sorted(STATUS_VALUES))}"
            )
        annotated = path[: -len(STATUS_SUFFIX)]
        # _get walks mappings only; a status key inside a list is left unchecked rather than
        # reported as missing.
        if "[" not in path and not _get(data, annotated)[0]:
            raise ConfigError(
                f"config/{name}.yaml: key {path!r} annotates {annotated!r}, which does not exist"
            )


def load(name: str, root: Path | str | None = None) -> dict:
    """Load ``config/<name>.yaml``, validate its schema, and return it as a plain dict.

    ``root`` overrides the config directory (tests only). Raises :class:`ConfigError` naming the
    file and the offending key for a missing file, a non-mapping document, a missing required key,
    or a malformed ``_status`` annotation.
    """
    path = _path(name, root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"config/{name}.yaml: cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"config/{name}.yaml: not valid yaml: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config/{name}.yaml: top level must be a mapping, got {type(data).__name__}")

    missing = [key for key in REQUIRED_KEYS[name] if not _get(data, key)[0]]
    if missing:
        raise ConfigError(
            f"config/{name}.yaml: missing required key{'s' if len(missing) > 1 else ''}: "
            + ", ".join(repr(key) for key in missing)
        )
    _check_status_keys(name, data)
    return data


def _canonical(data: dict) -> str:
    """Canonical text form of a loaded config: sorted keys, block style, explicit unicode."""
    return yaml.safe_dump(data, sort_keys=True, default_flow_style=False, allow_unicode=True)


def config_hash(name: str, root: Path | str | None = None) -> str:
    """sha256 of the canonical form of ``config/<name>.yaml``, as 64 lowercase hex characters.

    The hash is taken after parsing, so it ignores comments, whitespace and key order and changes
    if and only if a value changes. Datasets, checkpoints and eval results record it (5.6).
    """
    return hashlib.sha256(_canonical(load(name, root)).encode("utf-8")).hexdigest()


def unmeasured(name: str, root: Path | str | None = None) -> list[str]:
    """Dotted paths of every leaf in ``config/<name>.yaml`` tagged UNMEASURED, in document order.

    Both tag forms are reported under the path of the value itself: a leaf whose value is the
    literal ``UNMEASURED``, and a leaf ``x`` with a sibling ``x_status: UNMEASURED`` (reported as
    ``x``, never as ``x_status``). A leaf tagged both ways is reported once.
    """
    data = load(name, root)
    found: list[str] = []
    seen: set[str] = set()
    for path, value in _walk(data):
        key = path.rsplit(".", 1)[-1]
        if key.endswith(STATUS_SUFFIX) and value == UNMEASURED:
            target = path[: -len(STATUS_SUFFIX)]
        elif value == UNMEASURED:
            target = path
        else:
            continue
        if target not in seen:
            seen.add(target)
            found.append(target)
    return found
