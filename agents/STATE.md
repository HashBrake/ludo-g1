# STATE.md (Fable rewrites this every cycle)

updated: 2026-09-12T07:19+07:00
phase: 0 closed (D-014). Phase 1 read-only drivers are being built against fakes (T-018 done, T-019 next) so only their live checks wait for H-002/H-003. Phase 2/3 non-hardware work continues on mocks.
milestone: first real motion (T-021) needs Q-004 answered, a human-enabled session, T-018 live, and T-033 (done). Phase 3 gate: the real Greennode training run (cloud/greennode.sh train, docs/cloud.md) waits for Q-001 credentials and a real dataset; --device cuda has never executed.
last_accepted_commit: merge of wt/t041 (36 tasks accepted; ~780 tests green)
loop: running

next three tasks: T-042 module splits (in progress), T-040 policy termination head (in progress, parallel), then Phase 1 live checks as soon as H-002/H-003/H-004 are done

what Alois should look at (ten-second version):
- QUESTIONS.md: Q-001 Greennode credentials (blocks real training), Q-002 disk (12 GB free; a full diffusion checkpoint with EMA and Adam state is ~4.7 GB), Q-004 physical e-stop (blocks first motion), Q-011 Orin NX (now optional: ACT 91 ms and diffusion_small 80 ms fit the 100 ms budget on this laptop, D-021).
- HARDWARE_NEEDED.md: H-002 robot LAN up, H-003 plug in Brio and DexH15, H-004 glove and PICO (includes stopping the leftover holosim-pcservice that holds port 63901); H-001 board still. All read-only, no session.
- DECISIONS.md: D-006 (A5 refuted, own IK), D-015 (LeRobot v3), D-018 (clutch + first-command step cap now in the envelope), D-019/D-020 (inference budget). Say HUMAN: if you disagree.
- Phase 0 report: last "Phase 0 report" section of agents/BUILD_LOG.md.
