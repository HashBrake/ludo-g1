"""Turn a training checkpoint into an inference bundle (CLAUDE.md 5.8 ``compute.export_format``).

```bash
.venv/bin/python -m policy.export --checkpoint data/checkpoints/<run>
# -> data/checkpoints/<run>/bundle/{bundle.json,weights.pt}
.venv/bin/python -m eval.run_eval --policy bundle data/checkpoints/<run>/bundle --backend mock
```

**The bundle carries the EMA weights by default** (T-035): ``policy/train.py`` keeps an exponential
moving average of the parameters beside the live ones and stores it as ``ema_state_dict``, and that
is what an evaluated policy should be. ``--raw`` exports the last step's parameters instead, and
``bundle.json`` says which through ``weights_source`` -- a success rate belongs to one of the two,
never to "the run".

A bundle is what an adapter loads and what an eval result names, so it has to stand on its own: the
architecture (:class:`~policy.diffusion.PolicySpec` or :class:`~policy.act.ACTSpec`), the weights,
the normalisation statistics, and the provenance of the checkpoint it came from. Both models of
CLAUDE.md 5.7 export the same way; ``bundle.json`` carries a ``policy`` field and a format tag, and
:func:`open_bundle` is the one place that turns a bundle back into a
:class:`runtime.policy_api.Policy` -- so ``eval/run_eval.py --policy bundle PATH`` takes either
without being told which.

**TorchScript is attempted, verified, and not relied on.** ``compute.export_format: torchscript`` is
the target, so :func:`export` tries ``torch.jit.trace`` on the inference path at fixed shapes. The
diffusion noise is an *input* to the traced function rather than something drawn inside it: otherwise
the trace contains an ``aten::randn`` whose output cannot be compared with the eager model's, and
``check_trace`` reports a mismatch that means nothing. With the noise pinned, the traced graph is
compared against the eager model directly and ``bundle.json`` records the largest difference.

Even when it traces, the graph is **not** what :class:`~policy.diffusion.DiffusionAdapter` runs. A
trace of a reverse-diffusion loop bakes in the batch size, the image size and the DDIM step count
(the scheduler's Python loop is unrolled, and its ``prev_timestep >= 0`` branches become constants),
so it is a fixed-shape artefact for the Orin NX / ONNX leg of 5.8 to start from, not a policy. The
adapter always loads the ``state_dict`` and the spec, which is why the bundle carries them whatever
tracing said. Revisit when the model is final: script or export the U-Net alone and keep the
scheduler loop in Python.

Nothing here touches hardware and nothing sends a command (R1, R2).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import torch
from torch import Tensor, nn

from policy.act import ACTAdapter, ACTSpec, GoalACTPolicy
from policy.diffusion import BUNDLE_FILE, WEIGHTS_FILE, DiffusionAdapter, GoalDiffusionPolicy, PolicySpec
from policy.train import git_commit
from runtime.policy_api import GOAL_CHANNELS, Policy

__all__ = ["BUNDLE_FORMAT", "BUNDLE_FORMATS", "EMA_WEIGHTS", "RAW_WEIGHTS", "TORCHSCRIPT_FILE", "TRACE_TOLERANCE",
           "WEIGHT_SOURCES", "export", "main", "open_bundle"]

#: Version tag in ``bundle.json``; a loader that does not know the tag refuses the bundle.
BUNDLE_FORMAT = "ludo-g1/diffusion-bundle/1"
#: One tag per policy of 5.7. A bundle written before the ACT baseline existed carries no ``policy``
#: field and is a diffusion bundle by definition, which is why "diffusion" is also the default below.
BUNDLE_FORMATS: dict[str, str] = {"diffusion": BUNDLE_FORMAT, "act": "ludo-g1/act-bundle/1"}
TORCHSCRIPT_FILE = "model.ts"
#: The two sets of weights a checkpoint holds (T-035): the EMA of the run, or the last step's raw
#: parameters. ``export`` defaults to the average; ``--raw`` asks for the other.
EMA_WEIGHTS, RAW_WEIGHTS = "ema", "raw"
WEIGHT_SOURCES: tuple[str, ...] = (EMA_WEIGHTS, RAW_WEIGHTS)
#: How far the traced graph may differ from the eager model on the example inputs before it is
#: discarded. Float32 accumulation over a 10-step DDIM loop, not a model difference.
TRACE_TOLERANCE = 1e-4


class _TraceWrapper(nn.Module):
    """The inference path as a plain tensor-in, tensor-out module, which is all ``trace`` accepts."""

    def __init__(self, model: GoalDiffusionPolicy) -> None:
        super().__init__()
        self.model = model

    def forward(
        self, top: Tensor, oblique: Tensor, palm: Tensor, state: Tensor, task_id: Tensor, noise: Tensor
    ) -> Tensor:
        batch = {"top": top, "oblique": oblique, "palm": palm, "state": state, "task_id": task_id}
        return self.model.predict(batch, noise=noise)


class _ACTTraceWrapper(nn.Module):
    """The ACT inference path, tensor in, tensor out. No noise input: ACT is deterministic in eval."""

    def __init__(self, model: GoalACTPolicy) -> None:
        super().__init__()
        self.model = model

    def forward(self, top: Tensor, oblique: Tensor, palm: Tensor, state: Tensor, task_id: Tensor) -> Tensor:
        batch = {"top": top, "oblique": oblique, "palm": palm, "state": state, "task_id": task_id}
        return self.model.predict(batch)


def _observation_inputs(spec: PolicySpec | ACTSpec) -> tuple[Tensor, ...]:
    """One batch of one at the encoder's own shapes: the smallest thing the trace can run."""
    height, width = spec.image_hw
    return (
        torch.rand(1, 3 + spec.goal_channels, height, width),
        torch.rand(1, 3, height, width),
        torch.rand(1, 3, height, width),
        torch.zeros(1, spec.state_dim),
        torch.eye(spec.task_dim)[:1],
    )


