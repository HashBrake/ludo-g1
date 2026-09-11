# STATE.md (Fable rewrites this every cycle)

updated: 2026-09-11T21:53+07:00
phase: 0 closed (audit D-014); Phase 1 tasks created (T-018..T-024); non-hardware Phase 2/3 work continues (T-025..T-027)
milestone: Phase 1 read-only bring-up needs H-002 (robot LAN) and H-003 (plug in Brio, DexH15, glove); Phase 1 motion needs Q-004 answered and a human-enabled session
last_accepted_commit: merge of wt/t026 (19 tasks accepted; suite green)
loop: running

next three tasks: T-027 dataset loader (starting), then T-018/T-019/T-020 read-only device bring-up when H-002/H-003 are done; Fable to add more non-hardware tasks

what Alois should look at (ten-second version):
- Phase 0 is done except two human-gated exit checks: Greennode real round trip (Q-001 credentials) and a Brio still of the board (H-001).
- HARDWARE_NEEDED.md H-002 (bring the robot LAN up), H-003 (plug in Brio, DexH15 and glove once for enumeration). Both read-only, no session.
- QUESTIONS.md Q-004 (name the physical e-stop) blocks the first motion task T-021. Q-002 (disk: 14 GB free vs 500 GB target) blocks real recording.
- DECISIONS.md D-006 (brief assumption A5 refuted; we solve our own arm IK), D-015 (dataset format is LeRobot v3), D-014 (Phase 0 audit). Say HUMAN: if you disagree with any.
- The Phase 0 report is the last section of agents/BUILD_LOG.md.
