"""Diffusion Policy (CLAUDE.md 5.7) with the goal channels of 5.3, wrapped around lerobot 0.4.4.

```python
from policy.diffusion import DiffusionAdapter, GoalDiffusionPolicy, PolicySpec

model = GoalDiffusionPolicy(PolicySpec.from_config())      # training: policy/train.py
policy = DiffusionAdapter("data/checkpoints/<run>/bundle")  # inference: runtime/controller.py
```

Nothing here moves the robot. :class:`DiffusionAdapter` satisfies :class:`runtime.policy_api.Policy`
and returns an :class:`~runtime.policy_api.ActionChunk`; the only code that sends anything is
``runtime/controller.py``, through ``runtime/safety.py`` (R1, R3).

Why a wrapper and not a patch
-----------------------------
``third_party/`` and the installed lerobot package are never edited (R2, section 7), so the two
things CLAUDE.md 5.3 asks for that lerobot's Diffusion Policy does not offer are added *outside* it:

* **Two goal heatmap channels on `top`.** ``DiffusionConfig.validate_features``
  (``lerobot/policies/diffusion/configuration_diffusion.py:239``) refuses a 5-channel `top` beside
  3-channel `oblique` and `palm` -- "we expect all image shapes to match" -- and declaring *every*
  camera as 5 channels instead dies in the stock torchvision backbone
  (``modeling_diffusion.py:475``: "weight of size [64, 3, 7, 7] ... but got 5 channels"). Both
  refusals were reproduced before this module was written. So :class:`GoalDiffusionPolicy` owns a
  learned 1x1 convolution that projects ``RGB + 2 goal channels`` down to the 3 channels the encoder
  wants, initialised to pass the RGB through unchanged and to give the goal channels zero weight.
* **The task one-hot.** It is concatenated onto the 9-D state, so lerobot sees a 12-D
  ``observation.state`` and the multi-task conditioning of 5.7 rides in the state vector.

Two more consequences of the "all image shapes must match" rule and of where normalisation lives:

* every camera is resized to ``diffusion.encoder_image_hw`` before the encoder, so `palm`
  (320x240) and `top`/`oblique` (640x480) meet at one shape and the model is independent of the
  frame sizes it is fed;
* lerobot 0.4.4 moved normalisation out of the policy into processor pipelines
  (``lerobot/policies/diffusion/processor_diffusion.py:36``), which are built around a `LeRobotDataset`
  and a hub checkpoint. This module instead carries the same statistics as buffers in its own
  ``state_dict`` (VISUAL: mean/std; STATE and ACTION: min/max to [-1, 1], the mapping of
  ``DiffusionConfig.normalization_mapping`` with the formulas of
  ``lerobot/processor/normalize_processor.py:325-359``), so an exported bundle is self-contained.

Chunking (5.2)
--------------
``horizon`` is ``diffusion.chunk`` (16) and the adapter returns all 16, of which
``runtime/controller.py`` plays ``diffusion.execute`` (8) before asking again. lerobot's own
``generate_actions`` slices from ``n_obs_steps - 1`` because its dataset aligns the trajectory with
``action_delta_indices`` = [-1 .. 14]; ``policy/dataset.py`` aligns it at [0 .. 15] (the chunk starts
at the current frame), so this module samples the trajectory directly and takes it from index 0. The
two conventions must not be mixed: training and inference here both use the dataset's.

Observation history: ``diffusion.obs_history`` is 2, and ``policy/dataset.py`` yields one frame per
sample, so **training repeats the current frame** ``n_obs_steps`` times while inference keeps a real
queue of the last two observations. That is a train/inference mismatch; it is recorded in
``agents/BUILD_LOG.md`` (T-029) and in ``docs/policy.md``, and it has to be closed by giving the
dataset observation history before any real training run (Phase 3, T-031).
"""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import torch
from lerobot.configs.types import FeatureType, PolicyFeature
from lerobot.policies.diffusion.configuration_diffusion import DiffusionConfig
from lerobot.policies.diffusion.modeling_diffusion import DiffusionPolicy
from lerobot.utils.constants import ACTION, OBS_IMAGES, OBS_STATE
from torch import Tensor, nn

