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

## `policy/diffusion.py`

```python
from policy.diffusion import DiffusionAdapter, GoalDiffusionPolicy, PolicySpec

model = GoalDiffusionPolicy(PolicySpec.from_config())        # training
policy = DiffusionAdapter("data/checkpoints/<run>/bundle")   # inference, runtime/controller.py
```

The primary policy of CLAUDE.md 5.7: lerobot 0.4.4's `DiffusionPolicy` (ResNet-18 encoder per camera,
chunk 16, DDIM 10 at inference, receding horizon of 8) wrapped — never patched — so that it accepts
the observation of 5.3.

### What lerobot cannot be told, and what the wrapper does instead

Two things 5.3 asks for have no place in the upstream config, and both refusals were reproduced
before the wrapper was written (`tests/test_diffusion.py::test_lerobot_refuses_a_five_channel_top_beside_three_channel_cameras`):

| want | what lerobot 0.4.4 does | what `GoalDiffusionPolicy` does |
|---|---|---|
| 5-channel `top` (RGB + 2 goal channels) | `DiffusionConfig.validate_features` (`lerobot/policies/diffusion/configuration_diffusion.py:239`) raises *"`observation.images.oblique` does not match `observation.images.top`, but we expect all image shapes to match"* | a learned **1x1 convolution** projects 5 → 3 channels before the encoder |
| five channels on *every* camera instead | the stock torchvision backbone (`modeling_diffusion.py:475`) raises *"weight of size [64, 3, 7, 7] … but got 5 channels"* | — |
| task one-hot as a separate input | there is no such input | it is **concatenated onto the state**: lerobot sees a 12-D `observation.state` (9 + 3) |
| three cameras at two different resolutions | same "all image shapes must match" rule | every camera is resized to `diffusion.encoder_image_hw` (240x320) first |
| normalisation inside the policy | moved out into processor pipelines (`processor_diffusion.py:36`) built around a `LeRobotDataset` and a hub checkpoint | the same statistics live as **buffers in this model's `state_dict`** (VISUAL mean/std, STATE and ACTION min/max to [-1, 1]), so a bundle is self-contained |

The projection starts as the identity on the RGB channels with **zero weight on the goal channels**,
so at step 0 the encoder sees exactly the image it would see without a goal and training decides how
much goal to let in. Resizing to one encoder shape has a second effect worth knowing: the model does
not depend on the camera resolutions at all, so a model trained on the 64x48 mock frames runs on the
640x480 real ones unchanged.

### The chunk starts at the current frame

lerobot's `generate_actions` slices the predicted trajectory from `n_obs_steps - 1`, because its
sampler aligns actions with `action_delta_indices` = [-1 … 14]. `policy/dataset.py` aligns them at
[0 … 15] — the chunk starts at the frame that was observed. So `GoalDiffusionPolicy.predict` samples
the trajectory directly (`DiffusionModel.conditional_sample`) and returns all 16 from index 0, and
`runtime/controller.py` plays `diffusion.execute` (8) of them before asking again. Mixing the two
conventions would put a one-step-stale action first; training and inference here both use the
dataset's.

### Known gap: the observation history is repeated during training

`diffusion.obs_history` is 2 and `policy/dataset.py` yields one frame per sample, so **training
repeats the current frame twice** while `DiffusionAdapter` keeps a real queue of the last two
observations. The model therefore never sees motion in its conditioning during training, and sees it
at inference. This must be closed before any real training run — `LudoDataset` needs observation
`delta_timestamps` the way it already has them for actions — and it is logged as a T-029 finding in
`agents/BUILD_LOG.md`. EMA (`diffusion.ema_decay`) and the warmup scheduler of `diffusion.scheduler`
are likewise not applied by `policy/train.py` yet.

### `DiffusionAdapter` (the `runtime.policy_api.Policy` side)

`reset(command)` drops the observation queue and restarts the noise sequence; `act(observation)`
converts the `Observation` dataclass (uint8 HWC frames, the `(2, h, w)` goal channels, the 9-D state,
the task one-hot) into the batch, runs one DDIM sample and returns an `ActionChunk` of 16 at
`rates.action_hz`; `done()` is **always False** — this model has no termination head, so the
controller's 20 s `runtime.primitive_timeout_s` ends every primitive and the engine verifies the
state change (5.5). `seed=` pins the initial noise, which is what makes an exported bundle
reproducible.

