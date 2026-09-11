# Policy

`policy/` holds what learns: the dataset the models are trained on, the models themselves (Diffusion
Policy and the ACT baseline, CLAUDE.md 5.7), training and export. Only the dataset exists so far
(T-027); this page grows with the rest.

Nothing in `policy/` can move the robot. It reads recorded sessions and returns tensors; the only
code that sends anything is `runtime/controller.py`, through `runtime/safety.py` (R1, R3).

## `policy/dataset.py`

```python
from policy.dataset import LudoDataset, split_cell_pairs

sessions = [Path("data/raw/20260912T090000")]
train_pairs, held_out = split_cell_pairs(sessions, 0.2, seed=0)
data = LudoDataset(sessions, augment=True, seed=0, split=train_pairs)
sample = data[0]
```

One `LudoDataset` wraps one or more sessions written by `teleop/recorder.py` — a LeRobot v3.0 dataset
(D-011, D-015) plus the `episodes_meta.jsonl` sidecar — and yields the observation of CLAUDE.md 5.3
with a chunk of the actions that followed it:

| key | shape | dtype | meaning |
|---|---|---|---|
| `top` | `(5, h, w)` | float32 | Brio RGB in [0, 1], then the two goal heatmap channels |
| `oblique` | `(3, h, w)` | float32 | Orbbec Ego RGB in [0, 1] |
| `palm` | `(3, h, w)` | float32 | DexH15 palm RGB in [0, 1] |
| `state` | `(9,)` | float32 | 7 arm joints, waist yaw, pinch (`config/robot.yaml` `action_order`) |
| `task_id` | `(3,)` | float32 | one-hot over `config/training.yaml` `observation.task_ids` |
| `action` | `(chunk, 9)` | float32 | absolute joint targets at `rates.dataset_hz` |
| `action_mask` | `(chunk,)` | float32 | 1 where the action was recorded, 0 where the tail was padded |

`chunk` defaults to `diffusion.chunk` (16); the ACT baseline passes `chunk=32` (`act.chunk`). The 15
raw DexH15 joints and the 17 glove channels are in the dataset but are not in a sample: CLAUDE.md 5.3
records them and does not feed them to the policy.

### The goal channels are rendered, not stored

A `(2, 480, 640)` float32 heatmap is 2.4 MB per frame, so the recorder stores the two cell pixels
once per episode in the sidecar instead (`docs/teleop.md`). This module re-renders them with
`runtime/goal.py`'s `GoalRenderer` — the same renderer `runtime/controller.py` will use live — once
per episode, cached, and concatenates them onto the `top` RGB. A channel whose cell does not exist (a
ROLL addresses neither) is all zeros: the absence of a goal, not a goal at the origin.

If the sidecar's frame size differs from the stored image size (it does in the tests, which shrink the
cameras), the channels are bilinearly resized onto the image, so the gaussian stays on its cell.

### Every frame is a sample, and the tail is padded

A sample at frame `f` carries actions `f … f + chunk - 1`. Past the end of its episode the chunk
repeats that episode's last action and `action_mask` is 0 there; lerobot's own `delta_timestamps`
clamping does both, and a chunk never reaches into the next episode or the next session. The
alternative — dropping the last `chunk - 1` frames of every episode — would throw away precisely the
end of every primitive (the release, the retreat), which is the part the policy has least of and
needs most. A loss that ignores masked entries gets the same result either way.

### Augmentation (5.7), and what it may never do

With `augment=True`, each sample draws from a `torch.Generator` seeded with `seed + index`, so sample
*i* is the same sample in every epoch, in every DataLoader worker, and after a restart. `seed=None`
draws one base seed and records it in `LudoDataset.seed`, so a run is reproducible after the fact.
From `config/training.yaml` `augmentation`:

- **colour jitter** (brightness, contrast, saturation, hue in a random order) on all three RGB images;
- **crop-and-resize** to `random_crop_ratio` on `random_crop_cameras` — `oblique` and `palm` only;
- **gaussian blur** of a random sigma in `goal_heatmap_blur_px` on the two goal channels only.

The `top` RGB is never geometrically altered. The goal heatmaps live in that frame, so a crop, a flip
or a rotation there would leave the policy conditioned on a cell that is no longer under the gaussian.
`geometric_on_top: false` and the absence of `top` from `random_crop_cameras` are **enforced** at
construction, not assumed: either one violated raises `ConfigError`. `tests/test_dataset.py` pins it
from the other side, by comparing an augmented sample against an unaugmented one with the jitter at
zero strength and asserting the `top` RGB is bit-identical while `oblique`, `palm` and the goal
channels have all changed.

### The cell-pair split

```python
train_pairs, held_out = split_cell_pairs(sessions, 0.2, seed=0)   # two sorted lists of (src, dst)
```

The split is over cell pairs, never over frames or episodes (`dataset.split_by: cell_pair`): a policy
evaluated on a pair it trained on has been asked nothing. `eval/protocol.py` holds out the second
list. `seed` makes it reproducible and both lists come back sorted, so what was held out is a
diffable record. A positive fraction always holds out at least one pair, so an eval set is never
silently empty.

Only episodes with **both** cells contribute a pair: a ROLL addresses no cell pair and a RECOVER may
address one cell, and neither can be held out by pair. `LudoDataset(split=pairs)` keeps exactly the
episodes whose `(src_cell, dst_cell)` is listed, so a multi-task training set that wants the ROLL
episodes as well names their pair explicitly: `split=[*train_pairs, (None, None)]`.

### Throughput

`tests/test_dataset.py::test_iteration_benchmark` draws 1000 samples through a `DataLoader`
(batch 8, augmentation on) from the mock session and prints samples/s:

```bash
.venv/bin/python -m pytest tests/test_dataset.py::test_iteration_benchmark -q -s
```

On this laptop, at the tests' 64x48 frames and with one torch thread: **81 samples/s** with
`num_workers=0` and **145 samples/s** with `num_workers=2` (2026-09-11). Both legs run single-threaded
because a DataLoader worker sets `torch.set_num_threads(1)` itself, and on tensors this small the
default thread pool costs about three times what it saves — with the pool on, the `num_workers=0` leg
measures 30 samples/s, which is thread contention, not the loader. Real 640x480 frames will be
slower; that measurement belongs to the first real session, not to the mock.
