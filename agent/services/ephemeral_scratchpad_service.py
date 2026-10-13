"""Authorize bounded scratchpad accesses against current Hub run policy."""

from __future__ import annotations

import time
from typing import Callable, Protocol

from agent.services.memory_policy_gateway import MemoryAccessError, MemoryPolicyDecision
from ananta_contracts.memory_access import (
    MemoryActor,
    MemoryNamespace,
    MemoryOperation,
    MemoryRetention,
    MemoryScope,
    MemorySecurityMetadata,
    require_memory_timestamp,
)

SCRATCHPAD_CHANNEL = "run-scratchpad"
SCRATCHPAD_SECURITY = MemorySecurityMetadata(scope=MemoryScope.run, retention_class=MemoryRetention.ephemeral)


class ScratchpadStorePort(Protocol):
    def read(self, namespace: tuple[str, ...], key: str, *, now: float) -> str | None: ...
    def append(self, namespace, key, value, *, expires_at, now) -> None: ...
    def update(self, namespace, key, value, *, expires_at, now) -> None: ...
    def delete(self, namespace, key, *, now) -> bool: ...
    def discard(self, namespace: tuple[str, ...]) -> int: ...
    def reap(self, *, now: float) -> int: ...


class ScratchpadAuthorizationPort(Protocol):
    def authorize_run(self, *, actor: MemoryActor, run_id: str, operation: MemoryOperation,
                      channel_id: str, security: MemorySecurityMetadata) -> MemoryPolicyDecision: ...


class EphemeralScratchpadService:
    """No caller-supplied namespaces, persistence or automatic promotion."""

    def __init__(self, *, gateway: ScratchpadAuthorizationPort, store: ScratchpadStorePort,
                 ttl_seconds: float = 300, clock: Callable[[], float] = time.time):
        require_memory_timestamp(ttl_seconds, "scratchpad_ttl")
        if ttl_seconds > 3600:
            raise ValueError("scratchpad_ttl_invalid")
        self._gateway = gateway
        self._store = store
        self._ttl = ttl_seconds
        self._clock = clock

    def _authorize(self, actor, run_id, operation):
        decision = self._gateway.authorize_run(
            actor=actor, run_id=run_id, operation=operation,
            channel_id=SCRATCHPAD_CHANNEL, security=SCRATCHPAD_SECURITY,
        )
        now = self._clock()
        require_memory_timestamp(now, "scratchpad_clock")
        if now >= decision.expires_at:
            raise ValueError("scratchpad_run_expired")
        return decision.target.namespace.key(MemoryScope.run), now, min(now + self._ttl, decision.expires_at)

    def read(self, *, actor: MemoryActor, run_id: str, key: str) -> str | None:
        namespace, now, _expiry = self._authorize(actor, run_id, MemoryOperation.read)
        value = self._store.read(namespace, key, now=now)
        self._recheck(actor, run_id, MemoryOperation.read, namespace)
        return value

    def append(self, *, actor: MemoryActor, run_id: str, key: str, value: str) -> None:
        namespace, now, expiry = self._authorize(actor, run_id, MemoryOperation.append)
        self._store.append(namespace, key, value, expires_at=expiry, now=now)
        self._recheck(actor, run_id, MemoryOperation.append, namespace)

    def update(self, *, actor: MemoryActor, run_id: str, key: str, value: str) -> None:
        namespace, now, expiry = self._authorize(actor, run_id, MemoryOperation.update)
        self._store.update(namespace, key, value, expires_at=expiry, now=now)
        self._recheck(actor, run_id, MemoryOperation.update, namespace)

    def _recheck(self, actor, run_id, operation, namespace):
        """Deny concurrent termination before returning content or retaining a late write."""
        try:
            current_namespace, _now, _expiry = self._authorize(actor, run_id, operation)
            if current_namespace != namespace:
                raise MemoryAccessError("memory_run_binding_mismatch")
        except Exception:
            self._store.discard(namespace)
            raise

    def delete(self, *, actor: MemoryActor, run_id: str, key: str) -> bool:
        namespace, now, _expiry = self._authorize(actor, run_id, MemoryOperation.delete)
        return self._store.delete(namespace, key, now=now)

    def discard_run(self, namespace: MemoryNamespace) -> int:
        """Trusted Hub lifecycle hook for success, failure, timeout and cancellation."""
        return self._store.discard(namespace.key(MemoryScope.run))

    def reap_expired(self) -> int:
        """Bounded orphan cleanup; a process crash cannot retain this RAM-only store."""
        return self._store.reap(now=self._clock())
