"""ACT, the baseline of CLAUDE.md 5.7, on exactly the observation policy/diffusion.py takes.

```python
from policy.act import ACTAdapter, ACTSpec, GoalACTPolicy

model = GoalACTPolicy(ACTSpec.from_config())          # training: policy/train.py --policy act
policy = ACTAdapter("data/checkpoints/<run>/bundle")  # inference: runtime/controller.py
```

Nothing here moves the robot. :class:`ACTAdapter` satisfies :class:`runtime.policy_api.Policy` and
returns an :class:`~runtime.policy_api.ActionChunk`; the only code that sends anything is
``runtime/controller.py``, through ``runtime/safety.py`` (R1, R3).

Why this exists: CLAUDE.md 5.7 requires the baseline to be trained on *every* dataset the diffusion
model is trained on, so the comparison is always available. That is why the observation adaptation
below is the same code as ``policy/diffusion.py`` -- the same 1x1 goal projection, the same task
one-hot on the state, the same encoder input size, the same statistics buffers -- and why
``policy/train.py --policy act`` changes nothing but the model (same sessions, same split, same
dataset, same run directory layout). Anything else and a difference in the eval numbers would not be
a difference between two architectures.

What lerobot 0.4.4's ACT does differently from its Diffusion Policy, and what the wrapper does
-----------------------------------------------------------------------------------------------
* **One backbone for every camera.** ``ACT.__init__`` (``lerobot/policies/act/modeling_act.py:322``)
  builds a single ``self.backbone``; there is no ``use_separate_rgb_encoder_per_camera``. So the
  goal channels cannot be absorbed by a 5-channel first convolution on the `top` encoder alone even
  in principle, and the 1x1 projection of ``policy/diffusion.py`` is the only route: RGB through
  unchanged at initialisation, zero weight on the two goal channels, the gradient decides the rest.
* **One observation step.** ``ACTConfig.__post_init__``
  (``lerobot/policies/act/configuration_act.py:148``) refuses any ``n_obs_steps`` but 1, so the
  train/inference history mismatch recorded for the diffusion wrapper in T-029 does not exist here:
  ``policy/dataset.py`` yields one frame per sample and ACT wants exactly one.
* **Normalisation** lives in lerobot's processor pipelines, as it does for the Diffusion Policy, and
  is replaced here by the same :class:`policy.diffusion._Normalizer` buffers, so a bundle is
  self-contained and both models normalise identically. Upstream ACT maps STATE and ACTION to
  MEAN_STD where the Diffusion Policy maps them to MIN_MAX; since neither pipeline runs, this
  wrapper uses MIN_MAX for both models. Two baselines that normalise differently do not compare.
* **The chunk starts at the current frame** for ACT by construction:
  ``ACTConfig.action_delta_indices`` is ``range(chunk_size)`` = [0 .. 31], which is the alignment
  ``policy/dataset.py`` already produces. (The Diffusion Policy needed a note here; ACT does not.)

Temporal ensembling (5.7), and why it is in the adapter
-------------------------------------------------------
lerobot implements ACT's temporal ensembling in ``ACTTemporalEnsembler`` and gates it on
``temporal_ensemble_coeff``. Setting that coefficient in 0.4.4 **forces ``n_action_steps = 1``**
(``configuration_act.py:137``: "n_action_steps must be 1 when using temporal ensembling ... the
policy needs to be queried every step"), because ``ACTPolicy.select_action`` consumes exactly one
ensembled action per query. Our contract is the other shape: ``runtime/controller.py`` asks at
``rates.policy_hz`` (10 Hz) and plays the returned chunk at ``rates.action_hz`` (30 Hz). So the
wrapped ``ACTConfig`` keeps ``temporal_ensemble_coeff=None`` (``select_action`` is never called) and
:class:`TemporalEnsemble` does the ensembling over the chunk, with the same weights.

The math, generalised from one action per query to ``stride`` actions per query. Query *k* predicts
``chunk`` (32) actions for absolute action-steps ``k*s + j``, ``j = 0 .. 31``, where
``s = round(action_hz / policy_hz)`` (3) is how far the controller advances between two queries. An
action-step ``T`` is therefore predicted by every query ``k`` with ``k*s <= T < k*s + 32``. Ordering
those predictions oldest first as ``a_0 .. a_n``, the ensembled action is

    ensembled(T) = sum_i w_i * a_i / sum_i w_i,   w_i = exp(-coeff * i)

which is exactly lerobot's rule (``ACTTemporalEnsembler``: ``exp(-coeff * arange(chunk_size))``,
oldest weighted most at the default coefficient 0.01), written offline instead of online because the
online recursion in ``modeling_act.py:210`` assumes the query stride is 1.
``tests/test_act.py::test_temporal_ensemble_matches_lerobots_own`` pins the two against each other at
stride 1.

:meth:`ACTAdapter.act` returns ``act.expose`` (16) ensembled actions, so the controller contract of
5.2 is unchanged and ACT's 32 is internal. ``s`` is the *nominal* stride from the configured rates: a
late query makes the ensemble slightly stale in the same way a late query makes a receding horizon
slightly stale, and neither adapter is told what the controller actually played.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import torch
from lerobot.configs.types import FeatureType, PolicyFeature
from lerobot.policies.act.configuration_act import ACTConfig
from lerobot.policies.act.modeling_act import ACTPolicy
from lerobot.utils.constants import ACTION, OBS_STATE
from torch import Tensor, nn

from engine.interface import Command
from policy.dataset import CAMERAS

# The observation adaptation, the statistics buffers and the latency measurement are *shared* with
# the Diffusion Policy wrapper on purpose (5.7: the baseline must be comparable, see the docstring),
# so they are imported rather than copied -- including the two module-private ones.
from policy.diffusion import BUNDLE_FILE, IMAGE_KEYS, WEIGHTS_FILE, _image_tensor, _Normalizer, benchmark
from runtime import config
from runtime.policy_api import GOAL_CHANNELS, ActionChunk, Observation
from runtime.types import ACTION_DIM

__all__ = ["ACTAdapter", "ACTSpec", "GoalACTPolicy", "TemporalEnsemble", "main"]


# --------------------------------------------------------------------------------------------------
# the specification: everything needed to rebuild the model bit for bit
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ACTSpec:
    """The architecture, from ``config/training.yaml`` ``act`` unless a caller overrides it.

    The counterpart of :class:`policy.diffusion.PolicySpec`, and deliberately the same shape: an
    exported bundle stores it verbatim, so loading a bundle never consults the config file.
    """

    image_hw: tuple[int, int]
    state_dim: int = ACTION_DIM
    task_dim: int = 3
    action_dim: int = ACTION_DIM
    goal_channels: int = GOAL_CHANNELS
    chunk: int = 32
    expose: int = 16
    temporal_ensemble: bool = True
    temporal_ensemble_coeff: float = 0.01
    ensemble_stride: int = 3
    vision_backbone: str = "resnet18"
    pretrained_backbone_weights: str | None = None
    dim_model: int = 512
    n_heads: int = 8
    dim_feedforward: int = 3200
    n_encoder_layers: int = 4
    n_decoder_layers: int = 1
    use_vae: bool = True
    latent_dim: int = 32
    n_vae_encoder_layers: int = 4
    dropout: float = 0.1
    kl_weight: float = 10.0
    action_hz: float = 30.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "image_hw", tuple(int(v) for v in self.image_hw))
        if len(self.image_hw) != 2 or min(self.image_hw) < 1:
            raise ValueError(f"ACTSpec.image_hw must be (h, w) positive, got {self.image_hw}")
        if self.expose > self.chunk:
            raise ValueError(f"expose {self.expose} > chunk {self.chunk}: cannot hand out what was not predicted")
        if self.ensemble_stride < 1:
            raise ValueError(f"ensemble_stride must be >= 1, got {self.ensemble_stride}")

    @property
    def cond_state_dim(self) -> int:
        """What lerobot sees as ``observation.state``: the 9-D state plus the task one-hot (5.3)."""
        return self.state_dim + self.task_dim

    @classmethod
    def from_config(cls, config_root: Path | str | None = None, **overrides: Any) -> ACTSpec:
        """Build the spec from ``config/training.yaml``; ``overrides`` are for tests, not for runs."""
        training = config.load("training", root=config_root)
        block = training["act"]
        rates = training["rates"]
        weights = block["pretrained_backbone_weights"]
        spec = cls(
            image_hw=tuple(block["encoder_image_hw"]),
            state_dim=int(training["observation"]["state_dim"]),
            task_dim=len(training["observation"]["task_ids"]),
            action_dim=int(training["action"]["dim"]),
            goal_channels=int(training["observation"]["goal_channels"]),
            chunk=int(block["chunk"]),
            expose=int(block["expose"]),
            temporal_ensemble=bool(block["temporal_ensemble"]),
            temporal_ensemble_coeff=float(block["temporal_ensemble_coeff"]),
            # How far the controller advances between two queries at the configured rates (5.2).
            ensemble_stride=max(1, round(float(rates["action_hz"]) / float(rates["policy_hz"]))),
            vision_backbone=str(block["vision_encoder"]),
            pretrained_backbone_weights=None if weights is None else str(weights),
            dim_model=int(block["dim_model"]),
            n_heads=int(block["n_heads"]),
            dim_feedforward=int(block["dim_feedforward"]),
            n_encoder_layers=int(block["n_encoder_layers"]),
            n_decoder_layers=int(block["n_decoder_layers"]),
            use_vae=bool(block["use_vae"]),
            latent_dim=int(block["latent_dim"]),
            n_vae_encoder_layers=int(block["n_vae_encoder_layers"]),
            dropout=float(block["dropout"]),
            kl_weight=float(block["kl_weight"]),
            action_hz=float(rates["action_hz"]),
        )
        return replace(spec, **overrides) if overrides else spec

    def to_dict(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ACTSpec:
        return cls(**dict(data))

    def lerobot_config(self, device: str = "cpu") -> ACTConfig:
        """The ``ACTConfig`` of the wrapped policy (three 3-channel cameras, 12-D state).

        ``temporal_ensemble_coeff`` stays None and ``n_action_steps`` is ``expose``: the ensembling
        is :class:`TemporalEnsemble`'s, and ``ACTPolicy.select_action`` -- the only consumer of
        either setting -- is never called (module docstring).
        """
        features = {key: PolicyFeature(FeatureType.VISUAL, (3, *self.image_hw)) for key in IMAGE_KEYS.values()}
        features[OBS_STATE] = PolicyFeature(FeatureType.STATE, (self.cond_state_dim,))
        return ACTConfig(
            input_features=features,
            output_features={ACTION: PolicyFeature(FeatureType.ACTION, (self.action_dim,))},
            device=device,
            n_obs_steps=1,
            chunk_size=self.chunk,
            n_action_steps=self.expose,
            vision_backbone=self.vision_backbone,
            pretrained_backbone_weights=self.pretrained_backbone_weights,
            dim_model=self.dim_model,
            n_heads=self.n_heads,
            dim_feedforward=self.dim_feedforward,
            n_encoder_layers=self.n_encoder_layers,
            n_decoder_layers=self.n_decoder_layers,
            use_vae=self.use_vae,
            latent_dim=self.latent_dim,
            n_vae_encoder_layers=self.n_vae_encoder_layers,
            dropout=self.dropout,
            kl_weight=self.kl_weight,
            temporal_ensemble_coeff=None,
        )


# --------------------------------------------------------------------------------------------------
# temporal ensembling over a chunk (the math is in the module docstring)
# --------------------------------------------------------------------------------------------------


class TemporalEnsemble:
    """Exponentially weighted average of every prediction that covers an action-step.

    One :meth:`update` per policy query: it takes the query's ``(chunk, action_dim)`` prediction and
    returns the ``(expose, action_dim)`` the controller is handed, each entry averaged over every
    still-live prediction of that action-step with weights ``exp(-coeff * age)``, age 0 being the
    oldest. ``stride`` is how many action-steps the controller advances between two queries.
    """

    def __init__(self, chunk: int, expose: int, coeff: float, stride: int) -> None:
        if expose > chunk or expose < 1 or stride < 1:
            raise ValueError(
                f"TemporalEnsemble needs 1 <= expose <= chunk and stride >= 1, got {expose=} {chunk=} {stride=}"
            )
        self.chunk, self.expose, self.stride = int(chunk), int(expose), int(stride)
        self.coeff = float(coeff)
        #: ``w_i`` for the i-th oldest prediction of one action-step; lerobot's own weights.
        self.weights = np.exp(-self.coeff * np.arange(self.chunk, dtype=np.float64))
        self.reset()

    def __repr__(self) -> str:
        return (f"TemporalEnsemble(chunk={self.chunk}, expose={self.expose}, coeff={self.coeff:g}, "
                f"stride={self.stride}, live={len(self._history)})")

    def reset(self) -> None:
        """Forget every prediction: a new primitive is not a continuation of the last one."""
        self._history: list[tuple[int, np.ndarray]] = []
        self._t = 0

    def update(self, actions: np.ndarray) -> np.ndarray:
        """Fold one ``(chunk, action_dim)`` prediction in and return the next ``(expose, action_dim)``."""
        chunk = np.asarray(actions, dtype=np.float64)
        if chunk.shape[0] != self.chunk:
            raise ValueError(f"expected {self.chunk} actions per prediction, got {chunk.shape[0]}")
        self._history.append((self._t, chunk))
        out = np.empty((self.expose, chunk.shape[1]), dtype=np.float64)
        for j in range(self.expose):
            step = self._t + j
            # The history is in query order, so slicing it in order weights the oldest prediction first.
            covering = [entry[step - start] for start, entry in self._history if start <= step < start + self.chunk]
            weights = self.weights[: len(covering)]
            out[j] = (weights[:, None] * np.stack(covering)).sum(axis=0) / weights.sum()
        self._t += self.stride
        self._history = [(s, a) for s, a in self._history if s + self.chunk > self._t]
        return out


# --------------------------------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------------------------------


class GoalACTPolicy(nn.Module):
    """lerobot's ACT plus the goal projection, the task one-hot and the normalisation statistics.

    Training (``policy/train.py --policy act``) calls :meth:`forward` with a batch straight out of
    :class:`policy.dataset.LudoDataset`; inference (:class:`ACTAdapter`) calls :meth:`predict`. The
    interface is :class:`policy.diffusion.GoalDiffusionPolicy`'s, so the training loop is shared.
    """

    def __init__(self, spec: ACTSpec, *, device: str = "cpu") -> None:
        super().__init__()
        self.spec = spec
        self.lerobot = ACTPolicy(spec.lerobot_config(device=device))
        self.goal_proj = nn.Conv2d(3 + spec.goal_channels, 3, kernel_size=1, bias=False)
        with torch.no_grad():
            # Identity on the RGB, zero on the goal channels, exactly as in policy/diffusion.py.
            self.goal_proj.weight.zero_()
            for channel in range(3):
                self.goal_proj.weight[channel, channel, 0, 0] = 1.0
        self.norm = _Normalizer(spec)

    def __repr__(self) -> str:
        params = sum(p.numel() for p in self.parameters())
        return (
            f"GoalACTPolicy(image_hw={self.spec.image_hw}, chunk={self.spec.chunk}, "
            f"expose={self.spec.expose}, vae={self.spec.use_vae}, params={params / 1e6:.1f}M)"
        )

    # -- batch preparation ------------------------------------------------------------------------

    def _images(self, batch: Mapping[str, Tensor]) -> dict[str, Tensor]:
        """Resize, normalise and (for `top`) project every camera to ``(B, 3, h, w)``.

        No observation-step dimension: ACT takes exactly one frame (module docstring).
        """
        out: dict[str, Tensor] = {}
        for name, key in IMAGE_KEYS.items():
            x = batch[name]
            if x.ndim != 4:
                raise ValueError(
                    f"`{name}` must be (B, C, H, W): ACT takes one observation step, got {tuple(x.shape)}"
                )
            if tuple(x.shape[-2:]) != self.spec.image_hw:
                x = nn.functional.interpolate(x, size=self.spec.image_hw, mode="bilinear", align_corners=False)
            x = self.norm.image(name, x)
            if name == "top":
                if x.shape[1] != 3 + self.spec.goal_channels:
                    raise ValueError(
                        f"`top` must carry {3 + self.spec.goal_channels} channels (RGB + goal), got {x.shape[1]}"
                    )
                x = self.goal_proj(x)
            out[key] = x
        return out

    def _state(self, batch: Mapping[str, Tensor]) -> Tensor:
        return self.norm.state(torch.cat([batch["state"], batch["task_id"]], dim=-1))

    def _lerobot_batch(self, batch: Mapping[str, Tensor]) -> dict[str, Tensor]:
        out: dict[str, Tensor] = self._images(batch)
        out[OBS_STATE] = self._state(batch)
        return out

    # -- training ---------------------------------------------------------------------------------

    def forward(self, batch: Mapping[str, Tensor]) -> Tensor:
        """ACT's loss (masked L1 plus ``kl_weight`` x KL) on one batch of dataset samples."""
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
    def predict(self, batch: Mapping[str, Tensor]) -> Tensor:
        """``(B, chunk, action_dim)`` absolute actions, in joint units.

        Deterministic: with ``use_vae`` the VAE encoder runs in training only
        (``modeling_act.py:395``), so at inference the latent is zeros and two calls on the same
        observation return the same chunk. That is what ``policy/export.py`` round-trips on.
        """
        actions = self.lerobot.predict_action_chunk(self._lerobot_batch(batch))
        return self.norm.unnormalize_action(actions)


