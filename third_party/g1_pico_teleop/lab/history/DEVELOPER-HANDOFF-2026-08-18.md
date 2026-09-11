> Historical copy (2026-08-18) from the previous repo layout, kept verbatim for the record.
> Paths like `scripts/teleopit_estop.sh` refer to the old tree (branch `archive/holomotion-2026-08-18`); the Teleopit-era equivalents are under `lab/`.

# DEVELOPER HANDOFF — G1 PICO full-body teleoperation

For a developer taking this project over. Read `PROJECT_STATE.md` first for
what is currently true; this file is the stable background: hardware, network,
safety, how to run a session, and where everything lives.

---

## 1. Hardware inventory

| Item | Details |
|---|---|
| Robot | Unitree G1 EDU Ultimate, 29 DoF, Inspire FTP hands (**remove the hands** for full-body policy sessions — maker's guidance, policies trained without distal hand mass) |
| Robot PC2 | Jetson Orin NX, JetPack 5.1, `unitree@192.168.123.164` |
| Operator rig | PICO 4 headset + 2 PICO Motion Trackers (ankles). A waist tracker exists but is deliberately unused |
| Laptop | ThinkPad, Ubuntu 22.04, Intel Core Ultra 5 225H, 22 GB RAM, **no NVIDIA GPU** (this constraint shaped everything: CPU-only IK/torch, no visualizers) |
| Robot LAN | Laptop `192.168.123.2` via USB-GbE adapter `enx000ec6c3d44a` (NM connection `robot-lan-usb`); robot `192.168.123.164` |
| Safety rig | Gantry/tether + human spotter — **mandatory** |

## 2. Networks (get this wrong and nothing works)

- **Laptop ↔ robot**: Ethernet cable, `192.168.123.x`.
- **Headset ↔ laptop**: headset joins the robot-hosted AP `g1-teleop`
  (10.42.0.1 on the Orin), traffic NATs through the Orin to the laptop at
  `192.168.123.2`. The Teleopit discovery relay
  (`/home/unitree/discovery_relay.py`, crontab `@reboot` on the Orin)
  broadcasts `192.168.123.2|63901` into `10.42.0.255` so the headset app
  auto-connects.
- **NEVER join the laptop to the `g1-teleop` AP** — it breaks robot-LAN
  routing.
- Phone hotspot: only ever needed once (PICO app entitlement, now cached).
  USB tethering does NOT carry the body stream. Corporate Wi-Fi will hijack
  the headset mid-session because `g1-teleop` has no internet — forget the
  network on the headset.

## 3. Safety (non-negotiable)

1. Gantry + spotter with the Unitree remote in hand, every powered session.
2. **The remote is the e-stop, not the SDK.** `LocoClient.Damp()` returned
   `None` (no reply) at a real emergency while the bridge was commanding the
   robot. `scripts/g1_estop.py` now treats `None` as UNCONFIRMED and screams
   for the remote.
3. The proven software kill for Teleopit sessions is
   `scripts/teleopit_estop.sh`: it kills the process streaming LowCmd, the
   firmware notices the stream died and damps in ~2 s. The robot WILL go limp
   — it must be tethered.
4. Watchdogs are **alert-only** by design: auto-killing an untethered robot
   = guaranteed collapse. The operator decides.

**Remote button maps** (they differ per stack — misremembering these has
consequences):

| Context | Buttons |
|---|---|
| Teleopit state machine | **Start** = STANDING (from IDLE/DAMPING) · **Y** = MOCAP · **X** = back to STANDING · **L1+R1 = e-stop damp** |
| HoloMotion custom control | **Select** = damp+kill · **Y** = safe downshift to velocity stand · **L1 = TRAP: free-fall** (never press) · factory **L2+B is DEAD** here |
| Factory controller (no custom control) | **L2+B** = damp (never just B) · **L2+R2** = debug/dev mode |

## 4. Running a Teleopit session (the active stack)

Prerequisites once per boot:
```bash
systemctl --user stop holosim-pcservice   # it squats port 63901
```

1. Robot on, hung on the gantry, Ethernet in, remote in the spotter's hand,
   debug mode (L2+R2). Hands removed.
2. Laptop:
   ```bash
   cd ~/Teleopit && conda activate teleopit
   python scripts/run/run_sim2real.py    # per docs/docs/tutorials/pico-sim2real.md
   # interface: enx000ec6c3d44a
   ```
3. Headset: open PicoBridge (v0.2.1) on the `g1-teleop` Wi-Fi. It
   auto-connects via the discovery relay — the UI is status-only, nothing to
   click except one slider.
4. Drive with the remote per the Teleopit button map above. First unlocked-
   waist sessions: watch waist torque/temperature.
5. Emergency: spotter hits **L1+R1**; if software kill is needed,
   `scripts/teleopit_estop.sh`.

Practice paths that don't touch the robot: sim2sim
(`teleopit/configs/pico4_sim.yaml` path) and `standalone_standing --dry-run`
against the real robot (reads state, prints would-be commands).

## 5. Where everything lives

| Location | Contents |
|---|---|
| `~/full-body-teleoperation` (this repo) | Scripts, docs, HoloMotion vendored, benches, exported Teleopit patches |
| `~/Teleopit` | ACTIVE stack, branch `local-fixes-2026-08-17` (exported to `teleopit-export/` here) |
| `~/GR00T-WholeBodyControl` | Parked SONIC migration target (see `MIGRATION-SONIC.md`) |
| Orin `~/` | `robot_install.sh`, `humanoid_policy_patched/` overlay, `discovery_relay.py`, `teleopit_rollback_2026-08-17.tgz` |
| Orin Docker | `horizonrobotics/holomotion:v1.4.0-orin-jp5.1-arm64` (parked, functional) |

## 6. History in 10 lines (why the repo looks like this)

1. Project started on **HoloMotion v1.4.0** (PICO → XRoboToolkit → laptop IK
   retarget → ZMQ 50 Hz reference → on-robot ONNX policy → 500 Hz PD).
2. The robot hardware was repeatedly proven healthy; every real failure was in
   the **retargeting middleware** or **stock config hazards** (worst:
   `limit_scales: 2.0` doubles joint ranges and turns dirty references into
   violent chatter). Full catalog: `HANDOFF.md` (08-12).
3. An independent audit (08-14) produced patches P1–P5, including the
   C++-rebuild blocker (CRC strict-aliasing) — see
   `HANDOFF-2026-08-14-audit-patches.md`. Deploy scripts ship them all.
4. A migration to **NVIDIA SONIC** was prepared but stalled on the JetPack 6.2
   download quota (`MIGRATION-SONIC.md`).
5. The project pivoted to **Teleopit** (08-14), which passed sim2sim and a
   real dry-run same day.
6. A long-standing waist_pitch "jam" turned out to be the **factory waist
   lock** left by previous developers (08-17) — joint healthy, policy now
   dramatically more stable in sim A/B. Lesson recorded in
   `PROJECT_STATE.md` §3.

## 7. Documentation map

- `PROJECT_STATE.md` — current truth, pending work, trap list. **Start here.**
- `RUNBOOK.md` / `INSTRUCTIONS.md` / `SETUP_STATUS.md` — HoloMotion-track
  operations (parked but the safety/rigging discipline transfers).
- `HANDOFF.md` — the retargeting-middleware research brief (08-12). The
  deepest document; read it before touching any reference-stream code.
- `HANDOFF-2026-08-14-audit-patches.md` — audit + patch set P1–P5.
- `AUDIT-2026-08-11*.{md,json}` — earlier audit layers.
- `HANDOFF-archive-*.md` — daily archives, kept verbatim.
- `MIGRATION-SONIC.md` — how to resume the SONIC track if chosen.
- `../teleopit-export/README.md` — the exported Teleopit commits and how to
  reconstruct the branch.
- `../bench/RESEARCH.md` — laptop bench findings (IK timing, CRC aliasing).
