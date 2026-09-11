"""Global pytest configuration.

Implements the R1 gate at test-collection level: a test marked ``motion`` moves real hardware and
runs only inside a human-enabled session (CLAUDE.md 4.6). The verdict comes from
``runtime.safety.SessionGate().status()`` -- the same gate ``Guard.admit`` consults -- so there is one
implementation of R1 and the test suite cannot disagree with the runtime about it.

The gate is asked once, at collection. A session that expires mid-run therefore does not unskip or
re-skip anything, but every command a running motion test sends is still refused by the guard the
moment the session expires.
"""

import pytest


def _session_skip_reason() -> str | None:
    """Return None when motion tests may run, otherwise the reason to skip them."""
    try:
        from runtime import safety  # lazy: pytest must still collect if the import tree is broken
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
