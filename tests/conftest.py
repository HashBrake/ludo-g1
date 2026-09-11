"""Global pytest configuration.

Implements the R1 gate at test-collection level: a test marked ``motion`` moves real hardware and
runs only inside a human-enabled session (CLAUDE.md 4.6). Until T-005 lands ``runtime.safety`` does
not exist yet, so every motion test is skipped unconditionally.
"""

import pytest


def _session_skip_reason() -> str | None:
    """Return None when motion tests may run, otherwise the reason to skip them."""
    try:
        from runtime import safety  # imported lazily: the module may not exist yet
    except ImportError:
        return "no session gate yet"
    try:
        status = safety.SessionGate().status()
    except Exception as exc:  # the gate must fail closed, never fail the run open
        return f"session gate unusable: {exc!r}"
    if getattr(status, "valid", False):
        return None
    return f"no valid hardware session: {getattr(status, 'reason', 'unknown')}"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    reason = _session_skip_reason()
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "motion" in item.keywords:
            item.add_marker(skip)
