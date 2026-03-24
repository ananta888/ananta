"""Policy checks used by the hub to evaluate execution permissions."""

from typing import Iterable


def check_execution_allowed(worker, required_caps: Iterable[str]):
    """Raise PermissionError if the worker does not have all required capabilities."""
    if isinstance(required_caps, str):
        required_caps = [required_caps]
    caps = getattr(worker, 'capabilities', []) or []
    missing = [c for c in required_caps if c not in caps]
    if missing:
        raise PermissionError(f"Worker {getattr(worker, 'id', '<unknown>')} missing capabilities: {missing}")
    return True
