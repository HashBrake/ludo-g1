# STATE.md (Fable rewrites this every cycle)

updated: 2026-09-12T00:40+07:00
phase: 0 (discovery and scaffolding, no hardware motion)
milestone: Phase 0 exit checks (CLAUDE.md section 6, Phase 0 "Verify")
last_accepted_commit: bdb35bb (T-013 arm IK accepted; 327 tests green)
loop: running

next three tasks: T-016 mock controller loop (starting), T-010 real camera driver (in progress, parallel), T-014 worktree helper, then T-015 Phase 0 report

what Alois should look at (ten-second version):
- QUESTIONS.md Q-001 (Greennode credentials), Q-002 (disk: 15 GB free vs 500 GB target), Q-003 (may I pip-install unitree_sdk2py), Q-004 (name the physical e-stop)
- HARDWARE_NEEDED.md H-001: a Brio still of the empty board, whenever convenient
- DECISIONS.md D-006: assumption A5 in the brief is refuted (the Pico teleop stack cannot give arm targets from one controller); we build our own 8-DoF IK. Say HUMAN: if you disagree.
- HARDWARE_NEEDED.md H-002 (robot LAN up), H-003 (plug in Brio, DexH15, glove once for enumeration)
- QUESTIONS.md Q-008 (Orbbec depth route), Q-010 (teleop rest pose); Q-009 was decided by Fable (D-008)
- DECISIONS.md D-003: SDK folders were moved into third_party/; big binaries are on disk but git-ignored
