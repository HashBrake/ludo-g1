# REVIEW.md (Fable verdicts per task or commit; newest at the bottom)

## T-001  ACCEPTED  (fable, 2026-09-11T18:52+07:00, commits 4255484, 3cd3738)
Re-ran every acceptance check: python 3.10.20, ruff clean, pytest 15 passed 1 skipped, tree clean, .venv and data ignored,
and my own scratch commit with a ruff error was rejected by the installed hook (exit 1, HEAD unchanged). Hook is identical to
tools/pre-commit.sh and invokes the venv binaries explicitly. conftest fails closed on gate errors, which is the right default.
Notes (not defects): venv is uv-managed 3.10.20 rather than /usr/bin/python3 3.10.12; acceptable, revisit only if the cp310
DexH15 wheel refuses to load in T-002. requirements.txt pins transitives too; fine.
