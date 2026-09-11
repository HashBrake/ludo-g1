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
data = LudoDataset(sessions, n_obs_steps=2, augment=True, seed=0, split=train_pairs)
sample = data[0]
```

One `LudoDataset` wraps one or more sessions written by `teleop/recorder.py` — a LeRobot v3.0 dataset
(D-011, D-015) plus the `episodes_meta.jsonl` sidecar — and yields the observation of CLAUDE.md 5.3
with a chunk of the actions that followed it:

| key | shape | dtype | meaning |
|---|---|---|---|
| `top` | `(S, 5, h, w)` | float32 | Brio RGB in [0, 1], then the two goal heatmap channels |
| `oblique` | `(S, 3, h, w)` | float32 | Orbbec Ego RGB in [0, 1] |
| `palm` | `(S, 3, h, w)` | float32 | DexH15 palm RGB in [0, 1] |
| `state` | `(S, 9)` | float32 | 7 arm joints, waist yaw, pinch (`config/robot.yaml` `action_order`) |
| `obs_mask` | `(S,)` | float32 | 1 where the frame was recorded, 0 where the history was padded |
| `task_id` | `(3,)` | float32 | one-hot over `config/training.yaml` `observation.task_ids` |
| `action` | `(chunk, 9)` | float32 | absolute joint targets at `rates.dataset_hz` |
| `action_mask` | `(chunk,)` | float32 | 1 where the action was recorded, 0 where the tail was padded |

`chunk` defaults to `diffusion.chunk` (16); the ACT baseline passes `chunk=32` (`act.chunk`). `S` is
`n_obs_steps` and **the step dimension exists only above 1** (see below). The 15 raw DexH15 joints
and the 17 glove channels are in the dataset but are not in a sample: CLAUDE.md 5.3 records them and
does not feed them to the policy.

### Observation history (T-034)

`n_obs_steps` is how many observation frames a sample ends with — `diffusion.obs_history` is 2,
`act.obs_history` is 1 — and it is an **explicit argument per policy**, not a default: `policy/train.py`
passes `spec.n_obs_steps`, which is the config value for the Diffusion Policy and the constant 1 for
ACT, whose `ACTConfig` refuses anything else. There is no value that is right for both models, so
there is no default that is right either; at 1 a sample is rank 3 / rank 1 as it was before T-034.

The frames come from lerobot `delta_timestamps` on the four observation keys (three cameras and the
state) at `[-(S-1)/fps … 0]`, so they are **oldest first and end at the current frame**. At the start
of an episode the deltas reach before its first frame; lerobot clamps them to it and reports
`observation.state_is_pad`, which becomes `obs_mask`. A history therefore repeats the first frame
rather than borrowing the previous episode's last one.

That padding rule is the same one `DiffusionAdapter` applies to its live queue on the first call of a
primitive, and the order is the same order it stacks in.
`tests/test_diffusion.py::test_the_adapter_queue_and_the_dataset_history_agree` feeds two consecutive
`Observation`s built from a recorded episode to the adapter and asserts its batch equals the
dataset's own two-frame sample at that frame, tensor for tensor, including the padded first call.

The goal channels are **rendered once per episode and repeated** across the history (they are a
property of the command, not of the frame), and `task_id` carries no step dimension at all for the
same reason; `policy/_shared.py`'s `with_steps` repeats it inside the model.

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

One draw per **sample**, not per frame: every frame of a history is jittered and cropped identically,
because the frames are 33 ms apart through one fixed camera and a window that moved between them
would be a camera that moved between them.

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

On this laptop, at the tests' 64x48 frames and with one torch thread (2026-09-12):

| `n_obs_steps` | `num_workers=0` | `num_workers=2` |
|---|---|---|
| 1 (ACT) | 80 samples/s | 148 samples/s |
| 2 (Diffusion Policy) | 45 samples/s | 83 samples/s |

The history costs what it decodes: a two-step sample opens six PNGs where a one-step sample opens
three, and the rate roughly halves (T-034; the same test measured 90 / 143 samples/s at one step
before the change, so the one-step legs are unchanged within run-to-run noise). Every leg runs
single-threaded because a DataLoader worker sets `torch.set_num_threads(1)` itself, and on tensors
this small the default thread pool costs about three times what it saves — with the pool on, the
`num_workers=0` leg measures 30 samples/s, which is thread contention, not the loader. Real 640x480
frames will be slower, and a real training run will want workers; that measurement belongs to the
first real session, not to the mock.

## `policy/_shared.py`

What both models of 5.7 feed on, normalise with and are measured by, in one module so that neither is
a client of the other (T-030 review; before T-034 `policy/act.py` imported two of these through their
private names from `policy/diffusion.py`):

- **feeding** — `image_tensor` (an HWC frame to `(3, h, w)` float32 in [0, 1]), `observation_frame`
  (one `runtime.policy_api.Observation` to the frame both wrappers take, goal channels concatenated
  onto `top` exactly as the dataset does) and `with_steps` (the observation-step dimension: present in
  the batch for the Diffusion Policy, repeated for what is constant over an episode);
- **normalisation** — `Normalizer` (the statistics as buffers in the model's own `state_dict`, because
  lerobot 0.4.4 keeps them in a processor pipeline built around a hub checkpoint) and `dataset_stats`;
- **measurement** — `benchmark` and `synthetic_observation`, so that D-019 compares one measurement
  with itself.

`dataset_stats` counts **each camera's own pixels** since T-034; before that every camera was divided
by `top`'s pixel count, which scaled the `palm` mean and std by (640·480)/(320·240) = 4 and would have
mis-normalised the palm camera in the first real training run. It also counts every frame of a
history, so a dataset with `n_obs_steps` 2 and one with 1 give the same statistics.

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

### The observation history is real on both sides (T-034)

`diffusion.obs_history` is 2, and since T-034 both halves carry two real frames: `LudoDataset`
queries the last two camera frames and states per sample and `DiffusionAdapter` queues the last two
observations, in the same order and with the same start-of-episode padding (`policy/dataset.py`
above, and the test that pins them to each other). Until then the dataset yielded one frame and the
wrapper repeated it, which trained the model on a still image and ran it on motion — the T-029
finding this closes. What `policy/_shared.py`'s `with_steps` still repeats is what is constant over an
episode (the task one-hot) and a single frame handed in by a caller with no history.

Still open from T-029: EMA (`diffusion.ema_decay`) and the warmup scheduler of `diffusion.scheduler`
are not applied by `policy/train.py` (T-035).

### `DiffusionAdapter` (the `runtime.policy_api.Policy` side)

`reset(command)` drops the observation queue and restarts the noise sequence; `act(observation)`
converts the `Observation` dataclass (uint8 HWC frames, the `(2, h, w)` goal channels, the 9-D state,
the task one-hot) into the batch, runs one DDIM sample and returns an `ActionChunk` of 16 at
`rates.action_hz`; `done()` is **always False** — this model has no termination head, so the
controller's 20 s `runtime.primitive_timeout_s` ends every primitive and the engine verifies the
state change (5.5). `seed=` pins the initial noise, which is what makes an exported bundle
reproducible.

## `policy/act.py`

```python
from policy.act import ACTAdapter, ACTSpec, GoalACTPolicy