## `policy/train.py`

```bash
.venv/bin/python -m policy.train --sessions data/raw/<session> --steps 200000        # Greennode
.venv/bin/python -m policy.train --sessions data/raw/mock_smoke --smoke              # 30 steps, CPU
```

A plain torch loop (Adam, `diffusion.learning_rate`, `diffusion.weight_decay`) over `LudoDataset`
with the policy's own loss — no lerobot trainer, no hub. Each run writes
`data/checkpoints/<run>/`:

- `run.json` — args, git commit, **all six config hashes**, the **dataset manifest hash** (sha256 over
  each session's `meta/info.json` and `episodes_meta.jsonl`, in session-name order), frame and episode
  counts, parameter count, first/last loss, wall time;
- `loss.csv` — `step,loss,elapsed_s` for every step;
- `checkpoint.pt` — `{"spec", "state_dict", "run"}`.

Two runs are comparable exactly when their config hashes and manifest hash agree (R5, 5.6). A run
also writes `data/logs/train_<run>.heartbeat` (section 7).

The per-step training loss is a noisy estimate: `compute_loss` draws a fresh diffusion timestep and
noise every step, so a single step's number says little. Compare `loss_mean_last_10`, or the
fixed-probe loss `tests/test_diffusion.py` uses (same batch, same seeded draw, two models).

## `policy/export.py`

```bash
.venv/bin/python -m policy.export --checkpoint data/checkpoints/<run>
.venv/bin/python -m eval.run_eval --policy bundle data/checkpoints/<run>/bundle --backend mock
```

A checkpoint becomes an inference bundle: `bundle.json` (format tag, the `PolicySpec`, the weights
sha256, the source checkpoint and its sha256, the training run's hashes, what tracing did) and
`weights.pt` (the `state_dict`, statistics included). `DiffusionAdapter` loads exactly that, and
`eval/run_eval.py --policy bundle PATH` records the weights hash and the training run in every result.

TorchScript is attempted and verified: the trace takes the diffusion noise as an **input** (otherwise
the graph contains an `aten::randn` and `check_trace` compares two different random draws), and the
traced graph is then compared against the eager model — on the smoke run the difference was exactly
0.0 and `model.ts` was written. It is still **not what the adapter runs**: a traced reverse-diffusion
loop bakes in the batch size, the image size and the DDIM step count, and the file is as large as the
weights again (1.17 GB for the 293 M-parameter model). It is a starting point for the Orin NX / ONNX
leg of 5.8, and the honest export is the `state_dict` plus the spec. Revisit by exporting the U-Net
alone and keeping the scheduler loop in Python.

## Inference latency on this laptop (CPU, 2026-09-11)

```bash
.venv/bin/python -m policy.diffusion --bundle data/checkpoints/<run>/bundle --trials 20
.venv/bin/python -m policy.diffusion --bundle data/checkpoints/<run>/bundle --trials 20 --inference-steps 5
```

At the configured frame sizes (`top`/`oblique` 640x480, `palm` 320x240 → a 240x320 encoder input),
the full 293 M-parameter model, torch 2.9.1+**cpu**, 14 threads:

| DDIM steps | median `act()` | mean | budget (10 Hz) |
|---|---|---|---|
| 10 (`diffusion.inference_steps`) | **804 ms** | 1.1 s (spiky: p95 3.7 s) | 100 ms — **8x over** |
| 5 (the first rung of `compute.inference_fallback_order`) | **498 ms** | 502 ms | 100 ms — **5x over** |

Frame preparation (resize, normalise, project) is 8–16 ms of that; the rest is the U-Net. The
fallback ladder of 5.8 does not close an 8x gap on CPU: DDIM 5 halves the cost and is still 5x over,
so 10 Hz inference needs a GPU (a CUDA torch build on this laptop, or the Orin NX), a smaller model,
or both. This is a measurement on an untrained smoke checkpoint, which costs exactly what a trained
one of the same shape will.
