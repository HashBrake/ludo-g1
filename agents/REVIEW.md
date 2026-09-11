# REVIEW.md (Fable verdicts per task or commit; newest at the bottom)

## T-001  ACCEPTED  (fable, 2026-09-11T18:52+07:00, commits 4255484, 3cd3738)
Re-ran every acceptance check: python 3.10.20, ruff clean, pytest 15 passed 1 skipped, tree clean, .venv and data ignored,
and my own scratch commit with a ruff error was rejected by the installed hook (exit 1, HEAD unchanged). Hook is identical to
tools/pre-commit.sh and invokes the venv binaries explicitly. conftest fails closed on gate errors, which is the right default.
Notes (not defects): venv is uv-managed 3.10.20 rather than /usr/bin/python3 3.10.12; acceptable, revisit only if the cp310
DexH15 wheel refuses to load in T-002. requirements.txt pins transitives too; fine.

## T-004  ACCEPTED  (fable, 2026-09-11T20:25+07:00, commits 956147a, 628361d, merged as 6709700)
Re-ran in the worktree: ruff clean; tests/test_clock.py 9 passed with the printed skew line p50 2.982 ms, p99 6.701 ms,
max 7.799 ms (seed 20260911, 1800 frames, 10 ms tolerance so every frame also passed align); full suite 24 passed 1 skipped.
Read runtime/clock.py and runtime/log.py in full: bisect nearest with tie-to-older, align names every failing stream, shift
copies rather than mutates, skew definition matches the guidance and docs/clock.md states it. The optional `instants` and
`tolerance_ns` parameters on skew_stats are justified by the acceptance test itself; kept.
Note (not a defect): runtime/log.py docstring says stderr but PrintLoggerFactory writes stdout; harmless until a long-running
process cares, fix when the heartbeat writer lands.

## T-002  ACCEPTED  (fable, 2026-09-11T20:25+07:00, commits ac4fcc5, 07db06b)
Re-ran: ruff clean; tests/test_docs_sdks.py checked 157 path:line references (129 unique), 9 passed; list_devices.py exits 0
with nothing attached. Spot-checked the two claims that change the project against the sources myself: pico4_provider.py
_read_controller_state does drop controller.pose (lines 634-644), and g1_arm7_sdk_dds_example.py publishes rt/arm_sdk with
motor_cmd[29].q as the enable weight (lines 64, 107, 135). list_devices.py only issues VIDIOC_QUERYCAP on O_RDONLY fds and
never opens a serial port. No motion path, no session file, no third_party edits (git diff confirms).
Consequences are recorded as D-006..D-009; the requirements.txt file:// absolute path for pxdex is accepted for now and
tracked under Q-007.