model = GoalACTPolicy(ACTSpec.from_config())           # training: policy/train.py --policy act
policy = ACTAdapter("data/checkpoints/<run>/bundle")   # inference, runtime/controller.py
```

The baseline of CLAUDE.md 5.7: lerobot 0.4.4's `ACTPolicy` (ResNet-18, chunk 32, VAE objective,
temporal ensembling) wrapped — never patched — around **the same observation the Diffusion Policy
takes**. The adaptation is not merely similar, it is the same code: both wrappers import the
normalisation buffers (`Normalizer`), the frame conversion (`image_tensor`, `observation_frame`), the
camera keys and the latency benchmark from **`policy/_shared.py`** — since T-034 a module of their
own, so that neither model is a client of the other. 5.7 requires the baseline to be
trained on every dataset the primary is trained on, and a difference in inputs, normalisation or
measurement would make the comparison say something other than "these two architectures differ".

### What ACT does differently from the Diffusion Policy, and what the wrapper does

| | Diffusion Policy | ACT |
|---|---|---|
| camera encoders | one ResNet-18 per camera (`use_separate_rgb_encoder_per_camera`) | **one shared** ResNet-18; `ACT.__init__` builds a single `self.backbone` and there is no per-camera option — so `act.encoder_per_camera` was removed from `config/training.yaml`, it never existed |
| 5-channel `top` | refused by `DiffusionConfig.validate_features` | not refused by the config, but the shared 3-channel backbone raises *"expected input… to have 3 channels, but got 5"* — the same 1x1 goal projection is still the route |
| observation history | `n_obs_steps` 2 on both sides since T-034: the dataset queries two frames and the adapter queues two | `ACTConfig` **refuses** any `n_obs_steps` but 1, so `ACTSpec.n_obs_steps` is the constant 1, `LudoDataset` is built with it, and an `act.obs_history` other than 1 is refused in `ACTSpec.from_config` |
| chunk alignment | lerobot slices from `n_obs_steps - 1`; the wrapper samples the trajectory directly | `action_delta_indices` is `range(chunk_size)` = [0 … 31], already the dataset's alignment |
| normalisation | statistics as buffers, STATE/ACTION min/max to [-1, 1] | the same buffers and the same min/max (upstream ACT maps them to mean/std, but lerobot's processor pipeline does not run for either model, and two baselines that normalise differently do not compare) |
| pretrained backbone | `None` upstream | upstream default is `ResNet18_Weights.IMAGENET1K_V1`, which downloads at construction; `act.pretrained_backbone_weights: null` matches the Diffusion Policy so the comparison is not also a comparison of initialisations |
| inference determinism | DDIM noise; `seed=` pins it | deterministic: the VAE encoder runs in training only, so the latent is zeros at inference |

### Temporal ensembling lives in `ACTAdapter`, and why

lerobot gates ensembling on `temporal_ensemble_coeff`, and setting it in 0.4.4 **forces
`n_action_steps = 1`** (`configuration_act.py:137`: the policy must be queried every step, because
`ACTPolicy.select_action` consumes exactly one ensembled action per call). Our contract is the other
shape — `runtime/controller.py` queries at `rates.policy_hz` (10 Hz) and plays a chunk at
`rates.action_hz` (30 Hz) — so the wrapped `ACTConfig` keeps `temporal_ensemble_coeff=None`
(`select_action` is never called) and `TemporalEnsemble` does the averaging over the chunk.

Query *k* predicts 32 actions for absolute action-steps `k·s + j`, where `s = round(action_hz /
policy_hz)` = 3 is how far the controller advances between queries. An action-step `T` is therefore
predicted by every query with `k·s ≤ T < k·s + 32`; ordering those predictions oldest first,

```
ensembled(T) = Σᵢ wᵢ·aᵢ / Σᵢ wᵢ,     wᵢ = exp(-coeff · i)
```

which is lerobot's own rule (`ACTTemporalEnsembler`, coefficient 0.01, older actions weighted more),
written offline because lerobot's online recursion assumes the query stride is 1.
`tests/test_act.py::test_temporal_ensemble_matches_lerobots_own` runs both at stride 1 and pins them
to each other: 2.9e-08 against lerobot as it ships — that is its float32 weight table against this
one's float64 — and 2.2e-16 when the same weights are computed in float64.

`act()` returns `act.expose` (16) ensembled actions at `rates.action_hz`, so ACT's 32 is internal and
the controller contract of 5.2 is unchanged. `s` is the *nominal* stride: neither adapter is told how
many actions the controller actually played, so a late query makes the ensemble as stale as it makes
a receding horizon.

## `policy/train.py`

```bash
.venv/bin/python -m policy.train --sessions data/raw/<session> --steps 200000        # Greennode
.venv/bin/python -m policy.train --sessions data/raw/<session> --policy act          # the baseline
.venv/bin/python -m policy.train --sessions data/raw/mock_smoke --smoke              # 30 steps, CPU
```

A plain torch loop (Adam, `<policy>.learning_rate`, `<policy>.weight_decay`) over `LudoDataset`
with the policy's own loss — no lerobot trainer, no hub. Each run writes
`data/checkpoints/<run>/`:

- `run.json` — args, git commit, **all six config hashes**, the **dataset manifest hash** (sha256 over
  each session's `meta/info.json` and `episodes_meta.jsonl`, in session-name order), frame and episode
  counts, parameter count, first/last loss, wall time;
- `loss.csv` — `step,loss,elapsed_s` for every step;
- `checkpoint.pt` — `{"policy", "spec", "state_dict", "run"}`.

Two runs are comparable exactly when their config hashes and manifest hash agree (R5, 5.6). A run
also writes `data/logs/train_<run>.heartbeat` (section 7).

`--policy act` is the whole difference between the two models of 5.7: the same sessions, the same
`LudoDataset` (at the policy's own `chunk` and `obs_history` — 32 and 1 for ACT, 16 and 2 for the
Diffusion Policy, both recorded in `run.json`), the same split, the same
statistics, the same loop, the same run directory, and `run.json`/`checkpoint.pt` record which model
it was. Every default (steps, batch size, learning rate, weight decay, seed, encoder input size,
statistics samples) comes from the chosen policy's block in `config/training.yaml`, so the flag
cannot silently carry a diffusion hyperparameter into an ACT run. `train()` itself dispatches on the
*type of the spec* it is given (`policy_kind`), so a caller that builds a spec never names the policy
twice.

The per-step training loss is a noisy estimate: `compute_loss` draws a fresh diffusion timestep and
noise every step, so a single step's number says little. Compare `loss_mean_last_10`, or the
fixed-probe loss `tests/test_diffusion.py` uses (same batch, same seeded draw, two models).

## `policy/export.py`

```bash
.venv/bin/python -m policy.export --checkpoint data/checkpoints/<run>
.venv/bin/python -m eval.run_eval --policy bundle data/checkpoints/<run>/bundle --backend mock
```

A checkpoint becomes an inference bundle: `bundle.json` (format tag, the policy name, the spec, the
weights sha256, the source checkpoint and its sha256, the training run's hashes, what tracing did)
and `weights.pt` (the `state_dict`, statistics included). Both models of 5.7 export the same way —
the format tag is `ludo-g1/diffusion-bundle/1` or `ludo-g1/act-bundle/1` — and `open_bundle(path)` is
the one reader that turns either back into a `runtime.policy_api.Policy`, so
`eval/run_eval.py --policy bundle PATH` takes either without being told which and records the policy,
the weights hash and the training run in every result.

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
