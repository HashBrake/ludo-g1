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
| `done` | `()` | float32 | 1 when the frame is within `done.window_s` of the end of its episode (T-040) |

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

### The done label (T-040)

`done` is 1 when the frame's distance to the last frame of its episode is at most
`config/training.yaml` `done.window_s` — 1.0 s, so the last 31 frames at 30 Hz — and 0 otherwise. It
is read off the episode span, not off the operator's success flag: an episode that failed still
*ended*, and a policy that can see a primitive ending is right either way. One shared `done` block
feeds the label here and the head in both wrappers, because the label is a property of the recorded
data and two models trained on two different windows would not be the comparison 5.7 asks for.

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
  with itself, and `set_torch_threads`, the one place torch's intra-op thread pool is sized
  (`compute.torch_threads`, D-020). It is applied once per process — by `policy/train.py` and
  `eval/run_eval.py` at the start of `main`, and by both adapters' constructors, whichever comes
  first — and every later call returns what is in force without resizing a pool that is already
  running (a DataLoader worker sets its own count of 1, and must keep it). The table it is set from
  is at the end of this page.
- **termination** (T-040) — `DoneHead` (the auxiliary episode-end head of 5.5), `FeatureTap` (how the
  pooled features get out of a lerobot module we wrap and never patch), `DoneDetector` (threshold and
  hold, behind both adapters' `done()`) and `done_settings` (the one shared `done` config block). See
  the section below.

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

### The termination head (CLAUDE.md 5.5, T-040)

CLAUDE.md 5.5 ends a primitive on "the policy's own termination signal or a 20 s timeout". Until
T-040 only the timeout existed and both adapters' `done()` returned a hard-coded False. Both models
now carry the same auxiliary head, and it is the same head deliberately: the signal is a property of
the recorded data, so the primary and the baseline must terminate on it identically or the eval
comparison of 5.7 is confounded.

| | Diffusion Policy | ACT |
|---|---|---|
| features the head reads | the U-Net's `global_cond` — state + one camera feature block per camera, flattened over the observation steps | the mean over the transformer encoder's output tokens (`dim_model` wide) |
| how they are obtained in training | a `FeatureTap` forward pre-hook on the U-Net catches `global_cond` on its way in | a `FeatureTap` forward hook on `model.encoder` catches its output |
| how they are obtained at inference | `predict` already computes `global_cond` before sampling | the same hook, during `predict_action_chunk` |
| cost | one matrix multiply per call | one matrix multiply per call |

At the configured scale the head is 52 481 parameters on a 408-D feature for either diffusion block
(0.018% of the 293.1 M primary, 0.172% of the 30.5 M `diffusion_small`) and 65 793 on a 512-D feature
for the 51.6 M ACT baseline (0.127%).

The head is a two-layer MLP (`done.hidden_dim` = 128) ending in a **zero-initialised** output layer,
the same convention as the goal projection: an untrained head returns a logit of exactly 0, so its
probability is 0.5, so the detector — which needs *strictly above* `done.threshold` — never fires for
a model that has not learned the signal.

The loss is `BCEWithLogits` against the dataset's `done` label, added inside each wrapper's
`forward` with weight `done.loss_weight` (0.1). That is where it has to be: `policy/train.py` is one
loop for both models and calls `model(batch)` for one number, so the auxiliary objective belongs to
the model and not to the trainer. Each wrapper leaves the two parts in `last_losses`
(`{"action_loss", "done_loss"}`), which is what the tests measure and what a `done_loss` column in
`loss.csv` would read (a follow-up in `policy/train.py`, which T-040 did not touch). The tap hands
back a tensor that is still on the autograd graph, so the done gradient reaches the shared encoders
— `tests/test_diffusion.py::test_the_done_loss_trains_the_shared_encoders` pins exactly that.

At inference `DoneDetector` turns the probability into a stop: `done()` is True once `done.hold_steps`
(2) consecutive `act()` calls have exceeded `done.threshold` (0.5), which at `rates.policy_hz` is
200 ms of agreement, so a single confident frame does not end a primitive. `reset(command)` clears
the streak. **The probability is computed inside `act()`**, and `runtime/controller.py` asks
`done(obs)` *before* `act(obs)`, so the signal the loop reads is one policy period (100 ms) old.
That is the price of not running the vision encoders twice per tick, and it is stated rather than
hidden.

Measured on the 30-step mock smoke run (a 0.3 s window and weight 1.0, because the mock episodes are
1.0 s and 0.5 s long and the configured 1.0 s window would label every frame of both as done):

| | done loss over the session | trained p(done), not-done frame | done frame |
|---|---|---|---|
| Diffusion Policy | 0.6931 → 0.6664 (−3.9%) | 0.446 | 0.478 |
| ACT | 0.6931 → 0.6900 (−0.5%) | 0.484 | 0.484 |

30 steps on two mock episodes is enough to show the head is wired and training, and nothing more: the
base rate of that session is 0.444, and both heads are still mostly learning it. Whether the head
*separates* the classes is a Phase 3 question on real data (R5).

### `DiffusionAdapter` (the `runtime.policy_api.Policy` side)

`reset(command)` drops the observation queue and restarts the noise sequence; `act(observation)`
converts the `Observation` dataclass (uint8 HWC frames, the `(2, h, w)` goal channels, the 9-D state,
the task one-hot) into the batch, runs one DDIM sample and returns an `ActionChunk` of 16 at
`rates.action_hz`; `done()` reports the termination
head's streak (the section above); the 20 s `runtime.primitive_timeout_s` is the other end of 5.5 and
the engine verifies the state change either way. `seed=` pins the initial noise, which is what makes
an exported bundle reproducible.

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
| the done head of 5.5 | on the U-Net's `global_cond` | on the mean of the transformer encoder's output tokens — the same head, the same label, the same thresholds (T-040) |

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

The checkpoint I/O it uses — `atomic_save`, `write_step_checkpoint`, `prune_step_checkpoints`, the
disk guard and the `EMA` — lives in `policy/train_io.py` since T-042; `train.py` imports every name
back, so `policy.train.EMA` and the rest still resolve.

```bash
.venv/bin/python -m policy.train --sessions data/raw/<session> --steps 200000        # Greennode
.venv/bin/python -m policy.train --sessions data/raw/<session> --policy act          # the baseline
.venv/bin/python -m policy.train --sessions data/raw/mock_smoke --smoke              # 30 steps, CPU
.venv/bin/python -m policy.train --sessions ... --checkpoint-every 1000 --keep-last 2  # T-036
.venv/bin/python -m policy.train --sessions ... --resume data/checkpoints/<run>/checkpoint.pt
```

A plain torch loop (Adam, `<policy>.learning_rate`, `<policy>.weight_decay`) over `LudoDataset`
with the policy's own loss — no lerobot trainer, no hub. Each run writes
`data/checkpoints/<run>/`:

- `run.json` — args, git commit, **all six config hashes**, the **dataset manifest hash** (sha256 over
  each session's `meta/info.json` and `episodes_meta.jsonl`, in session-name order), frame and episode
  counts, the held-out cell pairs, parameter count, first/last loss, wall time;
- `loss.csv` — `step,loss,val_loss,lr,elapsed_s` for every step (`val_loss` is empty on a step that
  ran no validation pass);
- `checkpoint.pt` — `{"policy", "spec", "state_dict", "ema_state_dict", "ema_step", "optimizer",
  "step", "rng", "losses", "run"}`;
- `checkpoint_step<N>.pt` — the last `--keep-last` periodic copies of the same thing (T-036).

### EMA, warmup + cosine, validation, resume (T-035)

- **EMA.** An exponential moving average of the **parameters** at `<policy>.ema_decay` is kept beside
  the live weights and updated after every optimiser step; `policy/export.py` puts it in the bundle
  by default. The decay ramps as `min(decay, (1 + n) / (10 + n))`: at 0.9999 the plain recursion has
  a 10 000-step time constant, so without the ramp the "average" of any short run would still be the
  initialisation. Buffers — the normalisation statistics and the encoders' BatchNorm running
  statistics — are the live ones: they are already running averages of the data, and averaging them
  twice makes them lag the weights they belong to.
- **Warmup then cosine.** The learning rate is `learning_rate ×` a multiplier that ramps linearly over
  `warmup_steps` (`1/warmup` at the first step, 1.0 at step `warmup - 1`) and then decays as a cosine
  to `lr_min_ratio × learning_rate` at the last step; it is 1.0 at `warmup` from either side. The
  warmup is clamped to a tenth of the run (`warmup_for`), so the configured 500 steps do not swallow
  a 30-step smoke run. The multiplier that was applied is the `lr` column of `loss.csv`.
- **A validation split by cell pair.** `--val-fraction` (default `dataset.val_fraction`, 0 for
  `--smoke`) holds that fraction of the sessions' cell pairs out through `split_cell_pairs` — never a
  fraction of the frames, which would put the same pair on both sides. ROLL episodes address no pair,
  so they stay in training through their explicit `(None, None)`. Every `val_every` steps (and always
  at the last step) the loss over `val_batches` fixed validation batches is written to the `val_loss`
  column. The pass runs at a fixed seed (`VAL_SEED`) and restores the RNG afterwards, so two
  validation losses differ by the model and not by the diffusion draw, and the training stream is
  untouched. A split that would leave no training frames is refused, not silently trained on.
- **Slices and resume.** `--stop-after N` runs N steps of the run, checkpoints and exits;
  `--resume <checkpoint.pt>` continues it, restoring weights, optimiser, EMA, step counter, loss
  history and RNG state, with the batch stream positioned by step number: `_StepSampler` draws epoch
  *e*'s permutation from `default_rng([seed, e])`, so the resume point is arithmetic rather than a
  replay, and the DataLoader gets its own `generator` so that creating a second iterator does not
  draw from the global RNG (it does by default, and that single draw moved the resumed losses by
  ~0.1 before it was found). `--steps` stays the horizon the schedule is computed against, so a run
  taken in slices is the same run: 10 + 10 of a 20-step run **is** the 20-step run —
  `tests/test_train.py` pins the two loss sequences (largest difference **0.0**) and both sets of
  weights (live and EMA) against each other.

A checkpoint is **four times the parameters** on disk since T-035: the weights, their EMA, and Adam's
two moments. That is what an exact resume costs — 4.7 GB at the configured 293 M parameters, 490 MB
for `diffusion_small` — and it is why nothing keeps two of them on this laptop (Q-002).

### Surviving a crash: periodic checkpoints, pruning, the disk guard (T-036)

A real run is hours of a GPU we rent (5.8), so the run is checkpointed as it goes and not only at the
end. Three flags, three `compute` keys in `config/training.yaml`:

| flag | default | what it does |
|---|---|---|
| `--checkpoint-every N` | `compute.checkpoint_every` = 1000 | write the checkpoint every N steps; 0 writes only the one at the end of the invocation |
| `--keep-last K` | `compute.keep_last` = 2 | step checkpoints kept beside `checkpoint.pt`, newest first |
| `--no-disk-guard` | guard on, `compute.disk_guard_factor` = 2 | train even when the free space is below factor × the estimated checkpoint size |

- **Atomic.** Every checkpoint — periodic or final — is `torch.save`d to `checkpoint.pt.tmp` and then
  `os.replace`d over `checkpoint.pt`. The rename is atomic on one filesystem, so a crash (or a full
  disk) *during* a write leaves the previous checkpoint whole instead of a truncated file that
  `--resume` would choke on; the `.tmp` suffix is not `.pt`, so a half-written file can never be
  mistaken for a checkpoint. `tests/test_train.py` makes `torch.save` die after writing the temp file
  and asserts the old `checkpoint.pt` still loads.
- **Periodic, and named.** Each periodic write also leaves `checkpoint_step<N>.pt`, a **hard link** to
  the file just written — a 4.7 GB checkpoint is not written twice and two names do not cost two
  inodes. `--resume` takes either name. The step copy's `run` record is the run so far, with
  `complete: false`; only the checkpoint at the end of an invocation has `complete: true`.
- **Pruned.** After each periodic write, all but the `--keep-last K` newest `checkpoint_step<N>.pt`
  are deleted. Nothing else is ever considered: `checkpoint.pt`, `run.json`, `loss.csv` and an
  exported bundle sitting in the same run directory do not match the glob and cannot be pruned.
- **The disk guard.** Before the first step, the free space under the run directory is compared with
  `disk_guard_factor ×` the estimated checkpoint size (`parameters × 4 bytes × 4`, plus 10%, printed
  either way — at the configured scale **5.16 GB** for the 293.0 M diffusion policy, 0.54 GB for
  `diffusion_small`, and 0.91 GB for the 51.6 M ACT baseline). Short of it, the run refuses to start
  with a message naming Q-002, the estimate and the free space, instead of dying an hour in with a
  half-written checkpoint. The factor is 2 because an atomic write holds the new checkpoint and the
  previous one at the same time. `--no-disk-guard` overrides it with a logged warning: this is a
  training-time convenience against a full laptop disk, **not** a safety rule (R3 is `runtime/safety.py`
  and is not overridable). Measured against the real file at the test scale: 16.0 M parameters wrote
  256.8 MB and the estimate is 282.1 MB (1.10×), so the guard over-estimates by design.

The crash path is tested end to end, not described: `tests/test_train.py` runs `python -m policy.train`
in a subprocess with `--fault-at-step 5` (a documented test aid that does nothing unless passed), which
raises after step 5 of an 8-step run; the killed run's directory holds exactly `checkpoint.pt` and
`checkpoint_step4.pt` (step 2's was pruned, and both names are one inode); resuming from it and running
to step 8 reproduces the straight 8-step run's loss sequence with a largest difference of **0.0**.

`--config-block NAME` builds the spec (and takes the defaults) from another block of
`config/training.yaml`: `diffusion_small` is the D-019 fallback configuration — one shared ResNet-18,
120×160 encoder inputs, a quarter-width U-Net, 30.4 M parameters against 293.0 M — and the run
directory, `run.json` and `checkpoint.pt` all record which block produced them.

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
weights sha256, which weights they are, the source checkpoint and its sha256, the training run's
hashes, what tracing did, and the five `done_head` settings of T-040) and `weights.pt` (the
`state_dict`, statistics and the termination head included). **The bundle
carries the training run's EMA weights by default**; `--raw` exports the last optimiser step's
parameters instead, and `bundle.json` records which through `weights_source`, so a success rate
belongs to one of the two and never to "the run". A checkpoint written before T-035 has no
`ema_state_dict` and exports raw, which the manifest says. Both models of 5.7 export the same way —
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

## Inference latency on this laptop (CPU, T-035, 2026-09-12)

```bash
.venv/bin/python -m policy.diffusion --bundle data/checkpoints/<run>/bundle --trials 20
.venv/bin/python -m policy.diffusion --bundle data/checkpoints/<run>/bundle --trials 20 --inference-steps 5
.venv/bin/python -m policy.act       --bundle data/checkpoints/<run>/bundle --trials 20
```

Every number below is the median of 20 `act()` calls at the configured frame sizes (`top`/`oblique`
640×480, `palm` 320×240 → a 240×320 encoder input, 120×160 for `diffusion_small`), on untrained
models of the configured shapes — cost does not depend on the weights — taken in **one process, one
script, on a machine whose 1-minute load average was 2.80 at the start** (1–8 threads) and 0.94
(12–14). torch 2.9.1+**cpu**, 14 logical cores. The budget of 5.2 is 100 ms (10 Hz).

| torch threads | ACT (51.6 M) | Diffusion DDIM 10 (293 M) | Diffusion DDIM 5 | small DDIM 10 (30.4 M) | small DDIM 5 |
|---|---|---|---|---|---|
| 1 | 502 ms | 1746 ms | 1188 ms | 264 ms | 215 ms |
| 2 | 265 ms | 917 ms | 620 ms | 161 ms | 123 ms |
| 4 | 149 ms | 541 ms | 361 ms | 101 ms | 75 ms |
| **8** (`compute.torch_threads`) | **91 ms** | **454 ms** | **281 ms** | **71 ms** | **53 ms** |
| 12 | 91 ms | 397 ms | 247 ms | 77 ms | 53 ms |
| 14 (torch's own guess) | 250 ms | 1057 ms | 507 ms | 128 ms | 91 ms |

What it says:

- **The thread count is worth more than the fallback ladder.** Between torch's guess of 14 and 8,
  ACT goes 250 → 91 ms and the full diffusion policy 1057 → 454 ms. 8 is the configured value: every
  model is at or within noise of its minimum there, 12 matches it, and 8 of 14 cores leaves the
  camera, driver and DDS threads of `runtime/controller.py` somewhere to run. (T-030 measured 4
  threads as the best under a second builder's load; on the quiet machine 8 wins.)
- **ACT now fits the budget** — 91 ms against 100 ms, with no margin. The ACT rung of D-019's ladder
  is reachable on this laptop.
- **`diffusion_small` fits with margin**: 71 ms at DDIM 10, 53 ms at DDIM 5, at 30.4 M parameters
  (one shared ResNet-18, 120×160 inputs, a quarter-width U-Net). Whether it can *learn* the task is a
  Phase 3 eval question and nothing here answers it (R5).
- **The full Diffusion Policy does not fit on this CPU**: 454 ms at DDIM 10 is 4.5× over, 281 ms at
  DDIM 5 is 2.8× over. Its rung of the ladder is a GPU — a CUDA build here, or the Orin NX (Q-011).

Frame preparation (resize, normalise, project) is 4–16 ms of every number above; the rest is the
U-Net (diffusion) or the transformer (ACT). For reference, T-029/T-030 measured the full model at
804 ms (DDIM 10) and 498 ms (DDIM 5) and ACT at 154 ms under load with 14 and 4 threads; those runs
were taken while a second builder's suite was running and are superseded by the table above.
