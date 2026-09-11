# STATE.md (Fable rewrites this every cycle)

updated: 2026-09-11T23:16+07:00
phase: 0 closed (audit D-014); Phase 1 tasks created (T-018..T-024); non-hardware Phase 2/3 work continues (T-025..T-027)
milestone: Phase 1 read-only bring-up needs H-002 (robot LAN) and H-003 (plug in Brio, DexH15, glove); Phase 1 motion needs Q-004 answered and a human-enabled session
last_accepted_commit: merge of wt/t032 (23 tasks accepted; suite green)
loop: running

next three tasks: T-033 clutch + first-command step cap (in progress, P0 before any motion), T-030 ACT baseline (in progress, parallel), T-034 observation history

what Alois should look at (ten-second version):
- Phase 0 is done except two human-gated exit checks: Greennode real round trip (Q-001 credentials) and a Brio still of the board (H-001).
- HARDWARE_NEEDED.md H-002 (bring the robot LAN up), H-003 (plug in Brio, DexH15 and glove once for enumeration). Both read-only, no session.
- QUESTIONS.md Q-004 (name the physical e-stop) blocks the first motion task T-021. Q-002 (disk: 12 GB free; a checkpoint is 2.3 GB) now blocks even local smoke training. Q-011 (Orin NX for inference) is new: the laptop CPU runs the policy 8x too slow (D-019).
- DECISIONS.md D-006 (brief assumption A5 refuted; we solve our own arm IK), D-015 (dataset format is LeRobot v3), D-014 (Phase 0 audit). Say HUMAN: if you disagree with any.
- The Phase 0 report is the last section of agents/BUILD_LOG.md.
