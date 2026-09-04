"""Backup, verified restore, filtered export and content-safe diagnostics."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent.services.collaboration_delivery_service import CollaborationProjectionService
from agent.services.collaboration_search_service import CollaborationSearchService
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import canonical_digest


class CollaborationRecoveryService:
    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        policy: CollaborationWorkspacePolicy,
        projections: CollaborationProjectionService,
        search: CollaborationSearchService,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._policy = policy
        self._projections = projections
        self._search = search
        self._clock = clock

    def backup(self, destination: str | Path) -> dict[str, Any]:
        started = self._clock()
        manifest = self._store.backup_to(destination)
        return {**manifest, "duration_seconds": max(0.0, self._clock() - started)}

    def restore(
        self,
        source: str | Path,
        *,
        expected_digest: str,
        tenant_id: str,
        workspace_ids: list[str],
    ) -> dict[str, Any]:
        started = self._clock()
        restored = self._store.restore_from(source, expected_digest=expected_digest)
        rebuilt: dict[str, Any] = {}
        for workspace_id in workspace_ids:
            projections = self._projections.rebuild_all(tenant_id, workspace_id)
            search = self._search.rebuild(tenant_id, workspace_id)
            rebuilt[workspace_id] = {
                "projection_digests": {name: value["state_digest"] for name, value in projections.items()},
                "search_digest": search["index_digest"],
            }
        return {
            **restored,
            "rebuilt": rebuilt,
            "rto_seconds": max(0.0, self._clock() - started),
            "external_bridge_required": False,
        }

    def export_workspace(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
    ) -> dict[str, Any]:
        membership = self._store.membership(tenant_id, workspace_id, principal_actor_id)
        self._policy.require(membership, "event.read")
        workspace = self._store.get_workspace(tenant_id, workspace_id)
        workspace["rooms"] = [
            room
            for room in workspace["rooms"]
            if self._store.room_visible(tenant_id, workspace_id, room["room_id"], principal_actor_id)
        ]
        redacted = {
            (event.get("payload") or {}).get("target_event_id")
            for event in self._store.projection_events(tenant_id, workspace_id)
            if event["event_type"] == "event.redacted"
        }
        events = []
        for event in self._store.projection_events(tenant_id, workspace_id):
            if event["event_id"] in redacted or event["event_type"] == "event.redacted":
                continue
            room_id = event.get("room_id")
            if room_id is not None and not self._store.room_visible(
                tenant_id, workspace_id, room_id, principal_actor_id
            ):
                continue
            events.append(event)
        bundle = {
            "schema": "ananta.collaboration-workspace-export.v1",
            "workspace": workspace,
            "events": events,
            "checkpoint": int(events[-1]["sequence"]) if events else 0,
            "contains_key_material": False,
            "contains_secrets": False,
        }
        return {**bundle, "export_digest": canonical_digest(bundle)}


def content_safe_diagnostics(metrics: dict[str, int | float | str]) -> dict[str, Any]:
    allowed = {
        "latency_ms",
        "error_count",
        "projection_lag",
        "queue_depth",
        "replay_count",
        "revocation_latency_ms",
        "reconnect_count",
        "loop_rejection_count",
    }
    if set(metrics) - allowed:
        raise ValueError("collaboration_diagnostics_field_forbidden")
    return {
        "schema": "ananta.collaboration-diagnostics.v1",
        "metrics": dict(metrics),
        "contains_content": False,
        "cardinality_dimensions": ["deployment_profile", "adapter_kind", "reason_code"],
    }


__all__ = ["CollaborationRecoveryService", "content_safe_diagnostics"]
