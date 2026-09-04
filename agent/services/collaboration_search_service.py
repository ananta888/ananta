"""Permission-aware deterministic search, citation and room-memory projections."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from agent.services.collaboration_budget_service import CollaborationBudgetService
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import canonical_digest, canonical_json, require_id


class CollaborationSearchService:
    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        policy: CollaborationWorkspacePolicy,
        clock: Callable[[], float] = time.time,
        budget: CollaborationBudgetService | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._clock = clock
        self._budget = budget

    def rebuild(self, tenant_id: str, workspace_id: str, *, mode: str = "full") -> dict[str, Any]:
        if mode not in {"full", "incremental"}:
            raise ValueError("collaboration_search_rebuild_mode_invalid")
        events = self._store.projection_events(tenant_id, workspace_id)
        documents = self._documents(events)
        digest = canonical_digest(documents)
        checkpoint = int(events[-1]["sequence"]) if events else 0
        manifest = self._store.replace_search_documents(
            tenant_id,
            workspace_id,
            documents,
            checkpoint=checkpoint,
            index_digest=digest,
        )
        return {**manifest, "mode": mode}

    def rebuild_for_actor(
        self,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        *,
        mode: str = "full",
    ) -> dict[str, Any]:
        self._policy.require(self._store.membership(tenant_id, workspace_id, principal_actor_id), "workspace.manage")
        self._admit_budget(
            tenant_id,
            workspace_id,
            principal_actor_id,
            "search_rebuild",
            connection="hub-search-rebuild",
        )
        return self.rebuild(tenant_id, workspace_id, mode=mode)

    def drift(self, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        persisted = self._store.search_manifest(tenant_id, workspace_id)
        events = self._store.projection_events(tenant_id, workspace_id)
        expected_documents = self._documents(events)
        expected_digest = canonical_digest(expected_documents)
        checkpoint = int(events[-1]["sequence"]) if events else 0
        reasons: list[str] = []
        if persisted is None:
            reasons.append("search_manifest_missing")
        else:
            if persisted["index_digest"] != expected_digest:
                reasons.append("search_document_drift")
            if persisted["checkpoint"] != checkpoint:
                reasons.append("search_projection_lag")
            if persisted["document_count"] != len(expected_documents):
                reasons.append("search_document_count_drift")
        return {
            "ok": not reasons,
            "reason_codes": reasons,
            "checkpoint": checkpoint,
            "expected_digest": expected_digest,
        }

    def query(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        query: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id)
        self._admit_budget(tenant_id, workspace_id, principal_actor_id, "search_query")
        candidates = self._store.search_documents(tenant_id, workspace_id, query, limit=50)
        items: list[dict[str, Any]] = []
        for document in candidates:
            room_id = document.get("room_id")
            if room_id is not None:
                try:
                    visible = self._store.room_visible(tenant_id, workspace_id, room_id, principal_actor_id)
                except KeyError:
                    visible = False
                if not visible:
                    continue
            items.append({key: value for key, value in document.items() if key != "search_text"})
        return {"items": items[:limit], "query": str(query).strip().casefold(), "limit": limit}

    def room_memory(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        principal_actor_id: str,
        maximum_events: int = 20,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id)
        self._admit_budget(
            tenant_id,
            workspace_id,
            principal_actor_id,
            "search_memory",
            room_id=room_id,
        )
        if not 1 <= maximum_events <= 100 or not self._store.room_visible(
            tenant_id, workspace_id, room_id, principal_actor_id
        ):
            raise PermissionError("collaboration_room_visibility_denied")
        events = self._store.query_events(
            tenant_id,
            workspace_id,
            actor_binding_id=principal_actor_id,
            filters={"room_id": room_id},
            limit=maximum_events,
        )["items"]
        entries = [
            {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "sequence": event["sequence"],
                "summary": self._safe_summary(event.get("payload") or {}),
                "source_refs": event.get("source_refs") or [],
                "run_refs": event.get("run_refs") or [],
            }
            for event in events
            if event["event_type"] != "thread.tombstoned"
        ]
        return {
            "schema": "ananta.collaboration-room-memory.v1",
            "workspace_id": workspace_id,
            "room_id": room_id,
            "actor_binding_id": principal_actor_id,
            "entries": entries,
            "source_revision": max((item["sequence"] for item in entries), default=0),
            "memory_digest": canonical_digest(entries),
            "human_intervention_required": False,
        }

    def context_bundle(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        principal_actor_id: str,
        task_id: str,
        policy: Mapping[str, Any],
        ttl_seconds: float = 300.0,
    ) -> dict[str, Any]:
        memory = self.room_memory(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            room_id=room_id,
            principal_actor_id=principal_actor_id,
        )
        references = sorted(
            {reference for entry in memory["entries"] for reference in (*entry["source_refs"], *entry["run_refs"])}
        )
        return {
            "schema": "ananta.collaboration-context-bundle.v1",
            "workspace_id": workspace_id,
            "room_id": room_id,
            "actor_binding_id": principal_actor_id,
            "task_id": require_id(task_id, "task_id"),
            "source_revision": memory["source_revision"],
            "policy_digest": canonical_digest(policy),
            "expires_at": self._clock() + ttl_seconds,
            "allowed_references": references,
            "memory_digest": memory["memory_digest"],
        }

    def code_context(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        query: str,
        allowed_source_ids: set[str],
        room_source_ids: set[str] | None = None,
        task_source_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """Reduce an authoritative source scope for room/task context; never broaden it."""

        effective = set(allowed_source_ids)
        for narrowed in (room_source_ids, task_source_ids):
            if narrowed is not None:
                effective.intersection_update(narrowed)
        result = self.query(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_actor_id=principal_actor_id,
            query=query,
            limit=50,
        )
        items = [item for item in result["items"] if item.get("codecompass", {}).get("source_id") in effective]
        return {
            "items": items,
            "effective_source_ids": sorted(effective),
            "scope_broadened": False,
            "coverage_notice": "codecompass_partial_or_unavailable"
            if any(item.get("codecompass", {}).get("completeness") != "complete" for item in items)
            else None,
        }

    def _authorize(self, tenant_id: str, workspace_id: str, actor_id: str) -> None:
        self._policy.require(self._store.membership(tenant_id, workspace_id, actor_id), "event.read")

    def _admit_budget(
        self,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        traffic_class: str,
        *,
        room_id: str | None = None,
        task_id: str | None = None,
        intent_chain: str | None = None,
        connection: str = "hub-search",
    ) -> None:
        if self._budget is None:
            return
        admission = self._budget.admit(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            traffic_class=traffic_class,
            dimensions={
                "room": room_id,
                "principal": actor_id,
                "actor": actor_id,
                "task": task_id,
                "provider": None,
                "intent_chain": intent_chain,
                "connection": connection,
            },
        )
        if not admission["allowed"]:
            raise PermissionError(admission["reason_code"])

    @staticmethod
    def _documents(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tombstoned_threads = {event["thread_id"] for event in events if event["event_type"] == "thread.tombstoned"}
        redacted = {
            event.get("payload", {}).get("target_event_id")
            for event in events
            if event["event_type"] == "event.redacted"
        }
        documents = []
        for event in events:
            if (
                event["event_type"] in {"thread.tombstoned", "event.redacted"}
                or event["event_id"] in redacted
                or event["event_id"] in tombstoned_threads
                or event.get("thread_id") in tombstoned_threads
            ):
                continue
            documents.append(
                {
                    "event_id": event["event_id"],
                    "workspace_sequence": event["sequence"],
                    "room_id": event.get("room_id"),
                    "thread_id": event.get("thread_id"),
                    "actor_binding_id": event["actor_binding_id"],
                    "event_type": event["event_type"],
                    "visibility": event["visibility"],
                    "retention": event["retention"],
                    "projection_version": 1,
                    "citation": {
                        "type": "workspace_event",
                        "identifier": event["event_id"],
                        "digest": event["payload_digest"],
                        "revision": event["sequence"],
                        "permission_status": "revalidate_on_query",
                        "verification_status": "hub_verified"
                        if event.get("source_refs") and event.get("run_refs")
                        else "technical_observation",
                    },
                    "codecompass": CollaborationSearchService._codecompass(event.get("payload") or {}),
                    "search_text": canonical_json(event.get("payload") or {}).casefold(),
                }
            )
        return documents

    @staticmethod
    def _codecompass(payload: Mapping[str, Any]) -> dict[str, Any]:
        value = payload.get("codecompass")
        if not isinstance(value, Mapping):
            return {"coverage": "unknown", "notice": "codecompass_coverage_unavailable"}
        required = {
            "source_id",
            "source_digest",
            "symbol_id",
            "graph_ref",
            "graph_digest",
            "index_run_id",
            "completeness",
        }
        if set(value) != required:
            return {"coverage": "invalid", "notice": "codecompass_metadata_invalid"}
        digests = (str(value["source_digest"] or ""), str(value["graph_digest"] or ""))
        if any(
            len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest) for digest in digests
        ) or value["completeness"] not in {"complete", "partial", "unknown"}:
            return {"coverage": "invalid", "notice": "codecompass_metadata_invalid"}
        try:
            normalized = {
                "source_id": require_id(value["source_id"], "codecompass_source_id"),
                "source_digest": digests[0],
                "symbol_id": require_id(value["symbol_id"], "codecompass_symbol_id"),
                "graph_ref": require_id(value["graph_ref"], "codecompass_graph_ref"),
                "graph_digest": digests[1],
                "index_run_id": require_id(value["index_run_id"], "codecompass_index_run_id"),
                "completeness": str(value["completeness"]),
            }
        except ValueError:
            return {"coverage": "invalid", "notice": "codecompass_metadata_invalid"}
        if normalized["completeness"] != "complete":
            normalized["notice"] = "codecompass_coverage_partial"
        return normalized

    @staticmethod
    def _safe_summary(payload: Mapping[str, Any]) -> str:
        for key in ("text", "decision", "summary", "status"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:512]
        return "structured event"


__all__ = ["CollaborationSearchService"]
