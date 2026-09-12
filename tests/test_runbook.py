"""The Phase 1 runbook is executable prose: every command in it runs, every id in it exists (T-044).

``docs/runbook_phase1.md`` is what a human follows on the first three hardware days, so a command
that has drifted since it was written is worse than no runbook at all: it is discovered at the robot,
with the arm powered. These tests are the guard against that drift. They

* collect every ``.venv/bin/python`` line out of the fenced blocks and run it with ``--help``,
  requiring exit 0 -- the tool exists, imports and parses its arguments;
* check every ``H-nnn``, ``Q-nnn``, ``D-nnn`` and ``T-nnn`` the runbook names against the file that
  owns those ids (CLAUDE.md 4.3);
* check the structural promises of T-044: the three days, a check per step, and the abort section.

Nothing here touches hardware, opens a session or sends a motion command: ``--help`` returns before
argparse hands control to any tool's ``main`` (R1, R2).

The one documented exception to "exit 0" is ``enable_session.py``. It is the sole writer of
``hardware/session.enable`` and refuses *every* argument by design, printing a usage line and exiting
2, so that no flag, no script and no agent can drive it; :func:`test_enable_session_rejects_arguments`
asserts that stricter behaviour instead.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from runtime.config import STATUS_SUFFIX
from tools.hardware_checks.preflight_report import MOTION_KEYS, STEPS

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = REPO_ROOT / "docs" / "runbook_phase1.md"
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

#: The interactive session opener: it takes no arguments at all, so ``--help`` is exit 2, not 0.
NO_ARGUMENT_TOOLS = ("tools/hardware_checks/enable_session.py",)

#: Where each id prefix is defined (CLAUDE.md 4.3).
ID_SOURCES = {
    "H": REPO_ROOT / "agents" / "HARDWARE_NEEDED.md",
    "Q": REPO_ROOT / "agents" / "QUESTIONS.md",
    "D": REPO_ROOT / "agents" / "DECISIONS.md",
    "T": REPO_ROOT / "agents" / "TASKS.md",
}

_FENCE = re.compile(r"^```", re.MULTILINE)


def runbook_text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def fenced_blocks(text: str) -> list[str]:
    """The bodies of the ``` fenced blocks, in document order.

    ``_FENCE.split`` alternates outside/inside starting outside, so the odd indices are the bodies;
    a language tag (```` ```sh ````) would be the first line of a body and is dropped here.
    """
    bodies = []
    for part in _FENCE.split(text)[1::2]:
        head, _, rest = part.partition("\n")
        bodies.append(rest if head.strip() and " " not in head.strip() else part)
    return bodies


def python_commands(text: str) -> list[str]:
    """Every ``.venv/bin/python ...`` command line inside a fenced block, in document order."""
    found = []
    for block in fenced_blocks(text):
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith(".venv/bin/python"):
                found.append(stripped.split("#", 1)[0].strip())
    return found


def test_the_runbook_exists_and_is_indexed():
    assert RUNBOOK.is_file()
    index = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "runbook_phase1.md" in index, "docs/README.md must index the runbook"


def test_it_has_the_three_days_and_the_abort_section():
    text = runbook_text()
    for heading in ("## Day 1", "## Day 2", "## Day 3", "## Abort criteria"):
        assert heading in text, f"missing section {heading!r}"
    # Every step is a numbered heading, and every step carries a check the human can see.
    steps = re.findall(r"^### (\d+\.\d+) ", text, re.MULTILINE)
    assert len(steps) >= 15, f"only {len(steps)} steps: {steps}"
    assert text.count("Check:") >= len(steps) - 4, "most steps must state what proves they worked"


def test_the_abort_section_names_the_estop_the_log_and_the_incident_rule():
    text = runbook_text().split("## Abort criteria", 1)[1]
    for needed in (
        "Q-004",                                  # which e-stop the human triggers
        "hardware/session.enable",                # deleting it is the second action
        "agents/BUILD_LOG.md",                    # where the agent writes
        'runtime.config.config_hash("safety")',   # the envelope in force
        "SAFETY INCIDENT:",                       # the line that stops the loop
        "R4(c)",                                  # ...under which rule
        "agents/BLOCKERS.md",                     # what three aborts become
    ):
        assert needed in text, f"the abort section must state {needed!r}"


def test_step_three_zero_names_every_value_a_human_approves():
    """D-022: 3.0(b) must list exactly the keys the pre-flight lets a human approve (T-045)."""
    step = runbook_text().split("### 3.0 ", 1)[1].split("\n### ", 1)[0]
    assert "--show-envelope" in step and "HUMAN_APPROVED" in step
    for entry in (key for key in MOTION_KEYS if key.approved_ok):
        leaf = entry.key.rsplit(".", 1)[-1]
        assert f"{leaf}{STATUS_SUFFIX}" in step, f"3.0 never tells the human to approve {entry.key}"
    assert "no agent makes this edit" in step.lower()


def test_every_phase_one_motion_step_has_its_own_scoped_preflight():
    """Each motion step is gated by `--for <its own step>`, not by the strictest table (D-022)."""
    text = runbook_text()
    for step in (name for name in STEPS if name.startswith("t0")):
        assert f"--for {step}" in text, f"the runbook never scopes the pre-flight to {step}"


def test_every_motion_step_says_what_moves_and_what_goes_into_the_build_log():
    """3.4 to 3.7 are the motion steps; each must name what moves and the BUILD_LOG entry (4.6)."""
    text = runbook_text()
    for step in ("3.4", "3.5", "3.6", "3.7"):
        body = text.split(f"### {step} ", 1)[1].split("\n### ", 1)[0]
        assert "BUILD_LOG entry" in body, f"step {step} does not say what to write to BUILD_LOG.md"
        assert "config_hash" in body, f"step {step} does not record the envelope in force"
    moved = text.split("### 3.4 ", 1)[1].split("### 3.8 ", 1)[0]
    assert moved.count("What moves:") + moved.count("what moved") >= 4


@pytest.mark.parametrize("command", python_commands(runbook_text()))
def test_every_python_command_in_the_runbook_runs(command: str):
    """Run each command with ``--help``: the tool exists, imports, and parses arguments (T-044)."""
    argv = command.split()
    assert argv[0] == ".venv/bin/python"
    assert "-c" not in argv, "the runbook must not contain inline -c snippets: they cannot be checked"
    assert PYTHON.is_file(), f"no venv interpreter at {PYTHON}"

    script = next((part for part in argv[1:] if part.endswith(".py")), None)
    if script in NO_ARGUMENT_TOOLS:
        pytest.skip(f"{script} takes no arguments; test_enable_session_rejects_arguments covers it")
    if script is not None:
        assert (REPO_ROOT / script).is_file(), f"{script} does not exist"

    result = subprocess.run(
        [str(PYTHON), *argv[1:], "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, (
        f"`{command} --help` exited {result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    assert result.stdout.strip(), f"`{command} --help` printed nothing"


def test_the_runbook_actually_contains_commands():
    commands = python_commands(runbook_text())
    assert len(commands) >= 15, f"only {len(commands)} commands found: {commands}"
    tools = {part for command in commands for part in command.split() if part.endswith(".py")}
    for expected in (
        "tools/hardware_checks/list_devices.py",
        "tools/hardware_checks/stream_stats.py",
        "tools/hardware_checks/brio_still.py",
        "tools/hardware_checks/session_preflight.py",
        "tools/hardware_checks/enable_session.py",
    ):
        assert expected in tools, f"the runbook never runs {expected}"


def test_enable_session_rejects_arguments():
    """The gate writer takes no arguments at all: any argv is exit 2 with a usage line (R1)."""
    script = REPO_ROOT / "tools" / "hardware_checks" / "enable_session.py"
    assert script.is_file()
    result = subprocess.run(
        [str(PYTHON), str(script), "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "takes no arguments" in result.stdout + result.stderr


@pytest.mark.parametrize("prefix", sorted(ID_SOURCES))
def test_every_referenced_id_exists_in_the_agents_file_that_owns_it(prefix: str):
    text = runbook_text()
    source = ID_SOURCES[prefix].read_text(encoding="utf-8")
    referenced = sorted(set(re.findall(rf"\b{prefix}-\d{{3}}\b", text)))
    assert referenced, f"the runbook references no {prefix}- item"
    defined = set(re.findall(rf"^#+\s+({prefix}-\d{{3}})\b", source, re.MULTILINE))
    missing = [item for item in referenced if item not in defined]
    assert not missing, f"{missing} referenced by the runbook but not defined in {ID_SOURCES[prefix]}"


#: Everything days 0 to 2 are allowed to run: none of these has a writer of any kind (T-018..T-020).
READ_ONLY_ENTRYPOINTS = frozenset({
    "-m pytest",
    "-m board.calibration",
    "tools/hardware_checks/list_devices.py",
    "tools/hardware_checks/stream_stats.py",
    "tools/hardware_checks/brio_still.py",
    "tools/hardware_checks/session_preflight.py",
})


def test_days_one_and_two_run_nothing_that_could_command_a_joint():
    """No session is opened before day 3, so nothing before it may reach a writer (R1)."""
    before_day3 = runbook_text().split("## Day 3", 1)[0]
    for command in python_commands(before_day3):
        argv = command.split()
        entry = f"{argv[1]} {argv[2]}" if argv[1] == "-m" else argv[1]
        assert entry in READ_ONLY_ENTRYPOINTS, f"day 1/2 command is not read-only: {command}"
    assert "no agent creates, edits, copies or restores" in runbook_text()
