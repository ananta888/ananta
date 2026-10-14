"""Portable safetensors checkpoint: model, Adam moments, RNG and immutable lineage."""

from __future__ import annotations

import hashlib
import json
import inspect

from ananta_contracts.adaptive_growth import GrowthModelConfig, estimate_parameters
from worker.training.growth.model import build_model
from worker.training.research.modeling import require_torch


def serialize(model, optimizer, *, steps: int, parent_digest: str | None = None) -> bytes:
    from safetensors.torch import save

    torch = require_torch()
    if parent_digest is not None and (
        len(parent_digest) != 64 or any(c not in "0123456789abcdef" for c in parent_digest)
    ):
        raise ValueError("growth_lineage_invalid")
    tensors = {"model/" + key: value.detach().cpu().contiguous().clone() for key, value in model.state_dict().items()}
    for name, parameter in model.named_parameters():
        for key, value in optimizer.state.get(parameter, {}).items():
            if not isinstance(value, torch.Tensor):
                raise ValueError("growth_optimizer_checkpoint_unsupported")
            tensors[f"optimizer/{name}/{key}"] = value.detach().cpu().contiguous().clone()
    tensors["rng/cpu"] = torch.get_rng_state()
    if next(model.parameters()).device.type == "cuda":
        for index, state in enumerate(torch.cuda.get_rng_state_all()):
            tensors[f"rng/cuda/{index}"] = state
    envelope = {
        "schema": "ananta.growth-checkpoint.v1",
        "config": model.config.to_dict(),
        "steps": steps,
        "parent_digest": parent_digest,
        "optimizer": {
            k: v
            for k, v in optimizer.param_groups[0].items()
            if k != "params" and k in inspect.signature(torch.optim.AdamW).parameters
        },
    }
    tensors["metadata"] = torch.tensor(
        list(json.dumps(envelope, sort_keys=True, allow_nan=False).encode()), dtype=torch.uint8
    )
    return save(tensors)


def restore(content: bytes, *, expected_digest: str, device: str = "cpu"):
    from safetensors.torch import load

    torch = require_torch()
    if len(content) > 256 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected_digest:
        raise ValueError("growth_checkpoint_digest_mismatch")
    tensors = load(content)
    metadata = json.loads(bytes(tensors.pop("metadata").tolist()))
    if (
        metadata["schema"] != "ananta.growth-checkpoint.v1"
        or type(metadata["steps"]) is not int
        or metadata["steps"] < 0
    ):
        raise ValueError("growth_checkpoint_metadata_invalid")
    config = GrowthModelConfig(**metadata["config"])
    if estimate_parameters(config) > 10_000_000 or device not in {"cpu", "cuda"}:
        raise ValueError("growth_checkpoint_resource_limit")
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("growth_checkpoint_cuda_unavailable")
    model = build_model(config).to(device)
    model.load_state_dict(
        {name.removeprefix("model/"): value for name, value in tensors.items() if name.startswith("model/")},
        strict=True,
    )
    optimizer = torch.optim.AdamW(model.parameters(), **metadata["optimizer"])
    for name, parameter in model.named_parameters():
        prefix = f"optimizer/{name}/"
        state = {key.removeprefix(prefix): value for key, value in tensors.items() if key.startswith(prefix)}
        if state:
            if (
                set(state) - {"step", "exp_avg", "exp_avg_sq", "max_exp_avg_sq"}
                or not {"step", "exp_avg", "exp_avg_sq"} <= set(state)
                or any(not torch.isfinite(value).all() for value in state.values())
            ):
                raise ValueError("growth_checkpoint_optimizer_invalid")
            if any(value.shape != parameter.shape for key, value in state.items() if key != "step"):
                raise ValueError("growth_checkpoint_optimizer_invalid")
            optimizer.state[parameter] = {
                key: value.to(device) if key != "step" else value for key, value in state.items()
            }
    torch.set_rng_state(tensors["rng/cpu"])
    if device == "cuda":
        rng_states = [value for key, value in sorted(tensors.items()) if key.startswith("rng/cuda/")]
        if len(rng_states) != torch.cuda.device_count():
            raise ValueError("growth_checkpoint_cuda_rng_binding_invalid")
        torch.cuda.set_rng_state_all(rng_states)
    return model, optimizer, metadata