def _example_inputs(spec: PolicySpec) -> tuple[Tensor, ...]:
    """The diffusion trace's inputs: the observation plus the pinned noise (module docstring)."""
    return (
        *_observation_inputs(spec),
        torch.randn(1, spec.chunk, spec.action_dim, generator=torch.Generator().manual_seed(0)),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def open_bundle(bundle: Path | str, **kwargs) -> Policy:
    """Load the bundle at ``bundle`` as the adapter its ``policy`` field names (5.7).

    The one reader of the format: ``eval/run_eval.py --policy bundle PATH`` and every other caller
    takes whichever of the two models of 5.7 the bundle holds without being told which.
    """
    path = Path(bundle)
    manifest_path = path if path.is_file() else path / BUNDLE_FILE
    if not manifest_path.is_file():
        raise FileNotFoundError(f"{manifest_path} is not an inference bundle (policy/export.py writes one)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    kind = str(manifest.get("policy", "diffusion"))
    adapters: dict[str, type] = {"diffusion": DiffusionAdapter, "act": ACTAdapter}
    if kind not in adapters:
        raise ValueError(f"{manifest_path} names policy {kind!r}; this code knows {sorted(adapters)}")
    if manifest.get("format") != BUNDLE_FORMATS[kind]:
        raise ValueError(f"{manifest_path} has format {manifest.get('format')!r}, expected {BUNDLE_FORMATS[kind]!r}")
    return adapters[kind](manifest_path, **kwargs)


def export(checkpoint: Path | str, out_dir: Path | str | None = None, *, trace: bool = True,
           weights: str = EMA_WEIGHTS) -> dict:
    """Write the bundle for ``checkpoint`` (a run directory or a ``checkpoint.pt``) and return its manifest.

    ``weights`` chooses which set of weights the bundle carries: ``"ema"`` (the default) is the
    exponential moving average ``policy/train.py`` keeps beside the live parameters, ``"raw"`` the
    parameters the last optimiser step left. The average is the default because it is what a
    diffusion policy is evaluated with upstream and what the schedule of T-035 exists to produce; a
    checkpoint written before EMA existed has no ``ema_state_dict`` and falls back to the raw
    weights, which ``bundle.json`` records in ``weights_source``.
    """
    path = Path(checkpoint)
    file = path / "checkpoint.pt" if path.is_dir() else path
    if not file.is_file():
        raise FileNotFoundError(f"{file} is not a training checkpoint (policy/train.py writes one)")
    if weights not in WEIGHT_SOURCES:
        raise ValueError(f"weights must be one of {sorted(WEIGHT_SOURCES)}, got {weights!r}")
    payload = torch.load(file, map_location="cpu", weights_only=True)
    kind = str(payload.get("policy", "diffusion"))
    if kind not in BUNDLE_FORMATS:
        raise ValueError(f"{file} names policy {kind!r}; this code knows {sorted(BUNDLE_FORMATS)}")
    is_act = kind == "act"
    spec = (ACTSpec if is_act else PolicySpec).from_dict(payload["spec"])
    if spec.goal_channels != GOAL_CHANNELS:
        raise ValueError(f"checkpoint declares {spec.goal_channels} goal channels, the observation has {GOAL_CHANNELS}")
    source = weights if (weights == RAW_WEIGHTS or payload.get("ema_state_dict")) else RAW_WEIGHTS
    model = (GoalACTPolicy if is_act else GoalDiffusionPolicy)(spec)
    model.load_state_dict(payload["state_dict" if source == RAW_WEIGHTS else "ema_state_dict"])
    model.eval()

    directory = Path(file.parent / "bundle" if out_dir is None else out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    torchscript: str | None = None
    trace_error: str | None = None
    trace_max_diff: float | None = None
    if trace:
        wrapper = _ACTTraceWrapper if is_act else _TraceWrapper
        example = _observation_inputs(spec) if is_act else _example_inputs(spec)
        try:
            traced = torch.jit.trace(wrapper(model), example, check_trace=True)
            with torch.no_grad():
                trace_max_diff = float((traced(*example) - wrapper(model)(*example)).abs().max())
            if trace_max_diff > TRACE_TOLERANCE:
                raise RuntimeError(f"traced graph differs from the eager model by {trace_max_diff:.2e}")
            traced.save(str(directory / TORCHSCRIPT_FILE))
            torchscript = TORCHSCRIPT_FILE
        except Exception as exc:  # noqa: BLE001 - any tracing failure is a fact to record, not a crash
            trace_error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:400]}"

    torch.save({"state_dict": model.state_dict()}, directory / WEIGHTS_FILE)
    manifest = {
        "format": BUNDLE_FORMATS[kind],
        "policy": kind,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "spec": spec.to_dict(),
        "weights": WEIGHTS_FILE,
        "weights_sha256": _sha256(directory / WEIGHTS_FILE),
        "torchscript": torchscript,
        "torchscript_max_diff": trace_max_diff,
        # The adapter loads the state_dict, always: a traced diffusion loop is pinned to one batch
        # size, one image size and one DDIM step count (see the module docstring).
        "torchscript_used_at_inference": False,
        # Which set of weights this bundle carries (T-035): the EMA of the training run, or the raw
        # parameters of its last step. A success rate belongs to one of them, not to "the run".
        "weights_source": source,
        "trace_error": trace_error,
        "checkpoint": str(file),
        "checkpoint_sha256": _sha256(file),
        "train_run": {key: payload.get("run", {}).get(key) for key in
                      ("run", "config_hashes", "dataset_manifest_sha256", "loss_last", "args")},
    }
    (directory / BUNDLE_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    """``python -m policy.export --checkpoint data/checkpoints/<run>``."""
    parser = argparse.ArgumentParser(description="Checkpoint -> inference bundle (CLAUDE.md 5.8).")
    parser.add_argument("--checkpoint", required=True, help="run directory or checkpoint.pt")
    parser.add_argument("--out", default=None, help="bundle directory (default <run>/bundle)")
    parser.add_argument("--no-trace", action="store_true", help="skip the TorchScript attempt")
    parser.add_argument("--raw", action="store_true",
                        help="export the last step's weights instead of the training run's EMA of them")
    args = parser.parse_args(argv)

    manifest = export(args.checkpoint, args.out, trace=not args.no_trace,
                      weights=RAW_WEIGHTS if args.raw else EMA_WEIGHTS)
    directory = Path(args.out) if args.out else Path(manifest["checkpoint"]).parent / "bundle"
    adapter = open_bundle(directory)
    print(f"bundle {directory} ({manifest['policy']}, {manifest['weights_source']} weights)")
    print(f"  spec: {adapter!r}")
    if manifest["torchscript"]:
        print(f"  torchscript: {manifest['torchscript']} (traced, max diff vs eager "
              f"{manifest['torchscript_max_diff']:.2e}; fixed-shape artefact, not used at inference)")
    else:
        print(f"  torchscript: no, fell back to state_dict + spec -- {manifest['trace_error']}")
    print(f"  weights sha256 {manifest['weights_sha256']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
