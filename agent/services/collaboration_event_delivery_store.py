"""Event, projection, delivery and live-presence persistence."""

# SQL statements retain complete column/key declarations on one line for auditability.
# ruff: noqa: E501

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

from agent.services.collaboration_workspace_store_contracts import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import canonical_digest, canonical_json, require_id


class CollaborationEventDeliveryStoreMixin:
    """Persist immutable events and their derived delivery projections."""

    def append_event(self, tenant_id: str, event: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(event.get("workspace_id"), "workspace_id")
        event_id = require_id(event.get("event_id"), "event_id")
        idempotency = require_id(event.get("idempotency_key"), "idempotency_key")
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            room_id = event.get("room_id")
            thread_id = event.get("thread_id")
            existing = connection.execute(
                "SELECT payload_json FROM collaboration_events WHERE tenant_id=? AND workspace_id=? AND idempotency_key=?",
                (tenant, workspace, idempotency),
            ).fetchone()
            if existing:
                value = json.loads(existing[0])
                if value["event_id"] != event_id or value["payload_digest"] != event["payload_digest"]:
                    raise CollaborationStoreConflict("collaboration_event_idempotency_conflict")
                return value, True
            self._consume_quota_connection(
                connection,
                tenant,
                workspace,
                require_id(event.get("actor_binding_id"), "actor_binding_id"),
                category="durable_event",
                now=time.time(),
                window_seconds=60,
                maximum=120,
            )
            if (
                room_id is not None
                and not connection.execute(
                    "SELECT 1 FROM collaboration_rooms WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                    (tenant, workspace, room_id),
                ).fetchone()
            ):
                raise KeyError("collaboration_room_not_found")
            if room_id is not None:
                lifecycle = connection.execute(
                    "SELECT state FROM collaboration_room_lifecycle WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                    (tenant, workspace, room_id),
                ).fetchone()
                if lifecycle is None or lifecycle[0] != "active":
                    raise CollaborationStoreConflict("collaboration_room_not_active")
            if event.get("event_type") in {
                "message.replied",
                "thread.resolved",
                "thread.reopened",
                "thread.tombstoned",
            }:
                if thread_id is None:
                    raise ValueError("collaboration_thread_root_required")
                root = connection.execute(
                    "SELECT room_id,event_type FROM collaboration_events "
                    "WHERE tenant_id=? AND workspace_id=? AND event_id=?",
                    (tenant, workspace, thread_id),
                ).fetchone()
                if root is None or root[1] != "message.posted" or root[0] != room_id:
                    raise ValueError("collaboration_thread_root_invalid")
                projection = self._thread_projection(connection, tenant, workspace, str(thread_id))
                expected_revision = (event.get("payload") or {}).get("expected_thread_revision")
                if (
                    not isinstance(expected_revision, int)
                    or isinstance(expected_revision, bool)
                    or expected_revision != projection["revision"]
                ):
                    raise CollaborationStoreConflict("collaboration_thread_revision_conflict")
                if projection["status"] == "tombstoned":
                    raise CollaborationStoreConflict("collaboration_thread_tombstoned")
                if event.get("event_type") == "thread.resolved" and projection["status"] != "open":
                    raise CollaborationStoreConflict("collaboration_thread_state_conflict")
                if event.get("event_type") == "thread.reopened" and projection["status"] != "resolved":
                    raise CollaborationStoreConflict("collaboration_thread_state_conflict")
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM collaboration_events WHERE tenant_id=? AND workspace_id=?",
                    (tenant, workspace),
                ).fetchone()[0]
            )
            value = {
                **dict(event),
                "tenant_id": tenant,
                "sequence": sequence,
                "admitted_at": time.time(),
            }
            connection.execute(
                "INSERT INTO collaboration_events(tenant_id,workspace_id,sequence,event_id,idempotency_key,"
                "room_id,thread_id,event_type,actor_binding_id,causation_id,visibility,occurred_at,admitted_at,"
                "payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tenant,
                    workspace,
                    sequence,
                    event_id,
                    idempotency,
                    room_id,
                    thread_id,
                    event["event_type"],
                    event["actor_binding_id"],
                    event.get("causation_id"),
                    event["visibility"],
                    event["occurred_at"],
                    value["admitted_at"],
                    canonical_json(value),
                ),
            )
            connection.execute(
                "INSERT INTO collaboration_outbox(tenant_id,event_id,topic,status,payload_json) VALUES(?,?,?,?,?)",
                (tenant, event_id, "collaboration.workspace-event.v1", "pending", canonical_json(value)),
            )
        return value, False

    def thread(
        self,
        tenant_id: str,
        workspace_id: str,
        *,
        actor_binding_id: str,
        thread_id: str,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        root_id = require_id(thread_id, "thread_id")
        actor = require_id(actor_binding_id, "actor_binding_id")
        with self._connect() as connection:
            projection = self._thread_projection(connection, tenant, workspace, root_id)
            room_id = projection["room_id"]
            if room_id is not None:
                access = connection.execute(
                    "SELECT access_mode FROM collaboration_room_access "
                    "WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                    (tenant, workspace, room_id),
                ).fetchone()
                visible = bool(access and access[0] == "workspace") or bool(
                    connection.execute(
                        "SELECT 1 FROM collaboration_room_memberships WHERE tenant_id=? AND workspace_id=? "
                        "AND room_id=? AND actor_binding_id=?",
                        (tenant, workspace, room_id, actor),
                    ).fetchone()
                )
                if not visible:
                    raise KeyError("collaboration_thread_not_found")
            rows = connection.execute(
                "SELECT payload_json FROM collaboration_events WHERE tenant_id=? AND workspace_id=? "
                "AND (event_id=? OR thread_id=?) ORDER BY sequence",
                (tenant, workspace, root_id, root_id),
            ).fetchall()
        return {**projection, "events": [json.loads(row[0]) for row in rows]}

    def timeline(
        self,
        tenant_id: str,
        workspace_id: str,
        *,
        actor_binding_id: str,
        room_id: str | None,
        after: int,
        limit: int,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 200 or after < 0:
            raise ValueError("collaboration_timeline_page_invalid")
        clauses = ["e.tenant_id=?", "e.workspace_id=?", "e.sequence>?"]
        params: list[Any] = [require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id"), after]
        if room_id is not None:
            clauses.append("e.room_id=?")
            params.append(require_id(room_id, "room_id"))
        clauses.append(
            "(e.room_id IS NULL OR COALESCE(a.access_mode,'workspace')='workspace' OR EXISTS("
            "SELECT 1 FROM collaboration_room_memberships rm WHERE rm.tenant_id=e.tenant_id "
            "AND rm.workspace_id=e.workspace_id AND rm.room_id=e.room_id AND rm.actor_binding_id=?))"
        )
        params.append(require_id(actor_binding_id, "actor_binding_id"))
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT e.payload_json FROM collaboration_events e LEFT JOIN collaboration_room_access a "
                "ON a.tenant_id=e.tenant_id AND a.workspace_id=e.workspace_id AND a.room_id=e.room_id "
                f"WHERE {' AND '.join(clauses)} ORDER BY e.sequence LIMIT ?",
                params,
            ).fetchall()
        items = [json.loads(row[0]) for row in rows]
        return {"items": items, "next_after": items[-1]["sequence"] if items else after, "limit": limit}

    def search(
        self, tenant_id: str, workspace_id: str, actor_binding_id: str, query: str, *, limit: int
    ) -> dict[str, Any]:
        normalized = str(query or "").strip().casefold()
        if not 2 <= len(normalized) <= 128 or not 1 <= limit <= 50:
            raise ValueError("collaboration_search_query_invalid")
        page = self.timeline(
            tenant_id,
            workspace_id,
            actor_binding_id=actor_binding_id,
            room_id=None,
            after=0,
            limit=200,
        )
        items = [
            event for event in page["items"] if normalized in canonical_json(event.get("payload") or {}).casefold()
        ][:limit]
        return {"items": items, "query": normalized, "partial": len(page["items"]) == 200, "limit": limit}

    def query_events(
        self,
        tenant_id: str,
        workspace_id: str,
        *,
        actor_binding_id: str,
        filters: Mapping[str, Any],
        limit: int = 100,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 200 or set(filters) - {
            "room_id",
            "thread_id",
            "actor_binding_id",
            "event_type",
            "occurred_after",
            "occurred_before",
            "causation_id",
        }:
            raise ValueError("collaboration_temporal_query_invalid")
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        clauses = ["e.tenant_id=?", "e.workspace_id=?"]
        params: list[Any] = [tenant, workspace]
        for field in ("room_id", "thread_id", "actor_binding_id", "event_type", "causation_id"):
            if filters.get(field) is not None:
                clauses.append(f"e.{field}=?")
                params.append(require_id(filters[field], field))
        for field, operator in (("occurred_after", ">="), ("occurred_before", "<=")):
            if filters.get(field) is not None:
                value = filters[field]
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError("collaboration_temporal_query_time_invalid")
                clauses.append(f"e.occurred_at{operator}?")
                params.append(float(value))
        clauses.append(
            "(e.room_id IS NULL OR COALESCE(a.access_mode,'workspace')='workspace' OR EXISTS("
            "SELECT 1 FROM collaboration_room_memberships rm WHERE rm.tenant_id=e.tenant_id "
            "AND rm.workspace_id=e.workspace_id AND rm.room_id=e.room_id AND rm.actor_binding_id=?))"
        )
        params.extend((require_id(actor_binding_id, "actor_binding_id"), limit))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT e.payload_json FROM collaboration_events e LEFT JOIN collaboration_room_access a "
                "ON a.tenant_id=e.tenant_id AND a.workspace_id=e.workspace_id AND a.room_id=e.room_id "
                f"WHERE {' AND '.join(clauses)} ORDER BY e.sequence LIMIT ?",
                params,
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "limit": limit, "filters": dict(filters)}

    def projection_events(self, tenant_id: str, workspace_id: str) -> list[dict[str, Any]]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        with self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            rows = connection.execute(
                "SELECT payload_json FROM collaboration_events WHERE tenant_id=? AND workspace_id=? ORDER BY sequence",
                (tenant, workspace),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def replace_search_documents(
        self,
        tenant_id: str,
        workspace_id: str,
        documents: list[Mapping[str, Any]],
        *,
        checkpoint: int,
        index_digest: str,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            connection.execute(
                "DELETE FROM collaboration_search_documents WHERE tenant_id=? AND workspace_id=?",
                (tenant, workspace),
            )
            for document in documents:
                connection.execute(
                    "INSERT INTO collaboration_search_documents(tenant_id,workspace_id,event_id,room_id,sequence,"
                    "document_text,payload_json) VALUES(?,?,?,?,?,?,?)",
                    (
                        tenant,
                        workspace,
                        document["event_id"],
                        document.get("room_id"),
                        document["workspace_sequence"],
                        document["search_text"],
                        canonical_json(dict(document)),
                    ),
                )
            manifest = {
                "tenant_id": tenant,
                "workspace_id": workspace,
                "checkpoint": checkpoint,
                "document_count": len(documents),
                "index_digest": index_digest,
                "projection_version": 1,
            }
            connection.execute(
                "INSERT INTO collaboration_search_manifests(tenant_id,workspace_id,checkpoint,index_digest,"
                "payload_json) VALUES(?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET "
                "checkpoint=excluded.checkpoint,index_digest=excluded.index_digest,payload_json=excluded.payload_json",
                (tenant, workspace, checkpoint, index_digest, canonical_json(manifest)),
            )
        return manifest

    def search_documents(self, tenant_id: str, workspace_id: str, query: str, *, limit: int) -> list[dict[str, Any]]:
        normalized = str(query or "").strip().casefold()
        if not 2 <= len(normalized) <= 128 or not 1 <= limit <= 50:
            raise ValueError("collaboration_search_query_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM collaboration_search_documents WHERE tenant_id=? AND workspace_id=? "
                "AND instr(document_text,?)>0 ORDER BY sequence DESC LIMIT ?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(workspace_id, "workspace_id"),
                    normalized,
                    limit,
                ),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def search_manifest(self, tenant_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collaboration_search_manifests WHERE tenant_id=? AND workspace_id=?",
                (require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id")),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def claim_outbox(
        self,
        tenant_id: str,
        *,
        consumer_id: str,
        now: float,
        lease_seconds: float,
        limit: int,
    ) -> list[dict[str, Any]]:
        tenant = require_id(tenant_id, "tenant_id")
        consumer = require_id(consumer_id, "consumer_id")
        if not 1 <= limit <= 100 or not 1.0 <= lease_seconds <= 300.0:
            raise ValueError("collaboration_outbox_claim_invalid")
        claimed: list[dict[str, Any]] = []
        with self._transaction, self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id,topic,payload_json,attempts FROM collaboration_outbox WHERE tenant_id=? "
                "AND ((status IN ('pending','retry') AND next_attempt_at<=?) "
                "OR (status='delivering' AND lease_expires_at<=?)) ORDER BY event_id LIMIT ?",
                (tenant, float(now), float(now), limit),
            ).fetchall()
            for event_id, topic, payload_json, attempts in rows:
                attempt_id = f"attempt-{uuid.uuid4()}"
                next_attempt = int(attempts or 0) + 1
                connection.execute(
                    "UPDATE collaboration_outbox SET status='delivering',attempt_id=?,attempts=?,leased_by=?,"
                    "lease_expires_at=?,last_error_code=NULL WHERE tenant_id=? AND event_id=?",
                    (attempt_id, next_attempt, consumer, float(now) + lease_seconds, tenant, event_id),
                )
                claimed.append(
                    {
                        "tenant_id": tenant,
                        "event_id": event_id,
                        "topic": topic,
                        "payload": json.loads(payload_json),
                        "attempt_id": attempt_id,
                        "attempt": next_attempt,
                        "lease_expires_at": float(now) + lease_seconds,
                    }
                )
        return claimed

    def complete_outbox(
        self,
        tenant_id: str,
        event_id: str,
        attempt_id: str,
        *,
        completed_at: float,
    ) -> dict[str, Any]:
        return self._transition_outbox(
            tenant_id,
            event_id,
            attempt_id,
            status="delivered",
            next_attempt_at=float(completed_at),
            error_code=None,
            completed_at=float(completed_at),
        )

    def fail_outbox(
        self,
        tenant_id: str,
        event_id: str,
        attempt_id: str,
        *,
        error_code: str,
        next_attempt_at: float,
        terminal: bool,
    ) -> dict[str, Any]:
        return self._transition_outbox(
            tenant_id,
            event_id,
            attempt_id,
            status="failed" if terminal else "retry",
            next_attempt_at=float(next_attempt_at),
            error_code=require_id(error_code, "outbox_error_code"),
            completed_at=float(next_attempt_at) if terminal else None,
        )

    def admit_inbox(
        self,
        tenant_id: str,
        *,
        origin: str,
        adapter_id: str,
        external_event_id: str,
        mapping_version: str,
        payload_digest: str,
        admitted_at: float,
    ) -> tuple[dict[str, Any], bool]:
        values = {
            "tenant_id": require_id(tenant_id, "tenant_id"),
            "origin": require_id(origin, "inbox_origin"),
            "adapter_id": require_id(adapter_id, "adapter_id"),
            "external_event_id": require_id(external_event_id, "external_event_id"),
            "mapping_version": require_id(mapping_version, "mapping_version"),
            "payload_digest": str(payload_digest or "").strip().lower(),
            "admitted_at": float(admitted_at),
        }
        if len(values["payload_digest"]) != 64 or any(
            character not in "0123456789abcdef" for character in values["payload_digest"]
        ):
            raise ValueError("collaboration_inbox_payload_digest_invalid")
        keys = (values["tenant_id"], values["adapter_id"], values["external_event_id"])
        with self._transaction, self._connect() as connection:
            current = connection.execute(
                "SELECT origin,mapping_version,payload_digest,admitted_at FROM collaboration_inbox "
                "WHERE tenant_id=? AND adapter_id=? AND external_event_id=?",
                keys,
            ).fetchone()
            if current:
                if tuple(current[:3]) != (
                    values["origin"],
                    values["mapping_version"],
                    values["payload_digest"],
                ):
                    raise CollaborationStoreConflict("collaboration_inbox_replay_conflict")
                return {**values, "admitted_at": float(current[3])}, True
            connection.execute(
                "INSERT INTO collaboration_inbox(tenant_id,adapter_id,external_event_id,payload_digest,"
                "origin,mapping_version,admitted_at) VALUES(?,?,?,?,?,?,?)",
                (
                    values["tenant_id"],
                    values["adapter_id"],
                    values["external_event_id"],
                    values["payload_digest"],
                    values["origin"],
                    values["mapping_version"],
                    values["admitted_at"],
                ),
            )
        return values, False

    def rebuild_projection(
        self, tenant_id: str, workspace_id: str, projection_name: str, *, persist: bool = True
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        projection = require_id(projection_name, "projection_name")
        if projection not in {"timeline", "search", "threads"}:
            raise ValueError("collaboration_projection_unknown")
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            rows = connection.execute(
                "SELECT payload_json FROM collaboration_events WHERE tenant_id=? AND workspace_id=? ORDER BY sequence",
                (tenant, workspace),
            ).fetchall()
            events = [json.loads(row[0]) for row in rows]
            state = self._project(projection, events)
            checkpoint = int(events[-1]["sequence"]) if events else 0
            result = {
                "tenant_id": tenant,
                "workspace_id": workspace,
                "projection_name": projection,
                "checkpoint": checkpoint,
                "event_count": len(events),
                "state_digest": canonical_digest(state),
                "state": state,
            }
            if persist:
                connection.execute(
                    "INSERT INTO collaboration_projection_checkpoints(tenant_id,workspace_id,projection_name,"
                    "checkpoint,state_digest,payload_json) VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id,"
                    "projection_name) DO UPDATE SET checkpoint=excluded.checkpoint,state_digest=excluded.state_digest,"
                    "payload_json=excluded.payload_json",
                    (tenant, workspace, projection, checkpoint, result["state_digest"], canonical_json(result)),
                )
        return result
    def projection_checkpoint(self, tenant_id: str, workspace_id: str, projection_name: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collaboration_projection_checkpoints "
                "WHERE tenant_id=? AND workspace_id=? AND projection_name=?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(workspace_id, "workspace_id"),
                    require_id(projection_name, "projection_name"),
                ),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def acknowledge(
        self, tenant_id: str, workspace_id: str, room_id: str, actor_binding_id: str, sequence: int
    ) -> dict[str, Any]:
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError("collaboration_cursor_sequence_invalid")
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(room_id, "room_id"),
            require_id(actor_binding_id, "actor_binding_id"),
        )
        with self._transaction, self._connect() as connection:
            current = connection.execute(
                "SELECT sequence FROM collaboration_cursors WHERE tenant_id=? AND workspace_id=? AND room_id=? AND actor_binding_id=?",
                keys,
            ).fetchone()
            if current and sequence < int(current[0]):
                raise CollaborationStoreConflict("collaboration_cursor_regression")
            connection.execute(
                "INSERT INTO collaboration_cursors(tenant_id,workspace_id,room_id,actor_binding_id,sequence) VALUES(?,?,?,?,?) "
                "ON CONFLICT(tenant_id,workspace_id,room_id,actor_binding_id) DO UPDATE SET sequence=excluded.sequence",
                (*keys, sequence),
            )
        return {"room_id": keys[2], "actor_binding_id": keys[3], "sequence": sequence}

    def renew_presence(
        self,
        tenant_id: str,
        workspace_id: str,
        actor_binding_id: str,
        lease_id: str,
        expires_at: float,
        epoch: int,
    ) -> dict[str, Any]:
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
            raise ValueError("collaboration_presence_epoch_invalid")
        payload = {
            "actor_binding_id": require_id(actor_binding_id, "actor_binding_id"),
            "lease_id": require_id(lease_id, "lease_id"),
            "expires_at": float(expires_at),
            "epoch": epoch,
        }
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            payload["actor_binding_id"],
        )
        with self._transaction, self._connect() as connection:
            security_epoch = connection.execute(
                "SELECT epoch FROM collaboration_security_epochs WHERE tenant_id=? AND workspace_id=?",
                keys[:2],
            ).fetchone()
            if security_epoch is None or epoch != int(security_epoch[0]):
                raise CollaborationStoreConflict("collaboration_presence_security_epoch_stale")
            current = connection.execute(
                "SELECT epoch FROM collaboration_presence WHERE tenant_id=? AND workspace_id=? AND actor_binding_id=?",
                keys,
            ).fetchone()
            if current and epoch < int(current[0]):
                raise CollaborationStoreConflict("collaboration_presence_epoch_stale")
            connection.execute(
                "INSERT INTO collaboration_presence(tenant_id,workspace_id,actor_binding_id,epoch,payload_json) VALUES(?,?,?,?,?) "
                "ON CONFLICT(tenant_id,workspace_id,actor_binding_id) DO UPDATE SET epoch=excluded.epoch,payload_json=excluded.payload_json",
                (*keys, epoch, canonical_json(payload)),
            )
        return payload

    def room_presence(
        self,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        actor_binding_id: str,
        *,
        now: float,
    ) -> list[dict[str, Any]]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        room = require_id(room_id, "room_id")
        if not self.room_visible(tenant, workspace, room, actor_binding_id):
            raise PermissionError("collaboration_room_visibility_denied")
        with self._connect() as connection:
            access = connection.execute(
                "SELECT access_mode FROM collaboration_room_access WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                (tenant, workspace, room),
            ).fetchone()
            if access and access[0] == "restricted":
                actors = connection.execute(
                    "SELECT m.actor_binding_id,a.payload_json,p.payload_json FROM collaboration_room_memberships rm "
                    "JOIN collaboration_memberships m ON m.tenant_id=rm.tenant_id AND m.workspace_id=rm.workspace_id "
                    "AND m.actor_binding_id=rm.actor_binding_id JOIN collaboration_actors a ON a.tenant_id=m.tenant_id "
                    "AND a.actor_binding_id=m.actor_binding_id LEFT JOIN collaboration_presence p ON "
                    "p.tenant_id=m.tenant_id AND p.workspace_id=m.workspace_id AND p.actor_binding_id=m.actor_binding_id "
                    "WHERE rm.tenant_id=? AND rm.workspace_id=? AND rm.room_id=? AND m.status='active' "
                    "ORDER BY m.actor_binding_id",
                    (tenant, workspace, room),
                ).fetchall()
            else:
                actors = connection.execute(
                    "SELECT m.actor_binding_id,a.payload_json,p.payload_json FROM collaboration_memberships m "
                    "JOIN collaboration_actors a ON a.tenant_id=m.tenant_id AND a.actor_binding_id=m.actor_binding_id "
                    "LEFT JOIN collaboration_presence p ON p.tenant_id=m.tenant_id AND p.workspace_id=m.workspace_id "
                    "AND p.actor_binding_id=m.actor_binding_id WHERE m.tenant_id=? AND m.workspace_id=? "
                    "AND m.status='active' ORDER BY m.actor_binding_id",
                    (tenant, workspace),
                ).fetchall()
        result = []
        for actor_id, actor_json, presence_json in actors:
            presence = json.loads(presence_json) if presence_json else None
            state = "online" if presence and float(presence["expires_at"]) > now else "stale"
            result.append(
                {
                    "actor_binding_id": actor_id,
                    "actor": json.loads(actor_json),
                    "presence_state": state,
                    "expires_at": presence["expires_at"] if presence else None,
                    "membership_authority": False,
                    "task_ready_authority": False,
                }
            )
        return result
