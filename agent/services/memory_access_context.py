"""Scoped Hub composition binding. A binding alone never authorizes storage."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from agent.services.memory_policy_gateway import MemoryAccessError
from agent.services.result_memory_access import CurrentMemoryAuthorizationPort
from ananta_contracts.memory_access import MemoryActor


@dataclass(frozen=True)
class MemoryAccessContext:
    actor: MemoryActor
    run_id: str
    gateway: CurrentMemoryAuthorizationPort


_CURRENT: ContextVar[MemoryAccessContext | None] = ContextVar("hub_memory_access", default=None)


def current_memory_access() -> MemoryAccessContext:
    value = _CURRENT.get()
    if value is None:
        raise MemoryAccessError("memory_run_context_required")
    return value


@contextmanager
def bind_memory_access(context: MemoryAccessContext):
    if not isinstance(context, MemoryAccessContext):
        raise MemoryAccessError("memory_run_context_required")
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)
