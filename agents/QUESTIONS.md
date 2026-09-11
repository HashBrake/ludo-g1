# QUESTIONS.md (non-blocking; agents proceed on the stated assumption until answered)

## Q-001  Greennode access  (fable, 2026-09-11T18:35+07:00)  OPEN
No credentials at ~/.config/ludo-g1/env. Please create that file with: GREENNODE_HOST, GREENNODE_USER, GREENNODE_SSH_KEY
(path), GREENNODE_REMOTE_ROOT, and note the instance type and GPU.
Assumption meanwhile: cloud/greennode.sh is built and tested with a local fake transport; the real one-minute dummy job
(Phase 0 exit check) waits for the file.

## Q-002  Dataset disk  (fable, 2026-09-11T18:35+07:00)  OPEN
/home has 15 GB free; the brief targets >= 500 GB under ~/ludo-g1/data. Options: an external SSD mounted and symlinked to
data/raw, or a different partition. Assumption meanwhile: Phase 0 and mock work fit in 15 GB; Phase 2 real recording will not.

## Q-003  Unitree SDK2 Python is not in the folder  (fable, 2026-09-11T18:35+07:00)  OPEN
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
