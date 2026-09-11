"""Scaffold checks: the layout of CLAUDE.md 5.1 exists and imports cleanly."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

PACKAGES = [
    "board",
    "drivers",
    "drivers.mock",
    "engine",
    "eval",
    "policy",
    "runtime",
    "teleop",
    "tools",
    "tools.hardware_checks",
]

# Directories from CLAUDE.md 5.1 that are not Python packages.
PLAIN_DIRS = ["cloud", "config", "docs"]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__doc__, f"{name} has no module docstring"


@pytest.mark.parametrize("name", PLAIN_DIRS)
def test_plain_dir_exists(name: str) -> None:
    assert (REPO / name).is_dir()


def test_python_is_310() -> None:
    assert sys.version_info[:2] == (3, 10)


def test_session_enable_is_ignored_and_absent_from_history() -> None:
    """R1 / section 8 audit: the gate file is never committed."""
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "hardware/session.enable"], cwd=REPO, check=False
    )
    assert ignored.returncode == 0, "hardware/session.enable is not git-ignored"
    tracked = subprocess.run(
        ["git", "log", "--all", "--oneline", "--", "hardware/session.enable"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    assert tracked.stdout.strip() == "", "hardware/session.enable appears in git history"


@pytest.mark.motion
def test_motion_marker_is_skipped_without_a_session() -> None:
    """Never runs outside a session; its presence proves the autoskip is wired (see conftest)."""
    raise AssertionError("motion tests must not run without a valid hardware session")
