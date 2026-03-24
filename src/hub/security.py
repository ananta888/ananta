"""Simple capability helpers for the hub."""
from typing import Iterable


def has_capability(worker, capability: str) -> bool:
    """Return True if the given worker or object exposes the capability."""
    caps = getattr(worker, 'capabilities', None)
    if caps is None:
        return False
    return capability in (caps or [])


def authorize_user_capabilities(user_capabilities: Iterable[str], required) -> bool:
    """Return True if user_capabilities contains all required capabilities."""
    if isinstance(required, str):
        required = [required]
    user_caps = user_capabilities or []
    return all(r in user_caps for r in required)


def ensure_user_has(user_capabilities, required):
    if not authorize_user_capabilities(user_capabilities, required):
        raise PermissionError(f"Missing required capabilities: {required}")
