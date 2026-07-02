"""ExpertRegistry singleton — COSMOS-001

Thin wrapper around ExpertRegistry for application-wide use.
"""
from __future__ import annotations

import threading
from pathlib import Path

from agent.services.expert_definition import ExpertDefinition, ExpertRegistry

_registry: ExpertRegistry | None = None
_lock = threading.Lock()


def get_expert_registry(config_dir: str | Path | None = None) -> ExpertRegistry:
    """Return the application-wide ExpertRegistry singleton.

    If *config_dir* is provided on the first call it sets the directory used for
    loading.  Subsequent calls with a *different* config_dir will create a new
    registry instance (useful for testing / overrides).
    """
    global _registry
    with _lock:
        if _registry is None or config_dir is not None:
            _registry = ExpertRegistry(config_dir=config_dir)
        return _registry
