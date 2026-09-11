# QUESTIONS.md (non-blocking; agents proceed on the stated assumption until answered)

## Q-001  Greennode access  (fable, 2026-09-11T18:35+07:00)  OPEN
No credentials at ~/.config/ludo-g1/env. Please create that file with: GREENNODE_HOST, GREENNODE_USER, GREENNODE_SSH_KEY
(path), GREENNODE_REMOTE_ROOT, and note the instance type and GPU.
Assumption meanwhile: cloud/greennode.sh is built and tested with a local fake transport; the real one-minute dummy job
(Phase 0 exit check) waits for the file.

## Q-002  Dataset disk  (fable, 2026-09-11T18:35+07:00)  OPEN
/home has 15 GB free; the brief targets >= 500 GB under ~/ludo-g1/data. Options: an external SSD mounted and symlinked to
data/raw, or a different partition. Assumption meanwhile: Phase 0 and mock work fit in 15 GB; Phase 2 real recording will not.

## Q-003  Unitree SDK2 Python is not in the folder  (fable, 2026-09-11T18:35+07:00)  OPEN (acting on the assumption)
Update 2026-09-11T20:25: T-002 installed unitree_sdk2py 1.0.1 from the public upstream commit f7a5526 (same as the lab's
~/meta-quest-teleoperate checkout). Say HUMAN: if you object.
The vendored teleop fork's unitree_sdk2_python submodule is empty; the fork drives the G1 through a C++ DDS bridge
(third_party/g1_pico_teleop/third_party/g1_bridge_sdk). May Opus pip-install unitree_sdk2py from the upstream GitHub repo
into the project venv? Assumption meanwhile: yes, pinned to a commit, recorded in docs/sdks.md; the g1_bridge_sdk is the
fallback path.

## Q-004  Physical e-stop path for a rig-mounted G1 with legs locked  (fable, 2026-09-11T18:35+07:00)  OPEN
Which action is the e-stop during sessions: the Unitree remote damp chord (may be inert under custom low-level control per the
lab's prior notes), the Unitree app, or a power cut? The session checklist in enable_session.py will name it.
Assumption meanwhile: the checklist says "e-stop within reach" without naming it; Phase 1 will not start motion until answered.

## Q-005  Standalone PxCapPro SDK  (fable, 2026-09-11T18:35+07:00)  OPEN
third_party/pxcap_pro_sdk.md documents a pxhandsdk .deb with `from pxhandsdk import pxcappro`, but only the PyInstaller
teleop bundle is present. Do you have the .deb, or should the glove be read through the bundle's Python runtime?
Assumption meanwhile: Opus inventories the bundle and reports which route works read-only.

## Q-006  Horse arrow orientation  (fable, 2026-09-11T18:35+07:00)  OPEN
Assumed the engine does not care about the arrow direction on a horse. Confirm with the engine team.

## Q-007  Folder name  (fable, 2026-09-11T18:35+07:00)  OPEN
The brief says ~/ludo-g1; the repo is at ~/Desktop/ludo-g1. Agents use the actual path. No action needed unless you move it.

## Q-008  Orbbec depth: no usable Python SDK  (opus, 2026-09-11T20:05+07:00)  OPEN
The PyPI `pyorbbecsdk` 1.3.2 wheel is mis-packaged (tagged cp310-manylinux, contains only a macOS
`.cpython-311-darwin.so` plus `.dylib`s), so `import pyorbbecsdk` fails on this laptop; no Orbbec SDK exists
under third_party/ or /usr/lib. The Orbbec Ego does enumerate as a plain UVC stereo camera
(`/dev/video4` "ORBBEC: Ego left", `/dev/video6` "ORBBEC: Ego right"), so RGB works today through OpenCV.
Options: (a) accept RGB-only from the oblique camera and drop the depth cue; (b) build pyorbbecsdk from
Orbbec's GitHub source against the matching OrbbecSDK release; (c) compute stereo depth from the two Ego
streams with OpenCV. Evidence and references: docs/sdks.md section 8.2.
Assumption meanwhile: (a) — `oblique` is an RGB observation, as CLAUDE.md 5.3 already specifies; no depth
tensor enters the policy, so nothing downstream changes if depth never arrives.

## Q-009  Which cv2 wheel do we keep?  (opus, 2026-09-11T20:05+07:00)  DECIDED by Fable, D-008 (keep opencv-python)
`unitree_sdk2py` depends on `opencv-python` (GUI build), while T-001 pinned `opencv-python-headless`. Both are
now installed at the same upstream version 5.0.0.93 and `import cv2` works, but two distributions owning the
same `cv2/` directory is a packaging hazard. Options: drop the headless pin and keep `opencv-python`, or
install unitree_sdk2py with `--no-deps` and pin its real deps by hand.
Assumption meanwhile: both stay pinned in requirements.txt exactly as uv resolved them (the environment is
reproducible and `uv pip install -r requirements.txt --dry-run` reports "no changes"); Fable decides.

## Q-010  Teleop rest pose and elbow configuration  (fable, 2026-09-12T00:40+07:00)  OPEN
config/robot.yaml `teleop.rest_pose_rad` is all zeros (UNMEASURED). It is the IK posture target, so it decides which elbow
configuration the arm settles into over the board. Phase 1 should pick it on the rig with the table in place (elbow low and
outboard, away from the operator side). Assumption meanwhile: zeros; nothing downstream depends on the value yet.

## Q-011  Jetson Orin NX for inference  (fable, 2026-09-11T23:16+07:00)  OPEN
D-019: the laptop CPU runs the diffusion policy at 0.8 s per inference against a 100 ms budget. Is the Orin NX 16 GB free for
this project (it hosted the teleop relay before), and which JetPack is on it? Assumption meanwhile: it is available; the
export bundle from T-029 is the artefact that would move there; the port task is written only after Q-011 is answered.

## Q-002 update  (fable, 2026-09-11T23:16+07:00)
/home now has 12 GB free. One diffusion checkpoint plus its bundle is 2.3 GB (T-029). Even smoke training on this laptop
is now disk-limited; an external SSD or a second partition for data/ is needed before Phase 2 recording.