# --------------------------------------------------------------------------------------------------
# the runtime adapter (runtime.policy_api.Policy)
# --------------------------------------------------------------------------------------------------


class ACTAdapter:
    """An ACT inference bundle driving ``runtime/controller.py`` (:class:`runtime.policy_api.Policy`).

    ``reset`` drops the temporal ensemble, ``act`` runs one forward pass and returns ``expose`` (16)
    ensembled actions of the ``chunk`` (32) ACT predicts. No observation queue: ACT takes one frame.
    """

    def __init__(self, bundle: Path | str, *, device: str = "cpu", temporal_ensemble: bool | None = None) -> None:
        path = Path(bundle)
        manifest_path = path if path.is_file() else path / BUNDLE_FILE
        if not manifest_path.is_file():
            raise FileNotFoundError(f"{manifest_path} is not an inference bundle (policy/export.py writes one)")
        self.path = manifest_path.parent
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        spec = ACTSpec.from_dict(self.manifest["spec"])
        if temporal_ensemble is not None:
            spec = replace(spec, temporal_ensemble=bool(temporal_ensemble))
        self.spec = spec
        self.device = torch.device(device)
        weights = torch.load(self.path / WEIGHTS_FILE, map_location=self.device, weights_only=True)
        self.model = GoalACTPolicy(spec, device=str(self.device))
        self.model.load_state_dict(weights["state_dict"])
        self.model.to(self.device).eval()
        self.ensemble = TemporalEnsemble(spec.chunk, spec.expose, spec.temporal_ensemble_coeff, spec.ensemble_stride)
        self.command: Command | None = None
        self.calls = 0
        #: Wall-clock cost of the last :meth:`act`, split into preparation and forward pass (ms).
        self.last_timing: dict[str, float] = {}

    def __repr__(self) -> str:
        return (
            f"ACTAdapter(bundle={self.path.name!r}, chunk={self.spec.chunk}, expose={self.spec.expose}, "
            f"ensemble={self.spec.temporal_ensemble}, device={self.device.type}, calls={self.calls})"
        )

    # -- runtime.policy_api.Policy ----------------------------------------------------------------

    def reset(self, command: Command) -> None:
        """Start a primitive: forget every prediction the ensemble is still averaging."""
        self.command = command
        self.calls = 0
        self.ensemble.reset()

    def act(self, observation: Observation) -> ActionChunk:
        """One ACT forward pass: ``expose`` absolute 9-D actions at ``rates.action_hz``."""
        start = time.perf_counter()
        batch = {key: value.unsqueeze(0) for key, value in self._frame(observation).items()}
        prepared = time.perf_counter()
        chunk = self.model.predict(batch)[0].to("cpu").numpy().astype(np.float64)
        self.calls += 1
        actions = self.ensemble.update(chunk) if self.spec.temporal_ensemble else chunk[: self.spec.expose]
        done = time.perf_counter()
        self.last_timing = {"prepare_ms": (prepared - start) * 1e3, "forward_ms": (done - prepared) * 1e3,
                            "act_ms": (done - start) * 1e3}
        return ActionChunk(actions=np.asarray(actions, dtype=np.float64), hz=self.spec.action_hz)

    def done(self, observation: Observation) -> bool:
        """Always False: this model has no termination head either (see `DiffusionAdapter.done`)."""
        return False

    # -- observation -> tensors -------------------------------------------------------------------

    def _frame(self, observation: Observation) -> dict[str, Tensor]:
        """One observation as the tensors :class:`GoalACTPolicy` expects, on the device."""
        frame = {name: _image_tensor(getattr(observation, name), self.device) for name in CAMERAS}
        goal = torch.as_tensor(np.asarray(observation.goal), dtype=torch.float32, device=self.device)
        if goal.shape[-2:] != frame["top"].shape[-2:]:
            raise ValueError(f"goal {tuple(goal.shape)} does not match `top` {tuple(frame['top'].shape)}")
        frame["top"] = torch.cat([frame["top"], goal], dim=0)
        frame["state"] = torch.as_tensor(observation.state, dtype=torch.float32, device=self.device)
        frame["task_id"] = torch.as_tensor(observation.task_id, dtype=torch.float32, device=self.device)
        return frame


