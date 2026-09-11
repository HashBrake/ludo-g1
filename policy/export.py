"""Turn a training checkpoint into an inference bundle (CLAUDE.md 5.8 ``compute.export_format``).

```bash
.venv/bin/python -m policy.export --checkpoint data/checkpoints/<run>
# -> data/checkpoints/<run>/bundle/{bundle.json,weights.pt}
.venv/bin/python -m eval.run_eval --policy bundle data/checkpoints/<run>/bundle --backend mock
```

A bundle is what :class:`policy.diffusion.DiffusionAdapter` loads and what an eval result names, so it
has to stand on its own: the architecture (:class:`~policy.diffusion.PolicySpec`), the weights, the
normalisation statistics, and the provenance of the checkpoint it came from.

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

from policy.diffusion import BUNDLE_FILE, WEIGHTS_FILE, DiffusionAdapter, GoalDiffusionPolicy, PolicySpec
from policy.train import git_commit
from runtime.policy_api import GOAL_CHANNELS

__all__ = ["BUNDLE_FORMAT", "TORCHSCRIPT_FILE", "TRACE_TOLERANCE", "export", "main"]

#: Version tag in ``bundle.json``; a loader that does not know the tag refuses the bundle.
BUNDLE_FORMAT = "ludo-g1/diffusion-bundle/1"
TORCHSCRIPT_FILE = "model.ts"
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


def _example_inputs(spec: PolicySpec) -> tuple[Tensor, ...]:
    """One batch of one at the encoder's own shapes: the smallest thing the trace can run."""
    height, width = spec.image_hw
    return (
        torch.rand(1, 3 + spec.goal_channels, height, width),
        torch.rand(1, 3, height, width),
        torch.rand(1, 3, height, width),
        torch.zeros(1, spec.state_dim),
        torch.eye(spec.task_dim)[:1],
        torch.randn(1, spec.chunk, spec.action_dim, generator=torch.Generator().manual_seed(0)),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def export(checkpoint: Path | str, out_dir: Path | str | None = None, *, trace: bool = True) -> dict:
    """Write the bundle for ``checkpoint`` (a run directory or a ``checkpoint.pt``) and return its manifest."""
    path = Path(checkpoint)
    file = path / "checkpoint.pt" if path.is_dir() else path
    if not file.is_file():
        raise FileNotFoundError(f"{file} is not a training checkpoint (policy/train.py writes one)")
    payload = torch.load(file, map_location="cpu", weights_only=True)
    spec = PolicySpec.from_dict(payload["spec"])
    if spec.goal_channels != GOAL_CHANNELS:
        raise ValueError(f"checkpoint declares {spec.goal_channels} goal channels, the observation has {GOAL_CHANNELS}")
    model = GoalDiffusionPolicy(spec)
    model.load_state_dict(payload["state_dict"])
    model.eval()

    directory = Path(file.parent / "bundle" if out_dir is None else out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    torchscript: str | None = None
    trace_error: str | None = None
    trace_max_diff: float | None = None
    if trace:
        example = _example_inputs(spec)
        try:
            traced = torch.jit.trace(_TraceWrapper(model), example, check_trace=True)
            with torch.no_grad():
                trace_max_diff = float((traced(*example) - _TraceWrapper(model)(*example)).abs().max())
            if trace_max_diff > TRACE_TOLERANCE:
                raise RuntimeError(f"traced graph differs from the eager model by {trace_max_diff:.2e}")
            traced.save(str(directory / TORCHSCRIPT_FILE))
            torchscript = TORCHSCRIPT_FILE
        except Exception as exc:  # noqa: BLE001 - any tracing failure is a fact to record, not a crash
            trace_error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:400]}"

    torch.save({"state_dict": model.state_dict()}, directory / WEIGHTS_FILE)
    manifest = {
        "format": BUNDLE_FORMAT,
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
    args = parser.parse_args(argv)

    manifest = export(args.checkpoint, args.out, trace=not args.no_trace)
    directory = Path(args.out) if args.out else Path(manifest["checkpoint"]).parent / "bundle"
    adapter = DiffusionAdapter(directory)
    print(f"bundle {directory}")
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
