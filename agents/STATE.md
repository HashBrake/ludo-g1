# STATE.md (Fable rewrites this every cycle)

updated: 2026-09-12T11:14+07:00
phase: 0 closed (D-014). Everything Phase 1-5 can build without hardware is built and accepted on mocks: read-only drivers for every device, teleop loop with clutch, recorder (LeRobot v3), operator UI, dataset viewer, dataset loader, Diffusion Policy and ACT wrappers with training, export, eval runner, watchdog, network engine client, board perception, session pre-flight, Phase 1 runbook.
milestone: the first hardware days. Read docs/runbook_phase1.md; it is the whole procedure.
last_accepted_commit: c998dd6 (41 tasks accepted, T-001..T-045 except the four motion tasks; 892 tests green; audit D-023 clean)
loop: STOPPED under R4(b) at 2026-09-12T11:14+07:00. Every remaining task (T-021..T-024) needs a human hardware action. Restart the Fable session after doing any item below; it resumes from this file.

next three tasks (all wait on you): T-018/T-019/T-020 live 10-minute checks (need H-002, H-003, H-004), then T-021 first motion (needs Q-004, your envelope approval per D-022, a session), T-024 envelope test.

what Alois must do, in order (ten-second version):
1. Read-only day (no session): HARDWARE_NEEDED.md H-002 (robot LAN), H-003 (plug in Brio + DexH15), H-004 (glove + PICO; stop the leftover holosim-pcservice that holds port 63901), H-001 (two board stills). Then: `.venv/bin/python tools/hardware_checks/session_preflight.py` and follow docs/runbook_phase1.md day 1 and day 2.
2. Answer QUESTIONS.md Q-004 (which physical e-stop) and put it in config/safety.yaml session.checklist; approve the envelope: `.venv/bin/python tools/hardware_checks/session_preflight.py --show-envelope` and commit the HUMAN_APPROVED status lines (D-022, R3).
3. Q-001 Greennode credentials (~/.config/ludo-g1/env) and Q-002 disk (12 GB free; a diffusion checkpoint needs 10 GB) before any real training or recording.
4. Say HUMAN: in DECISIONS.md if you disagree with D-006 (own IK instead of Teleopit), D-015 (LeRobot v3), D-018 (clutch + step cap), D-021 (ACT/diffusion_small fit the laptop), D-022 (human-approved envelope).
Open questions: Q-001, Q-002, Q-004, Q-005 (deb optional), Q-006, Q-008, Q-010, Q-011 (Orin now optional). No blockers. No safety incident. No motion command has ever been sent.