# --------------------------------------------------------------------------------------------------
# the latency benchmark (CLAUDE.md 5.8, D-019: the ACT rung of the fallback ladder)
# --------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """``python -m policy.act --bundle data/checkpoints/<run>/bundle --trials 20``.

    Deliberately the same measurement as ``python -m policy.diffusion``: the same synthetic
    observation at the configured frame sizes, the same :func:`policy.diffusion.benchmark`, the same
    trial count. D-019 compares the two numbers, so they have to be the same measurement.
    """
    parser = argparse.ArgumentParser(description="Measure ACTAdapter.act() latency (CLAUDE.md 5.8, D-019).")
    parser.add_argument("--bundle", required=True, help="inference bundle directory (policy/export.py)")
    parser.add_argument("--trials", type=int, default=20, help="calls to time (default 20)")
    parser.add_argument("--device", default="cpu", help="cpu (default) or cuda")
    parser.add_argument("--no-ensemble", action="store_true", help="skip the temporal ensemble (it is not the cost)")
    args = parser.parse_args(argv)

    from policy.diffusion import _synthetic_observation  # the identical inputs, for the identical number

    adapter = ACTAdapter(args.bundle, device=args.device,
                         temporal_ensemble=False if args.no_ensemble else None)
    sizes = config.load("training")["observation"]["images"]
    observation = _synthetic_observation(sizes, adapter.spec.task_dim)
    result = benchmark(adapter, observation, trials=args.trials)
    budget_ms = 1e3 / float(config.load("training")["rates"]["policy_hz"])
    print(f"{adapter!r}")
    print(f"  parameters: {sum(p.numel() for p in adapter.model.parameters()) / 1e6:.1f}M")
    frames = ", ".join(f"{name}={list(hw)}" for name, hw in sizes.items())
    print(f"frames: {frames} -> encoder {adapter.spec.image_hw}")
    print(f"act(): mean {result['act_ms']:.0f} ms (prepare {result['prepare_ms']:.0f} ms, "
          f"forward {result['forward_ms']:.0f} ms), median {result['median_ms']:.0f} ms, "
          f"p95 {result['p95_ms']:.0f} ms over {args.trials} calls")
    verdict = "within" if result["act_ms"] <= budget_ms else f"OVER by {result['act_ms'] / budget_ms:.1f}x"
    print(f"budget at {int(1e3 / budget_ms)} Hz: {budget_ms:.0f} ms -> {verdict}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
