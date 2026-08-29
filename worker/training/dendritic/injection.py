"""Exact allowlisted target resolution with reversible model patching."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True)
class DendriticInjectionHandle:
    model: Any
    originals: Mapping[str, Any]
    base_digest: str

    def unpatch(self) -> None:
        for name, original in self.originals.items():
            _set_module(self.model, name, original)
        if _base_digest(self.model) != self.base_digest:
            raise RuntimeError("dendritic_base_model_weight_drift")


def inject_dendritic_modules(
    model: Any, *, targets: tuple[str, ...], modules: Mapping[str, Any], allowed_prefixes: tuple[str, ...]
) -> DendriticInjectionHandle:
    if set(targets) != set(modules) or any(not target.startswith(allowed_prefixes) for target in targets):
        raise PermissionError("dendritic_target_layer_denied")
    named = dict(model.named_modules())
    if any(target not in named for target in targets):
        raise ValueError("dendritic_target_layer_missing")
    base_digest = _base_digest(model)
    originals: dict[str, Any] = {}
    try:
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        for target in targets:
            original = named[target]
            wrapper = _wrapper(original, modules[target])
            originals[target] = original
            _set_module(model, target, wrapper)
    except Exception:
        for name, original in originals.items():
            _set_module(model, name, original)
        raise
    return DendriticInjectionHandle(model=model, originals=originals, base_digest=base_digest)


def _wrapper(original: Any, memory: Any):
    try:
        from torch import nn
    except ImportError as exc:
        raise RuntimeError("dendritic_torch_unavailable") from exc

    class ResidualWrapper(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.original = original
            self.memory = memory

        def forward(self, *args, **kwargs):
            output = self.original(*args, **kwargs)
            if isinstance(output, tuple):
                return (self.memory(output[0]), *output[1:])
            return self.memory(output)

    return ResidualWrapper()


def _set_module(model: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    parent = model
    for part in parts[:-1]:
        parent = parent[int(part)] if part.isdigit() else getattr(parent, part)
    final = parts[-1]
    if final.isdigit():
        parent[int(final)] = value
    else:
        setattr(parent, final, value)


def _base_digest(model: Any) -> str:
    digest = hashlib.sha256()
    for name, parameter in sorted(model.named_parameters()):
        if ".memory." in name:
            continue
        digest.update(name.encode())
        digest.update(parameter.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


__all__ = ["DendriticInjectionHandle", "inject_dendritic_modules"]
