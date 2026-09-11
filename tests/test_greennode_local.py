"""Runs the T-009 bash acceptance test under pytest.

The test itself is ``tests/test_greennode_local.sh``: the thing under test is a shell script, so the
checks are written in shell and this module only reports the outcome. The bash script uses a temporary
HOME, so no real credentials file is read or written.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tests" / "test_greennode_local.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_greennode_local_round_trip() -> None:
    """up -> train dummy_job -> down in GREENNODE_TRANSPORT=local mode, plus the remote-mode refusal."""
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    report = f"$ bash {SCRIPT}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    print(report)
    assert proc.returncode == 0, report


def test_no_credential_strings_in_cloud() -> None:
    """Acceptance: `git grep -i -E "password|secret|token" cloud/` finds nothing.

    ``--untracked`` is added so the check is real before the files are staged as well as after.
    """
    proc = subprocess.run(
        ["git", "grep", "--untracked", "-i", "-n", "-E", "password|secret|token", "--", "cloud/"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    # git grep exits 1 when there are no matches, which is the outcome this test wants.
    assert proc.returncode == 1, f"credential-looking strings under cloud/:\n{proc.stdout}"
