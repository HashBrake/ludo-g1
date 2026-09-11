> Historical copy (2026-08-18) from the previous repo layout, kept verbatim for the record.
> The old HoloMotion-era tree it describes lives on branch `archive/holomotion-2026-08-18`.
> Current end-to-end instructions are in the root README.md.

# PROJECT STATE — 2026-08-18

Single current-truth document. Where this contradicts an older dated document
in this folder, **this file wins** (the older files are kept as records).

---

## 1. What is active: the Teleopit stack

Since 2026-08-14 the project runs on
[BotRunner64/Teleopit](https://github.com/BotRunner64/Teleopit) — full-body
PICO 4 teleop for the G1 — after HoloMotion was parked and the SONIC migration
stalled on a blocked download (§4).

**Verified working (08-14):** PICO sim2sim (operator tracked correctly in
MuJoCo) and `standalone_standing --dry-run` against the real robot (50 Hz,
stable targets). **08-17:** waist unlocked (§3), audit fixes deployed, sim A/B
shows the unlocked policy dramatically more stable.

**Laptop setup** (`~/Teleopit`, branch `local-fixes-2026-08-17` — 11 commits
ahead of upstream master, exported to `teleopit-export/` in this repo):
- conda env `teleopit` (py3.11, CPU torch, mujoco 3.11); assets +
  `track_g1.onnx` via `scripts/setup/download_assets.py` (modelscope).
- `g1_bridge_sdk` built (needed conda eigen +
  `CPLUS_INCLUDE_PATH=~/miniconda3/envs/teleopit/include`).
- Real run: `python scripts/run/run_sim2real.py` per
  `docs/docs/tutorials/pico-sim2real.md`, interface `enx000ec6c3d44a`.

**Headset:** PicoBridge APK v0.2.1 (`com.picobridge.app`) on the PICO 4. The
app UI is status-only — it auto-connects on UDP discovery (port 29888 → TCP
63901). iPhone hotspot was needed once for PICO entitlement (cached now).

**Key infra:**
- The pico-bridge discovery broadcast cannot cross the Orin NAT, and its
  `--bridge-advertise-ip` path wrongly assumes /24. Fix =
  `/home/unitree/discovery_relay.py` on the Orin (crontab `@reboot`),
  broadcasting `192.168.123.2|63901` into `10.42.0.255`. Headset on the
  `g1-teleop` AP then connects automatically.
- The laptop's `holosim-pcservice` (XRoboToolkit) is a systemd **user** unit
  and squats port 63901 — `systemctl --user stop holosim-pcservice` before
  every Teleopit session.

**Remote state machine (Unitree remote, real robot):**
Start = STANDING (from IDLE/DAMPING) · Y = MOCAP · X = back to STANDING ·
**L1+R1 = e-stop damp**.

**Software e-stop:** `scripts/teleopit_estop.sh` — kills the Python process
streaming LowCmd; firmware releases motors in ~2 s (proven 08-14, ankle
chatter event). `LocoClient.Damp()` does NOT work while the bridge holds the
robot (3× "DAMP UNCONFIRMED" on record) — it is attempted last, best-effort.
Robot must be tethered when e-stopping or a fall is expected.

## 2. Robot-side deployed state (Orin)

- Teleopit fixes deployed to the Orin match the laptop branch. Rollback:
  `~/teleopit_rollback_2026-08-17.tgz` on the Orin (or set
  `action_scale[14]: 0.0` to re-freeze the waist).
- `discovery_relay.py` in `unitree`'s crontab (`@reboot`).
- The HoloMotion Docker image (v1.4.0, JP5.1) is still on the Orin, parked
  but functional, with the patch overlay from `scripts/robot_install.sh`.

## 3. Waist pitch — RESOLVED 2026-08-17 (it was the waist LOCK)

The "mechanical jam" of `waist_pitch` (joint 14: stalls at 33 Nm, fault 512,
immovable by hand) was the **G1 waist lock** — a mobile-app config setting
PLUS physical fastener screws left by previous developers. Unlocking the app
setting and removing the screws restored the joint immediately (<2 Nm both
directions, probed with `scripts/waist_pitch_probe.py`).

- `action_scale[14]` restored to **0.4386** on laptop AND Orin.
- Lesson (do not lose): a locked joint and a jammed joint produce identical
  telemetry; the factory controller quietly routes around locked joints.
  **Check config/lock state and ask what previous developers changed before
  diagnosing hardware damage.** (They had also altered motor offsets.)
- Watch waist torque/temperature on the first unlocked sessions regardless.

## 4. Parked tracks

**HoloMotion v1.4.0** — parked 08-14, fully functional to study. The complete
failure catalog of its retargeting middleware is `Docs/HANDOFF.md` (08-12) and
the 08-14 audit (`Docs/HANDOFF-2026-08-14-audit-patches.md`, patches P1–P5).
Note the deployed C++ binary is the STOCK Jul-16 build; rebuilds require the
CRC strict-aliasing fix (P5) now wired into `scripts/deploy_cpp_patch.sh`.

**SONIC / GR00T-WholeBodyControl** — parked. Laptop side done and verified
(`~/GR00T-WholeBodyControl`, `.venv_teleop`). Single remaining gate: JetPack
6.2 reflash of the Orin (TensorRT 10.7 EXACTLY). The 9.94 GB
`Jetpack_6.2_nx.tar.bz2` download is quota-blocked by Google Drive (~677 MB
in, auto-resume loop was running; log `/media/alois/SONIC-FLASH/gdown.log`).
Do not resume unless asked. Plan: `Docs/MIGRATION-SONIC.md`.

## 5. Pending work (Teleopit track)

- `preflight.py` — pre-session automated checks (not started).
- Fall detector / stale-hold escalation / thermal supervisor (not started).
- First real unlocked-waist sessions: monitor waist tau/temp.
- Consider upstreaming the 11 local fixes to BotRunner64/Teleopit.

## 6. Known traps (cost real time — read before debugging)

- **pkill/pgrep self-match**: the eval'd Bash command contains the pattern —
  kill and launch in SEPARATE shell invocations, bracket-trick the pattern.
- **Five-copy trap (HoloMotion/ros2)**: ros2 runs an extensionless entry-point
  COPY of the Python node — editing the `.py` does nothing until the copy is
  refreshed. Same class of trap as the TWO copies of
  `g1_29dof_holomotion.yaml` in the image (src/config + install/share; the
  binary reads install/share — patch both, then verify the runtime log says
  `Joint limit scales - Position: 1.000000`).
- **Network**: NEVER join the laptop to the `g1-teleop` AP (it breaks the
  robot LAN routing). Headset→laptop goes through the robot NAT to
  `192.168.123.2`. USB tether to a phone does not carry the body-tracking
  stream. Corporate Wi-Fi hijacks the headset mid-session (auto-hop).
- **Cameras**: on this laptop `/dev/video4` is the RealSense DEPTH node
  (near-black frames); RGB is `/dev/video6`.
- **Factory remote under custom control**: L2+B (factory damp) is DEAD while
  custom control runs. See the per-stack button maps in
  `DEVELOPER-HANDOFF.md`.
