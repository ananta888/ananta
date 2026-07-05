"""DD-015: feature flags grouped by import provider.

Each flag module exposes ``flags() -> dict[str, bool]`` and a
``is_enabled(name) -> bool`` helper. All defaults are ``off`` so that
native Ananta graph capabilities (blast radius, metrics, diff) work
without any external dependency.

Flags are *opt-in* via environment variables of the form
``CODECOMPASS_<GROUP>_<NAME>=1``.
"""
from __future__ import annotations

import os
from typing import Callable

from . import (
    codecompass_crg,
    codecompass_rig,
    codecompass_spade,
    codecompass_sqlite,
)


def _group_flags(module) -> dict[str, bool]:
    raw = module.flags()
    safety_on = getattr(module, "SAFETY_ON", frozenset())
    resolved: dict[str, bool] = {}
    for name, default in raw.items():
        env_key = f"CODECOMPASS_{module.GROUP.upper()}_{name.upper()}"
        env_value = os.environ.get(env_key)
        if name in safety_on:
            # Safety properties: env cannot disable. Env can only enable an
            # otherwise-off default, but cannot turn off an on-default.
            if env_value is None:
                resolved[name] = default
            else:
                # explicit "off" / "0" / "false" is ignored for safety flags
                truthy = env_value.strip().lower() in {"1", "true", "yes", "on"}
                resolved[name] = bool(default) or truthy
        else:
            resolved[name] = _env_to_bool(env_key, default)
    return resolved


def _env_to_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_GROUPS: list = [
    codecompass_crg,
    codecompass_rig,
    codecompass_spade,
    codecompass_sqlite,
]


def all_flags() -> dict[str, dict[str, bool]]:
    return {mod.GROUP: _group_flags(mod) for mod in _GROUPS}


def is_enabled(name: str) -> bool:
    """Resolve a fully qualified flag like ``crg.adapter_enabled``."""
    group, _, flag_name = name.partition(".")
    for mod in _GROUPS:
        if mod.GROUP == group:
            return _group_flags(mod).get(flag_name, False)
    return False


__all__ = ["all_flags", "is_enabled"]