"""Crash-safe delivery and deterministic projection application services."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore


@dataclass(frozen=True, slots=True)
class CollaborationDeliveryPolicy:
    max_attempts: int = 5
    base_backoff_seconds: float = 1.0
    maximum_backoff_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.base_backoff_seconds <= 0 or self.maximum_backoff_seconds <= 0:
            raise ValueError("collaboration_delivery_policy_invalid")

    def backoff_seconds(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("collaboration_delivery_attempt_invalid")
        return min(self.maximum_backoff_seconds, self.base_backoff_seconds * (2 ** (attempt - 1)))


class CollaborationDeliveryService:
    """Owns delivery retry policy while the store owns atomic state transitions."""

    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        policy: CollaborationDeliveryPolicy | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._policy = policy or CollaborationDeliveryPolicy()
        self._clock = clock

    def claim(
        self,
        tenant_id: str,
        *,
        consumer_id: str,
        lease_seconds: float = 30.0,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self._store.claim_outbox(
            tenant_id,
            consumer_id=consumer_id,
            now=self._clock(),
            lease_seconds=lease_seconds,
            limit=limit,
        )

    def complete(self, tenant_id: str, event_id: str, attempt_id: str) -> dict[str, Any]:
        return self._store.complete_outbox(
            tenant_id,
            event_id,
            attempt_id,
            completed_at=self._clock(),
        )

    def fail(
        self,
        tenant_id: str,
        event_id: str,
        attempt_id: str,
        *,
        attempt: int,
        error_code: str,
    ) -> dict[str, Any]:
        terminal = attempt >= self._policy.max_attempts
        now = self._clock()
        return self._store.fail_outbox(
            tenant_id,
            event_id,
            attempt_id,
            error_code=error_code,
            next_attempt_at=now if terminal else now + self._policy.backoff_seconds(attempt),
            terminal=terminal,
        )

    def admit_external(
        self,
        tenant_id: str,
        *,
        origin: str,
        adapter_id: str,
        external_event_id: str,
        mapping_version: str,
        payload_digest: str,
    ) -> dict[str, Any]:
        value, replayed = self._store.admit_inbox(
            tenant_id,
            origin=origin,
            adapter_id=adapter_id,
            external_event_id=external_event_id,
            mapping_version=mapping_version,
            payload_digest=payload_digest,
            admitted_at=self._clock(),
        )
        return {**value, "replayed": replayed}


class CollaborationProjectionService:
    """Rebuilds projections and detects persisted checkpoint drift."""

    PROJECTIONS = ("timeline", "search", "threads")

    def __init__(self, store: CollaborationWorkspaceStore) -> None:
        self._store = store

    def rebuild_all(self, tenant_id: str, workspace_id: str) -> dict[str, dict[str, Any]]:
        return {name: self._store.rebuild_projection(tenant_id, workspace_id, name) for name in self.PROJECTIONS}

    def verify_all(self, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        results: dict[str, Any] = {}
        drifted: list[str] = []
        for name in self.PROJECTIONS:
            persisted = self._store.projection_checkpoint(tenant_id, workspace_id, name)
            rebuilt = self._store.rebuild_projection(tenant_id, workspace_id, name, persist=False)
            matches = bool(
                persisted
                and persisted["checkpoint"] == rebuilt["checkpoint"]
                and persisted["state_digest"] == rebuilt["state_digest"]
            )
            results[name] = {"matches": matches, "persisted": persisted, "rebuilt": rebuilt}
            if not matches:
                drifted.append(name)
        return {"ok": not drifted, "drifted": drifted, "projections": results}


__all__ = [
    "CollaborationDeliveryPolicy",
    "CollaborationDeliveryService",
    "CollaborationProjectionService",
]
