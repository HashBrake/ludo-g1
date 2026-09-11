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

Observation history (T-034)
---------------------------
``diffusion.obs_history`` is 2, and both halves now carry two real frames: ``policy/dataset.py``
yields the last ``n_obs_steps`` camera frames and states per sample (lerobot ``delta_timestamps`` on
the observation keys, oldest first, padded at the start of an episode with its first frame), and
:class:`DiffusionAdapter` keeps a queue with the same order and the same padding rule. Until T-034
the dataset yielded one frame and this module repeated it, which trained the model on a still image
and ran it on motion; :func:`policy._shared.with_steps` still repeats what is genuinely constant over
an episode (the task one-hot) and nothing else.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
from collections.abc import Mapping
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

from engine.interface import Command
from policy._shared import (
    BUNDLE_FILE,
    EPS,
    IMAGE_KEYS,
    WEIGHTS_FILE,
    Normalizer,
    benchmark,
    dataset_stats,
    observation_frame,
    set_torch_threads,
    synthetic_observation,
    with_steps,
)
from policy.dataset import CAMERAS
from runtime import config
from runtime.policy_api import GOAL_CHANNELS, ActionChunk, Observation
from runtime.types import ACTION_DIM

#: Re-exported from :mod:`policy._shared`, which both models of 5.7 feed on (T-030 review): a caller
#: that has a diffusion bundle in its hand should not have to know where the constants moved to.
__all__ = [
    "BUNDLE_FILE",
    "EPS",
    "IMAGE_KEYS",
    "WEIGHTS_FILE",
    "DiffusionAdapter",
    "GoalDiffusionPolicy",
    "PolicySpec",
    "benchmark",
    "dataset_stats",
    "main",
    "set_torch_threads",
]


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
    def from_config(
        cls, config_root: Path | str | None = None, *, block: str = "diffusion", **overrides: Any
    ) -> PolicySpec:
        """Build the spec from ``config/training.yaml``; ``overrides`` are for tests, not for runs.

        ``block`` names which block of the file describes the architecture: ``diffusion`` (5.7) or
        ``diffusion_small``, the smaller fallback configuration of D-019 (one shared encoder, 120x160
        inputs, a quarter-width U-Net). Both build the same class and run through the same wrapper --
        the block is the *only* difference -- so the two are comparable and the run record says which
        one produced a checkpoint (``policy/train.py --config-block``).
        """
        training = config.load("training", root=config_root)
        if block not in training:
            raise config.ConfigError(f"config/training.yaml has no {block!r} block")
        block = training[block]
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
        self.norm = Normalizer(spec)

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
            x = with_steps(batch[name], self.spec.n_obs_steps, base_ndim=4)
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
        state = with_steps(batch["state"], self.spec.n_obs_steps, base_ndim=2)
        task = with_steps(batch["task_id"], self.spec.n_obs_steps, base_ndim=2)
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
        #: The thread pool of D-020, sized before the model is built (once per process).
        self.torch_threads = set_torch_threads()
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
        batch = self._batch(observation)
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
        return observation_frame(observation, self.device)

    def _batch(self, observation: Observation) -> dict[str, Tensor]:
        """Queue the observation and stack the last ``n_obs_steps`` frames, oldest first.

        The queue is the inference half of the history ``policy/dataset.py`` yields per sample, and
        the two must agree on both conventions or the model sees one thing in training and another on
        the robot (T-034). **Order**: oldest first, so appending on the right and stacking in queue
        order is the dataset's ``[-(S-1)/fps ... 0]``. **Padding**: before the queue has filled -- the
        first call of a primitive -- the oldest frame is repeated, which is what lerobot's
        ``delta_timestamps`` clamping does at the start of an episode.
        ``tests/test_diffusion.py::test_the_adapter_queue_and_the_dataset_history_agree`` pins both
        against a recorded episode, frame for frame.
        """
        self._queue.append(self._frame(observation))
        while len(self._queue) < self._queue.maxlen:  # type: ignore[operator]
            self._queue.appendleft(self._queue[0])
        return {
            key: torch.stack([frame[key] for frame in self._queue]).unsqueeze(0)
            for key in (*CAMERAS, "state", "task_id")
        }


# --------------------------------------------------------------------------------------------------
# the latency benchmark (CLAUDE.md 5.8: 10 Hz, or the fallback ladder)
# --------------------------------------------------------------------------------------------------


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
    observation = synthetic_observation(sizes, adapter.spec.task_dim)
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
