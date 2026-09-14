# STATE.md (Fable rewrites this every cycle)

updated: 2026-09-14T14:20+07:00
phase: 0 closed (D-014). Phase 1 read-only measurements: oblique (Orbbec Ego) done and clean on kernel stamps (T-046, T-047, D-026); arm, hand, palm, glove, pose wait on H-002..H-004. Everything Phase 1-5 can build without hardware is built and accepted on mocks (D-023).
milestone: the first hardware days. Read docs/runbook_phase1.md; it is the whole procedure.
last_accepted_commit: 987f2fd (T-047 accepted; 43 tasks accepted; 902 tests green; audit D-026 clean)
remote: github.com/HashBrake/ludo-g1 (private), pushed after every Fable commit.
loop: STOPPED under R4(b) at 2026-09-14T14:20+07:00. Every remaining task (T-021..T-024) needs a human hardware action. Restart the Fable session ("continue") after doing any item below; it resumes from this file.

next three tasks (all wait on you): T-018/T-019/T-020 live 10-minute checks (need H-002, H-003, H-004), then T-021 first motion (needs Q-004, your envelope approval per D-022, a session), T-024 envelope test.

what Alois must do, in order (ten-second version):
1. Read-only day (no session): HARDWARE_NEEDED.md H-002 (robot LAN), H-003 (plug in Brio + DexH15), H-004 (glove + PICO; stop holosim-pcservice on port 63901), H-001 (two board stills). H-005 is resolved; nothing to do for the Ego. Then `.venv/bin/python tools/hardware_checks/session_preflight.py` and docs/runbook_phase1.md day 1 and 2.
2. Answer QUESTIONS.md Q-004 (which physical e-stop) and put it in config/safety.yaml session.checklist; approve the envelope: `.venv/bin/python tools/hardware_checks/session_preflight.py --show-envelope` and commit the HUMAN_APPROVED status lines (D-022, R3).
3. Q-001 Greennode credentials (~/.config/ludo-g1/env) and Q-002 disk (12 GB free) before any real training or recording.
4. Say HUMAN: in DECISIONS.md if you disagree with D-006, D-015, D-018, D-021, D-022, D-025.
Open questions: Q-001, Q-002, Q-004, Q-005, Q-006, Q-008, Q-010, Q-011. No blockers. No safety incident. No motion command has ever been sent.
