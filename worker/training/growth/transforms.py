"""Function-preserving reference transforms and explicit Adam-state migration."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import inspect

from ananta_contracts.adaptive_growth import SUPPORTED_ARCHITECTURE
from worker.training.growth.model import build_model
from worker.training.research.modeling import require_torch


def _require_supported(model):
    if getattr(model, "architecture", None) != SUPPORTED_ARCHITECTURE:
        raise ValueError("growth_architecture_unsupported")


def _like(model, config):
    result = build_model(config)
    parameter = next(model.parameters())
    return result.to(device=parameter.device, dtype=parameter.dtype)


def grow_depth(model, *, layers: int = 1):
    _require_supported(model)
    if type(layers) is not int or not 1 <= layers <= 4:
        raise ValueError("growth_depth_invalid")
    result = _like(model, replace(model.config, depth=model.config.depth + layers))
    torch = require_torch()
    with torch.no_grad():
        result.load_state_dict(model.state_dict(), strict=False)
        for block in result.blocks[model.config.depth :]:
            block.projection.weight.zero_()
            block.projection.bias.zero_()
            for expert in block.experts:
                expert.down.weight.zero_()
                expert.down.bias.zero_()
    return result


def width_tensor(name, tensor, factor: int, *, moment: int = 1):
    """Tiled neuron replication. Second moments follow squared linear scaling."""
    if name.endswith("router.weight"):
        return tensor.repeat(1, factor) / factor**moment
    if tensor.ndim == 1:
        return tensor.repeat(factor)
    if name in {"embedding.weight", "position.weight"}:
        return tensor.repeat(1, factor)
    if name == "output.weight":
        return tensor.repeat(1, factor) / factor**moment
    if tensor.ndim == 2:
        return tensor.repeat(factor, factor) / factor**moment
    raise ValueError("growth_tensor_shape_unsupported")


def grow_width(model, *, factor: int = 2):
    _require_supported(model)
    if model.config.experts != 1 or type(factor) is not int or not 2 <= factor <= 4:
        raise ValueError("growth_width_unsupported")
    result = _like(model, replace(model.config, width=model.config.width * factor, heads=model.config.heads * factor))
    result.load_state_dict(
        {name: width_tensor(name, tensor, factor) for name, tensor in model.state_dict().items()}, strict=True
    )
    return result


def upcycle(model, *, experts: int = 4, top_k: int = 2):
    _require_supported(model)
    if model.config.experts != 1:
        raise ValueError("growth_upcycle_requires_dense")
    result = _like(model, replace(model.config, experts=experts, top_k=top_k))
    state = result.state_dict()
    source = model.state_dict()
    for name in state:
        if name.endswith("router.weight"):
            # Break top-k ties reproducibly without changing the function:
            # cloned experts agree, and selected routing weights still sum to 1.
            state[name].normal_(mean=0, std=0.001)
        elif ".experts." in name:
            prefix, suffix = name.split(".experts.")
            state[name] = source[prefix + ".experts.0." + suffix.split(".", 1)[1]].clone()
        else:
            state[name] = source[name].clone()
    result.load_state_dict(state, strict=True)
    return result


def prune_experts(model, *, keep: tuple[int, ...]):
    _require_supported(model)
    if (
        not keep
        or len(set(keep)) != len(keep)
        or any(type(i) is not int or not 0 <= i < model.config.experts for i in keep)
    ):
        raise ValueError("growth_prune_invalid")
    result = _like(model, replace(model.config, experts=len(keep), top_k=min(len(keep), model.config.top_k)))
    state, source = result.state_dict(), model.state_dict()
    for name in state:
        if name.endswith("router.weight"):
            state[name] = source[name][list(keep)].clone()
        elif ".experts." in name:
            prefix, suffix = name.split(".experts.")
            index, rest = suffix.split(".", 1)
            state[name] = source[prefix + f".experts.{keep[int(index)]}." + rest].clone()
        else:
            state[name] = source[name].clone()
    result.load_state_dict(state)
    return result


def migrate_adam(optimizer, old_model, new_model, *, operation: str, keep: tuple[int, ...] | None = None):
    """Migrate state explicitly; unsupported optimizers fail before any mutation.

    This preserves moments/steps, not an identical future optimization trajectory.
    New identity layers start with fresh moments; cloned experts inherit state.
    """
    torch = require_torch()
    if type(optimizer) is not torch.optim.AdamW or len(optimizer.param_groups) != 1:
        raise ValueError("growth_optimizer_unsupported")
    if operation not in {"depth", "width", "upcycle", "prune"}:
        raise ValueError("growth_optimizer_operation_unsupported")
    if operation == "prune" and (
        not keep or len(keep) != new_model.config.experts or any(not 0 <= i < old_model.config.experts for i in keep)
    ):
        raise ValueError("growth_optimizer_prune_invalid")
    group = optimizer.param_groups[0]
    accepted = inspect.signature(torch.optim.AdamW).parameters
    options = {key: value for key, value in group.items() if key != "params" and key in accepted}
    migrated = torch.optim.AdamW(new_model.parameters(), **options)
    old = dict(old_model.named_parameters())
    for name, parameter in new_model.named_parameters():
        source_name = name
        if operation == "upcycle" and ".experts." in name:
            prefix, suffix = name.split(".experts.")
            source_name = prefix + ".experts.0." + suffix.split(".", 1)[1]
        if operation == "prune" and ".experts." in name:
            prefix, suffix = name.split(".experts.")
            index, rest = suffix.split(".", 1)
            source_name = prefix + f".experts.{keep[int(index)]}." + rest
        source = old.get(source_name)
        if (
            source is None
            or source not in optimizer.state
            or (operation == "upcycle" and name.endswith("router.weight"))
        ):
            continue
        state = deepcopy(optimizer.state[source])
        if operation == "prune" and name.endswith("router.weight"):
            for key in ("exp_avg", "exp_avg_sq", "max_exp_avg_sq"):
                if key in state:
                    state[key] = state[key][list(keep)].clone()
        if operation == "width":
            factor = new_model.config.width // old_model.config.width
            for key in ("exp_avg", "exp_avg_sq", "max_exp_avg_sq"):
                if key in state:
                    state[key] = width_tensor(name, state[key], factor, moment=1 if key == "exp_avg" else 2)
        if any(value.shape != parameter.shape for key, value in state.items() if key != "step"):
            raise ValueError("growth_optimizer_state_shape_invalid")
        migrated.state[parameter] = state
    return migrated
