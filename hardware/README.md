# hardware/

`session.enable` lives here when a human has enabled a motion session (CLAUDE.md section 4.6).
It is git-ignored, written only by `tools/hardware_checks/enable_session.py` run interactively
by a human, and read by `runtime/safety.py`. Agents never create, edit, copy, or restore it.
