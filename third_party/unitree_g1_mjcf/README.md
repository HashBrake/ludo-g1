Canonical Unitree G1 robot model used by Teleopit runtime, GMR retargeting,
dataset FK validation, and mjlab training.

`g1_29dof.xml` is the default G1 XML entry point for this repository.
`g1_29dof_dex3.xml` is the Dex3 hand mesh variant used by
`unitree_g1_with_hands`. `g1_29dof_neck_o6.xml` keeps the same 29 actuated G1
joints and adds fixed O6 hands plus a fixed neck camera module, with simplified
inertials and collisions. This directory is distributed as an external asset
and is intentionally ignored by Git because it includes STL mesh files.

Mesh files are grouped by source module:

- `meshes/g1/`: stock Unitree G1 body and rubber hand meshes.
- `meshes/dex3/`: Dex3 fixed-hand meshes for `g1_29dof_dex3.xml`.
- `meshes/o6_left/`: left O6 hand meshes for `g1_29dof_neck_o6.xml`.
- `meshes/o6_right/`: right O6 hand meshes for `g1_29dof_neck_o6.xml`.
- `meshes/avp/`: neck camera module meshes for `g1_29dof_neck_o6.xml`.

```bash
python scripts/setup/download_assets.py --only robots
```