from engine.interface import Command, Primitive
from policy.dataset import CAMERAS
from runtime import config
from runtime.policy_api import GOAL_CHANNELS, ActionChunk, Observation
from runtime.types import ACTION_DIM

__all__ = [
    "BUNDLE_FILE",
    "IMAGE_KEYS",
    "WEIGHTS_FILE",
    "DiffusionAdapter",
    "GoalDiffusionPolicy",
    "PolicySpec",
    "dataset_stats",
    "main",
]

#: The lerobot feature key of each camera. Anything under ``observation.images.`` is a VISUAL feature
#: to lerobot; the stacked tensor it builds internally is ``observation.images`` (no trailing name).
IMAGE_KEYS: dict[str, str] = {name: f"observation.images.{name}" for name in CAMERAS}
#: Division guard, the value ``NormalizerProcessorStep`` uses.
EPS = 1e-8
#: What an inference bundle holds (see :func:`policy.export.export`).
BUNDLE_FILE = "bundle.json"
WEIGHTS_FILE = "weights.pt"


# --------------------------------------------------------------------------------------------------
# the specification: everything needed to rebuild the model bit for bit
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicySpec:
    """The architecture, from ``config/training.yaml`` unless a caller overrides it.

    An exported bundle stores this verbatim, so loading a bundle never consults the config file: a
    checkpoint trained under one config cannot be silently rebuilt under another.
    """

    image_hw: tuple[int, int]
    state_dim: int = ACTION_DIM
    task_dim: int = 3
    action_dim: int = ACTION_DIM
    goal_channels: int = GOAL_CHANNELS
    chunk: int = 16
    execute: int = 8
    n_obs_steps: int = 2
    inference_steps: int = 10
    vision_backbone: str = "resnet18"
    down_dims: tuple[int, ...] = (512, 1024, 2048)
    spatial_softmax_keypoints: int = 32
    separate_encoder_per_camera: bool = True
    action_hz: float = 30.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "image_hw", tuple(int(v) for v in self.image_hw))
        object.__setattr__(self, "down_dims", tuple(int(v) for v in self.down_dims))
        if len(self.image_hw) != 2 or min(self.image_hw) < 1:
            raise ValueError(f"PolicySpec.image_hw must be (h, w) positive, got {self.image_hw}")
        if self.execute > self.chunk:
            raise ValueError(
                f"execute {self.execute} > chunk {self.chunk}: the controller cannot play what was not predicted"
            )

    @property
    def cond_state_dim(self) -> int:
        """What lerobot sees as ``observation.state``: the 9-D state plus the task one-hot (5.3)."""
        return self.state_dim + self.task_dim

    @classmethod
    def from_config(cls, config_root: Path | str | None = None, **overrides: Any) -> PolicySpec:
        """Build the spec from ``config/training.yaml``; ``overrides`` are for tests, not for runs."""
        training = config.load("training", root=config_root)
        block = training["diffusion"]
        spec = cls(
            image_hw=tuple(block["encoder_image_hw"]),
            state_dim=int(training["observation"]["state_dim"]),
            task_dim=len(training["observation"]["task_ids"]),
            action_dim=int(training["action"]["dim"]),
            goal_channels=int(training["observation"]["goal_channels"]),
            chunk=int(block["chunk"]),
            execute=int(block["execute"]),
            n_obs_steps=int(block["obs_history"]),
            inference_steps=int(block["inference_steps"]),
            vision_backbone=str(block["vision_encoder"]),
            down_dims=tuple(block["down_dims"]),
            spatial_softmax_keypoints=int(block["spatial_softmax_keypoints"]),
            separate_encoder_per_camera=bool(block["encoder_per_camera"]),
            action_hz=float(training["rates"]["action_hz"]),
        )
        return replace(spec, **overrides) if overrides else spec

    def to_dict(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PolicySpec:
        return cls(**{k: v for k, v in data.items()})

    def lerobot_config(self, device: str = "cpu") -> DiffusionConfig:
        """The ``DiffusionConfig`` of the wrapped policy (three 3-channel cameras, 12-D state)."""
        features = {key: PolicyFeature(FeatureType.VISUAL, (3, *self.image_hw)) for key in IMAGE_KEYS.values()}
        features[OBS_STATE] = PolicyFeature(FeatureType.STATE, (self.cond_state_dim,))
        return DiffusionConfig(
            input_features=features,
            output_features={ACTION: PolicyFeature(FeatureType.ACTION, (self.action_dim,))},
            device=device,
            n_obs_steps=self.n_obs_steps,
            horizon=self.chunk,
            n_action_steps=self.execute,
            vision_backbone=self.vision_backbone,
            down_dims=self.down_dims,
            spatial_softmax_num_keypoints=self.spatial_softmax_keypoints,
            use_separate_rgb_encoder_per_camera=self.separate_encoder_per_camera,
            noise_scheduler_type="DDIM",
            num_inference_steps=self.inference_steps,
            # policy/dataset.py pads the tail of an episode's chunk and reports the mask; masking the
            # loss there is why it reports it.
            do_mask_loss_for_padding=True,
        )


# --------------------------------------------------------------------------------------------------
# normalisation (lerobot 0.4.4 keeps it in a processor pipeline; a bundle has to carry it itself)
# --------------------------------------------------------------------------------------------------


class _Normalizer(nn.Module):
    """Dataset statistics as buffers, so they travel in the ``state_dict`` and onto the device.

    VISUAL is mean/std and STATE/ACTION are min/max onto [-1, 1] -- the mapping
    ``DiffusionConfig.normalization_mapping`` declares, with the formulas of
    ``lerobot/processor/normalize_processor.py:325-359``. Until :meth:`load_stats` is called the
    buffers are the identity, which is what an untrained model in a shape test wants.
    """

    def __init__(self, spec: PolicySpec) -> None:
        super().__init__()
        channels = {name: (3 + spec.goal_channels if name == "top" else 3) for name in CAMERAS}
        for name, count in channels.items():
            self.register_buffer(f"{name}_mean", torch.zeros(count, 1, 1))
            self.register_buffer(f"{name}_std", torch.ones(count, 1, 1))
        for what, size in (("state", spec.cond_state_dim), ("action", spec.action_dim)):
            self.register_buffer(f"{what}_min", -torch.ones(size))
            self.register_buffer(f"{what}_max", torch.ones(size))
        self.register_buffer("fitted", torch.zeros((), dtype=torch.bool))

    def load_stats(self, stats: Mapping[str, Tensor]) -> None:
        """Install statistics from :func:`dataset_stats` (keys ``top_mean`` ... ``action_max``)."""
        own = dict(self.named_buffers())
        for key, value in stats.items():
            if key not in own:
                raise KeyError(f"unknown statistic {key!r}; known: {sorted(k for k in own if k != 'fitted')}")
            if tuple(value.shape) != tuple(own[key].shape):
                raise ValueError(f"statistic {key} has shape {tuple(value.shape)}, expected {tuple(own[key].shape)}")
            own[key].copy_(value.to(own[key].dtype))
        self.fitted.fill_(True)

    def image(self, name: str, x: Tensor) -> Tensor:
        mean, std = getattr(self, f"{name}_mean"), getattr(self, f"{name}_std")
        return (x - mean) / (std + EPS)

    def _min_max(self, what: str, x: Tensor, inverse: bool) -> Tensor:
        low, high = getattr(self, f"{what}_min"), getattr(self, f"{what}_max")
        span = torch.where(high - low == 0, torch.full_like(high, EPS), high - low)
        return (x + 1) / 2 * span + low if inverse else 2 * (x - low) / span - 1

    def state(self, x: Tensor) -> Tensor:
        return self._min_max("state", x, inverse=False)

    def action(self, x: Tensor) -> Tensor:
        return self._min_max("action", x, inverse=False)

    def unnormalize_action(self, x: Tensor) -> Tensor:
        return self._min_max("action", x, inverse=True)


def dataset_stats(dataset, samples: int = 256, seed: int = 0) -> dict[str, Tensor]:
    """Per-channel image mean/std and state/action min/max over up to ``samples`` random samples.

    A pass over every frame of a real session is minutes of PNG decoding for numbers that converge in
    hundreds of samples, so this draws a reproducible random subset (``seed``) and says how many in
    the run record. The state statistic covers the 12-D vector the policy sees (state + task one-hot).
    """
    count = min(int(samples), len(dataset))
    if count < 1:
        raise ValueError("dataset_stats needs at least one sample")
    order = np.random.default_rng(int(seed)).permutation(len(dataset))[:count]
    sums: dict[str, Tensor] = {}
    squares: dict[str, Tensor] = {}
    pixels = 0
    lows: dict[str, Tensor] = {}
    highs: dict[str, Tensor] = {}
    for index in order:
        sample = dataset[int(index)]
        for name in CAMERAS:
            image = sample[name].to(torch.float64)
            sums[name] = image.sum(dim=(1, 2)) + sums.get(name, 0)
            squares[name] = (image**2).sum(dim=(1, 2)) + squares.get(name, 0)
        pixels += int(sample["top"].shape[1] * sample["top"].shape[2])
        vectors = {
            "state": torch.cat([sample["state"], sample["task_id"]]).to(torch.float64),
            "action": sample["action"].to(torch.float64),
        }
        for what, value in vectors.items():
            flat = value.reshape(-1, value.shape[-1])
            low, high = flat.min(dim=0).values, flat.max(dim=0).values
            lows[what] = low if what not in lows else torch.minimum(lows[what], low)
            highs[what] = high if what not in highs else torch.maximum(highs[what], high)
    stats: dict[str, Tensor] = {}
    for name in CAMERAS:
        mean = sums[name] / pixels
        variance = torch.clamp(squares[name] / pixels - mean**2, min=0.0)
        stats[f"{name}_mean"] = mean.to(torch.float32).reshape(-1, 1, 1)
        stats[f"{name}_std"] = variance.sqrt().to(torch.float32).reshape(-1, 1, 1)
    for what in ("state", "action"):
        stats[f"{what}_min"] = lows[what].to(torch.float32)
        stats[f"{what}_max"] = highs[what].to(torch.float32)
    return stats


# --------------------------------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------------------------------


class GoalDiffusionPolicy(nn.Module):
    """lerobot's Diffusion Policy plus the goal projection, the task one-hot and the statistics.

    Training (``policy/train.py``) calls :meth:`forward` with a batch straight out of
    :class:`policy.dataset.LudoDataset`; inference (:class:`DiffusionAdapter`) calls :meth:`predict`.
    """

    def __init__(self, spec: PolicySpec, *, device: str = "cpu") -> None:
        super().__init__()
        self.spec = spec
        self.lerobot = DiffusionPolicy(spec.lerobot_config(device=device))
        self.goal_proj = nn.Conv2d(3 + spec.goal_channels, 3, kernel_size=1, bias=False)
        with torch.no_grad():
            # Identity on the RGB, zero on the goal channels: at step 0 the encoder sees exactly the
            # image it would see without the goal, and the gradient decides how much goal to let in.
            self.goal_proj.weight.zero_()
            for channel in range(3):
                self.goal_proj.weight[channel, channel, 0, 0] = 1.0
        self.norm = _Normalizer(spec)

    def __repr__(self) -> str:
        params = sum(p.numel() for p in self.parameters())
        return (
            f"GoalDiffusionPolicy(image_hw={self.spec.image_hw}, chunk={self.spec.chunk}, "
            f"execute={self.spec.execute}, ddim={self.spec.inference_steps}, params={params / 1e6:.1f}M)"
        )

    # -- batch preparation ------------------------------------------------------------------------

    def _images(self, batch: Mapping[str, Tensor]) -> dict[str, Tensor]:
        """Resize, normalise and (for `top`) project every camera to ``(B, S, 3, h, w)``."""
        out: dict[str, Tensor] = {}
        for name, key in IMAGE_KEYS.items():
            x = _with_steps(batch[name], self.spec.n_obs_steps, base_ndim=4)
            b, s = x.shape[:2]
            flat = x.flatten(0, 1)
            if tuple(flat.shape[-2:]) != self.spec.image_hw:
                flat = nn.functional.interpolate(flat, size=self.spec.image_hw, mode="bilinear", align_corners=False)
            flat = self.norm.image(name, flat)
            if name == "top":
                if flat.shape[1] != 3 + self.spec.goal_channels:
                    raise ValueError(
                        f"`top` must carry {3 + self.spec.goal_channels} channels (RGB + goal), got {flat.shape[1]}"
                    )
                flat = self.goal_proj(flat)
            out[key] = flat.unflatten(0, (b, s))
        return out

    def _state(self, batch: Mapping[str, Tensor]) -> Tensor:
        state = _with_steps(batch["state"], self.spec.n_obs_steps, base_ndim=2)
        task = _with_steps(batch["task_id"], self.spec.n_obs_steps, base_ndim=2)
        return self.norm.state(torch.cat([state, task], dim=-1))

    def _lerobot_batch(self, batch: Mapping[str, Tensor]) -> dict[str, Tensor]:
        out: dict[str, Tensor] = self._images(batch)
        out[OBS_STATE] = self._state(batch)
        return out

    # -- training ---------------------------------------------------------------------------------

    def forward(self, batch: Mapping[str, Tensor]) -> Tensor:
        """The diffusion loss on one batch of :class:`policy.dataset.LudoDataset` samples."""
        prepared = self._lerobot_batch(batch)
        prepared[ACTION] = self.norm.action(batch["action"])
        mask = batch.get("action_mask")
        prepared["action_is_pad"] = (
            torch.zeros(batch["action"].shape[:2], dtype=torch.bool, device=batch["action"].device)
            if mask is None
            else mask < 0.5
        )
        loss, _ = self.lerobot.forward(prepared)
        return loss

    # -- inference --------------------------------------------------------------------------------

    @torch.no_grad()
    def predict(self, batch: Mapping[str, Tensor], *, noise: Tensor | None = None) -> Tensor:
        """``(B, chunk, action_dim)`` absolute actions, in joint units.

        The whole horizon is returned, starting at the current frame: ``policy/dataset.py`` aligns
        the action chunk at delta 0, unlike lerobot's own ``generate_actions``, which slices from
        ``n_obs_steps - 1`` for a dataset aligned at delta ``1 - n_obs_steps`` (see the module
        docstring).
        """
        prepared = self._lerobot_batch(batch)
        prepared[OBS_IMAGES] = torch.stack([prepared[key] for key in IMAGE_KEYS.values()], dim=-4)
        model = self.lerobot.diffusion
        global_cond = model._prepare_global_conditioning(prepared)
        sample = model.conditional_sample(prepared[OBS_STATE].shape[0], global_cond=global_cond, noise=noise)
        return self.norm.unnormalize_action(sample)


def _with_steps(x: Tensor, n_obs_steps: int, *, base_ndim: int) -> Tensor:
    """Give ``x`` an observation-step dimension, repeating the frame if it has none.

    ``base_ndim`` is the rank of a batch of single frames: 4 for ``(B, C, H, W)`` images, 2 for
    ``(B, D)`` vectors. One more than that is a batch that already carries the step dimension.
    ``policy/dataset.py`` yields one frame per sample, so training repeats it; the adapter stacks a
    real queue and passes a tensor that already has the dimension. See the module docstring.
    """
    if x.ndim == base_ndim:
        return x.unsqueeze(1).expand(-1, n_obs_steps, *([-1] * (base_ndim - 1)))
    if x.ndim != base_ndim + 1:
        raise ValueError(f"expected a tensor of rank {base_ndim} or {base_ndim + 1}, got shape {tuple(x.shape)}")
    if x.shape[1] != n_obs_steps:
        raise ValueError(f"expected {n_obs_steps} observation steps, got {x.shape[1]}")
    return x


# --------------------------------------------------------------------------------------------------
# the runtime adapter (runtime.policy_api.Policy)
# --------------------------------------------------------------------------------------------------


class DiffusionAdapter:
    """An inference bundle driving ``runtime/controller.py`` (:class:`runtime.policy_api.Policy`).

    ``reset`` drops the observation queue, ``act`` returns the whole ``chunk`` (16) of which the
    controller plays ``execute`` (8) before asking again -- the receding horizon of CLAUDE.md 5.2.
    ``seed`` fixes the initial diffusion noise, which makes two adapters holding the same weights
    return the same actions (``policy/export.py`` round-trips on exactly that).
    """

    def __init__(
        self,
        bundle: Path | str,
        *,
        device: str = "cpu",
        seed: int | None = None,
        inference_steps: int | None = None,
    ) -> None:
        path = Path(bundle)
        manifest_path = path if path.is_file() else path / BUNDLE_FILE
        if not manifest_path.is_file():
            raise FileNotFoundError(f"{manifest_path} is not an inference bundle (policy/export.py writes one)")
        self.path = manifest_path.parent
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        spec = PolicySpec.from_dict(self.manifest["spec"])
        if inference_steps is not None:
            spec = replace(spec, inference_steps=int(inference_steps))
        self.spec = spec
        self.device = torch.device(device)
        weights = torch.load(self.path / WEIGHTS_FILE, map_location=self.device, weights_only=True)
        self.model = GoalDiffusionPolicy(spec, device=str(self.device))
        self.model.load_state_dict(weights["state_dict"])
        self.model.to(self.device).eval()
        self.seed = seed
        self._generator = None if seed is None else torch.Generator(device=self.device).manual_seed(int(seed))
        self._queue: deque[dict[str, Tensor]] = deque(maxlen=spec.n_obs_steps)
        self.command: Command | None = None
        self.calls = 0
        #: Wall-clock cost of the last :meth:`act`, split into preparation and diffusion (ms).
        self.last_timing: dict[str, float] = {}

    def __repr__(self) -> str:
        return (
            f"DiffusionAdapter(bundle={self.path.name!r}, ddim={self.spec.inference_steps}, "
            f"chunk={self.spec.chunk}, device={self.device.type}, calls={self.calls})"
        )

    # -- runtime.policy_api.Policy ----------------------------------------------------------------

    def reset(self, command: Command) -> None:
        """Start a primitive: drop the observation queue and restart the noise sequence."""
        self.command = command
        self.calls = 0
        self._queue.clear()
        if self.seed is not None:
            self._generator = torch.Generator(device=self.device).manual_seed(int(self.seed))

    def act(self, observation: Observation) -> ActionChunk:
        """One diffusion sample: ``chunk`` absolute 9-D actions at ``rates.action_hz``."""
        start = time.perf_counter()
        self._queue.append(self._frame(observation))
        while len(self._queue) < self._queue.maxlen:  # type: ignore[operator]
            self._queue.appendleft(self._queue[0])
        batch = {
            key: torch.stack([frame[key] for frame in self._queue]).unsqueeze(0)
            for key in (*CAMERAS, "state", "task_id")
        }
        prepared = time.perf_counter()
        noise = None
        if self._generator is not None:
            noise = torch.randn(
                (1, self.spec.chunk, self.spec.action_dim), generator=self._generator, device=self.device
            )
        actions = self.model.predict(batch, noise=noise)
        self.calls += 1
        done = time.perf_counter()
        self.last_timing = {"prepare_ms": (prepared - start) * 1e3, "sample_ms": (done - prepared) * 1e3,
                            "act_ms": (done - start) * 1e3}
        return ActionChunk(actions=actions[0].to("cpu").numpy().astype(np.float64), hz=self.spec.action_hz)

    def done(self, observation: Observation) -> bool:
        """Always False: this model has no termination head.

        CLAUDE.md 5.5 gives the policy its own termination signal; nothing in the data labels one yet,
        so the controller's ``runtime.primitive_timeout_s`` (20 s) ends every primitive and the engine
        verifies the state change. A termination head is a separate task, not a default of False
        dressed up as one.
        """
        return False

    # -- observation -> tensors -------------------------------------------------------------------

    def _frame(self, observation: Observation) -> dict[str, Tensor]:
        """One observation as the tensors :class:`GoalDiffusionPolicy` expects, on the device."""
        frame = {name: _image_tensor(getattr(observation, name), self.device) for name in CAMERAS}
        goal = torch.as_tensor(np.asarray(observation.goal), dtype=torch.float32, device=self.device)
        if goal.shape[-2:] != frame["top"].shape[-2:]:
            raise ValueError(f"goal {tuple(goal.shape)} does not match `top` {tuple(frame['top'].shape)}")
        frame["top"] = torch.cat([frame["top"], goal], dim=0)
        frame["state"] = torch.as_tensor(observation.state, dtype=torch.float32, device=self.device)
        frame["task_id"] = torch.as_tensor(observation.task_id, dtype=torch.float32, device=self.device)
        return frame


def _image_tensor(image: np.ndarray, device: torch.device) -> Tensor:
    """``(h, w, 3)`` uint8 or float to ``(3, h, w)`` float32 in [0, 1], the dataset's convention."""
    array = np.asarray(image)
    tensor = torch.as_tensor(np.ascontiguousarray(array.transpose(2, 0, 1)), device=device)
    return tensor.to(torch.float32) / 255.0 if array.dtype == np.uint8 else tensor.to(torch.float32)


# --------------------------------------------------------------------------------------------------
# the latency benchmark (CLAUDE.md 5.8: 10 Hz, or the fallback ladder)
# --------------------------------------------------------------------------------------------------


def benchmark(
    adapter: DiffusionAdapter, observation: Observation, trials: int = 20, warmup: int = 2
) -> dict[str, float]:
    """Mean/p95 wall-clock cost of :meth:`DiffusionAdapter.act` over ``trials`` calls."""
    adapter.reset(Command(Primitive.MOVE, None, None, None))
    for _ in range(max(int(warmup), 0)):
        adapter.act(observation)
    timings: list[dict[str, float]] = []
    for _ in range(int(trials)):
        adapter.act(observation)
        timings.append(dict(adapter.last_timing))
    out = {key: float(np.mean([t[key] for t in timings])) for key in timings[0]}
    out["median_ms"] = float(np.median([t["act_ms"] for t in timings]))
    out["p95_ms"] = float(np.percentile([t["act_ms"] for t in timings], 95))
    out["trials"] = float(trials)
    return out


def _synthetic_observation(sizes: Mapping[str, Iterable[int]], task_dim: int, seed: int = 0) -> Observation:
    """An observation of the configured frame sizes, for a latency measurement with no hardware."""
    rng = np.random.default_rng(seed)
    frames = {name: rng.integers(0, 256, (int(hw[1]), int(hw[0]), 3), dtype=np.uint8)
              for name, hw in ((n, list(v)) for n, v in sizes.items())}
    goal = rng.random((GOAL_CHANNELS, frames["top"].shape[0], frames["top"].shape[1])).astype(np.float32)
    task = np.zeros(task_dim, dtype=np.float32)
    task[0] = 1.0
    return Observation(ts_ns=0, top=frames["top"], oblique=frames["oblique"], palm=frames["palm"],
                       state=np.zeros(ACTION_DIM), goal=goal, task_id=task)


def main(argv: list[str] | None = None) -> int:
    """``python -m policy.diffusion --bundle data/checkpoints/<run>/bundle --trials 20``."""
    parser = argparse.ArgumentParser(description="Measure DiffusionAdapter.act() latency (CLAUDE.md 5.8).")
    parser.add_argument("--bundle", required=True, help="inference bundle directory (policy/export.py)")
    parser.add_argument("--trials", type=int, default=20, help="calls to time (default 20)")
    parser.add_argument("--inference-steps", type=int, default=None, help="DDIM steps (default: the bundle's)")
    parser.add_argument("--device", default="cpu", help="cpu (default) or cuda")
    parser.add_argument("--seed", type=int, default=0, help="noise seed, so the run is reproducible")
    args = parser.parse_args(argv)

    adapter = DiffusionAdapter(args.bundle, device=args.device, seed=args.seed,
                               inference_steps=args.inference_steps)
    sizes = config.load("training")["observation"]["images"]
    observation = _synthetic_observation(sizes, adapter.spec.task_dim)
    result = benchmark(adapter, observation, trials=args.trials)
    budget_ms = 1e3 / float(config.load("training")["rates"]["policy_hz"])
    print(f"{adapter!r}")
    frames = ", ".join(f"{name}={list(hw)}" for name, hw in sizes.items())
    print(f"frames: {frames} -> encoder {adapter.spec.image_hw}")
    print(f"act(): mean {result['act_ms']:.0f} ms (prepare {result['prepare_ms']:.0f} ms, "
          f"diffusion {result['sample_ms']:.0f} ms), median {result['median_ms']:.0f} ms, "
          f"p95 {result['p95_ms']:.0f} ms over {args.trials} calls")
    verdict = "within" if result["act_ms"] <= budget_ms else f"OVER by {result['act_ms'] / budget_ms:.1f}x"
    print(f"budget at {int(1e3 / budget_ms)} Hz: {budget_ms:.0f} ms -> {verdict}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
