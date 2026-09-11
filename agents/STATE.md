# STATE.md (Fable rewrites this every cycle)

updated: 2026-09-12T03:07+07:00
phase: 0 closed (D-014). Phase 1 read-only drivers are being built against fakes (T-018 done, T-019 next) so only their live checks wait for H-002/H-003. Phase 2/3 non-hardware work continues on mocks.
milestone: first real motion (T-021) needs Q-004 answered, a human-enabled session, T-018 live, and T-033 (done). Phase 3 gate: the real Greennode training run (cloud/greennode.sh train, docs/cloud.md) waits for Q-001 credentials and a real dataset; --device cuda has never executed.
last_accepted_commit: merge of wt/t018 (28 tasks accepted; 533 tests green)
loop: running

next three tasks: T-035 training loop completeness + thread sweep (in progress), T-019 DexH15 read-only driver (in progress, parallel), T-020 glove and controller pose drivers

what Alois should look at (ten-second version):
- QUESTIONS.md: Q-001 Greennode credentials (blocks real training), Q-002 disk (12 GB free; a diffusion checkpoint is 2.3 GB, ACT 0.6 GB), Q-004 physical e-stop (blocks first motion), Q-011 Orin NX for inference (laptop CPU: ACT ~150 ms, diffusion ~560 ms per step at 4 threads, budget 100 ms; D-019, D-020).
- HARDWARE_NEEDED.md: H-002 robot LAN up, H-003 plug in Brio, DexH15, glove once; H-001 board still. All read-only, no session.
- DECISIONS.md: D-006 (A5 refuted, own IK), D-015 (LeRobot v3), D-018 (clutch + first-command step cap now in the envelope), D-019/D-020 (inference budget). Say HUMAN: if you disagree.
- Phase 0 report: last "Phase 0 report" section of agents/BUILD_LOG.md.
