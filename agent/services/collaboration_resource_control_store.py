"""Agent-intent, resource-lease, command and quota persistence."""

# SQL statements retain complete column/key declarations on one line for auditability.
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from agent.services.collaboration_workspace_store_contracts import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import canonical_digest, canonical_json, require_id


class CollaborationResourceControlStoreMixin:
    """Persist Hub-controlled resource and admission decisions."""

    def admit_agent_intent(
        self, tenant_id: str, intent: Mapping[str, Any], *, maximum_correlation_intents: int
    ) -> tuple[dict[str, Any], bool]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(intent.get("workspace_id"), "workspace_id")
        intent_id = require_id(intent.get("intent_id"), "intent_id")
        digest = canonical_digest(dict(intent))
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            current = connection.execute(
                "SELECT payload_digest,payload_json FROM collaboration_agent_intents WHERE tenant_id=? "
                "AND workspace_id=? AND intent_id=?",
                (tenant, workspace, intent_id),
            ).fetchone()
            if current:
                if current[0] != digest:
                    raise CollaborationStoreConflict("collaboration_agent_intent_conflict")
                return json.loads(current[1]), True
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM collaboration_agent_intents WHERE tenant_id=? AND workspace_id=? "
                    "AND correlation_id=?",
                    (tenant, workspace, intent["correlation_id"]),
                ).fetchone()[0]
            )
            if count >= maximum_correlation_intents:
                raise CollaborationStoreConflict("collaboration_agent_intent_loop_limit")
            if intent.get("causation_id") == intent_id:
                raise CollaborationStoreConflict("collaboration_agent_intent_self_causation")
            self._validate_intent_causation(
                connection,
                tenant,
                workspace,
                intent_id=intent_id,
                causation_id=intent.get("causation_id"),
            )
            value = {**dict(intent), "state": "pending_hub_decision", "payload_digest": digest}
            connection.execute(
                "INSERT INTO collaboration_agent_intents(tenant_id,workspace_id,intent_id,correlation_id,"
                "causation_id,hop_count,state,payload_digest,payload_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    tenant,
                    workspace,
                    intent_id,
                    intent["correlation_id"],
                    intent.get("causation_id"),
                    intent["hop_count"],
                    value["state"],
                    digest,
                    canonical_json(value),
                ),
            )
        return value, False

    @staticmethod
    def _validate_intent_causation(
        connection: sqlite3.Connection,
        tenant_id: str,
        workspace_id: str,
        *,
        intent_id: str,
        causation_id: object,
    ) -> None:
        current = str(causation_id or "").strip() or None
        visited = {intent_id}
        depth = 0
        while current is not None:
            if current in visited:
                raise CollaborationStoreConflict("collaboration_agent_intent_causation_cycle")
            visited.add(current)
            depth += 1
            if depth > 8:
                raise CollaborationStoreConflict("collaboration_agent_intent_causation_depth")
            row = connection.execute(
                "SELECT causation_id FROM collaboration_agent_intents WHERE tenant_id=? AND workspace_id=? "
                "AND intent_id=?",
                (tenant_id, workspace_id, current),
            ).fetchone()
            current = (str(row[0] or "").strip() or None) if row else None

    def decide_agent_intent(
        self,
        tenant_id: str,
        workspace_id: str,
        intent_id: str,
        *,
        state: str,
        reason_code: str,
        assignment: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if state not in {"accepted", "denied"}:
            raise ValueError("collaboration_agent_intent_decision_invalid")
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(intent_id, "intent_id"),
        )
        reason = require_id(reason_code, "intent_reason_code")
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT state,payload_json FROM collaboration_agent_intents WHERE tenant_id=? AND workspace_id=? "
                "AND intent_id=?",
                keys,
            ).fetchone()
            if row is None:
                raise KeyError("collaboration_agent_intent_not_found")
            value = json.loads(row[1])
            if row[0] != "pending_hub_decision":
                return value
            value.update({"state": state, "reason_code": reason, "assignment": dict(assignment or {})})
            connection.execute(
                "UPDATE collaboration_agent_intents SET state=?,payload_json=? WHERE tenant_id=? AND workspace_id=? "
                "AND intent_id=?",
                (state, canonical_json(value), *keys),
            )
        return value

    def reserve_resource_lease(self, tenant_id: str, lease: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(lease.get("workspace_id"), "workspace_id")
        lease_id = require_id(lease.get("lease_id"), "lease_id")
        digest = canonical_digest(dict(lease))
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            current = connection.execute(
                "SELECT payload_digest,payload_json,status FROM collaboration_resource_leases WHERE tenant_id=? "
                "AND workspace_id=? AND lease_id=?",
                (tenant, workspace, lease_id),
            ).fetchone()
            if current:
                if current[0] != digest:
                    raise CollaborationStoreConflict("collaboration_resource_lease_conflict")
                return {**json.loads(current[1]), "status": current[2]}, True
            connection.execute(
                "INSERT INTO collaboration_resource_leases(tenant_id,workspace_id,lease_id,resource_id,task_id,"
                "assignment_id,fencing_token,status,expires_at,payload_digest,payload_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tenant,
                    workspace,
                    lease_id,
                    lease["resource_id"],
                    lease["task_id"],
                    lease["assignment_id"],
                    lease["fencing_token"],
                    lease["status"],
                    lease["expires_at"],
                    digest,
                    canonical_json(dict(lease)),
                ),
            )
        return dict(lease), False

    def resource_lease(self, tenant_id: str, workspace_id: str, lease_id: str) -> dict[str, Any] | None:
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(lease_id, "lease_id"),
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json,status FROM collaboration_resource_leases "
                "WHERE tenant_id=? AND workspace_id=? AND lease_id=?",
                keys,
            ).fetchone()
        return {**json.loads(row[0]), "status": row[1]} if row else None

    def validate_resource_result(
        self,
        tenant_id: str,
        workspace_id: str,
        lease_id: str,
        *,
        task_id: str,
        assignment_id: str,
        fencing_token: int,
        now: float,
    ) -> dict[str, Any]:
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(lease_id, "lease_id"),
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_id,assignment_id,fencing_token,status,expires_at,payload_json "
                "FROM collaboration_resource_leases WHERE tenant_id=? AND workspace_id=? AND lease_id=?",
                keys,
            ).fetchone()
        if row is None:
            raise KeyError("collaboration_resource_lease_not_found")
        if (
            row[0] != require_id(task_id, "task_id")
            or row[1] != require_id(assignment_id, "assignment_id")
            or int(row[2]) != fencing_token
            or row[3] != "active"
            or float(row[4]) <= now
        ):
            raise CollaborationStoreConflict("collaboration_resource_result_binding_rejected")
        return json.loads(row[5])

    def revoke_resource_leases(self, tenant_id: str, workspace_id: str, *, task_id: str) -> int:
        with self._transaction, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE collaboration_resource_leases SET status='revoked' WHERE tenant_id=? AND workspace_id=? "
                "AND task_id=? AND status='active'",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(workspace_id, "workspace_id"),
                    require_id(task_id, "task_id"),
                ),
            )
        return int(cursor.rowcount)

    def record_command_decision(self, tenant_id: str, decision: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(decision.get("workspace_id"), "workspace_id")
        request_id = require_id(decision.get("request_id"), "request_id")
        binding_digest = require_id(decision.get("binding_digest"), "binding_digest")
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            current = connection.execute(
                "SELECT binding_digest,payload_json FROM collaboration_command_decisions "
                "WHERE tenant_id=? AND workspace_id=? AND request_id=?",
                (tenant, workspace, request_id),
            ).fetchone()
            if current:
                if current[0] != binding_digest:
                    raise CollaborationStoreConflict("collaboration_command_decision_replay_conflict")
                return json.loads(current[1]), True
            connection.execute(
                "INSERT INTO collaboration_command_decisions(tenant_id,workspace_id,request_id,task_id,"
                "binding_digest,state,policy_revision,payload_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    tenant,
                    workspace,
                    request_id,
                    decision["task_id"],
                    binding_digest,
                    decision["state"],
                    decision["policy_revision"],
                    canonical_json(dict(decision)),
                ),
            )
        return dict(decision), False

    def consume_quota(
        self,
        tenant_id: str,
        workspace_id: str,
        actor_binding_id: str,
        *,
        category: str,
        now: float,
        window_seconds: int,
        maximum: int,
    ) -> dict[str, Any]:
        if window_seconds < 1 or maximum < 1:
            raise ValueError("collaboration_quota_policy_invalid")
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(actor_binding_id, "actor_binding_id"),
            require_id(category, "quota_category"),
        )
        with self._transaction, self._connect() as connection:
            return self._consume_quota_connection(
                connection,
                keys[0],
                keys[1],
                keys[2],
                category=keys[3],
                now=now,
                window_seconds=window_seconds,
                maximum=maximum,
            )

    def consume_quota_set(
        self,
        tenant_id: str,
        workspace_id: str,
        quotas: list[Mapping[str, Any]],
        *,
        now: float,
    ) -> list[dict[str, Any]]:
        """Consume all quota dimensions atomically so a denial has no partial side effects."""

        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        if not quotas or len(quotas) > 16:
            raise ValueError("collaboration_quota_set_invalid")
        normalized: list[tuple[str, str, int, int]] = []
        for quota in quotas:
            if set(quota) != {"subject", "category", "window_seconds", "maximum"}:
                raise ValueError("collaboration_quota_set_invalid")
            window = quota["window_seconds"]
            maximum = quota["maximum"]
            if (
                not isinstance(window, int)
                or isinstance(window, bool)
                or window < 1
                or not isinstance(maximum, int)
                or isinstance(maximum, bool)
                or maximum < 1
            ):
                raise ValueError("collaboration_quota_policy_invalid")
            normalized.append(
                (
                    require_id(quota["subject"], "quota_subject"),
                    require_id(quota["category"], "quota_category"),
                    window,
                    maximum,
                )
            )
        with self._transaction, self._connect() as connection:
            return [
                self._consume_quota_connection(
                    connection,
                    tenant,
                    workspace,
                    subject,
                    category=category,
                    now=now,
                    window_seconds=window,
                    maximum=maximum,
                )
                for subject, category, window, maximum in normalized
            ]

    def reset_quotas(
        self,
        tenant_id: str,
        workspace_id: str,
        *,
        subject: str,
        category: str,
    ) -> int:
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(subject, "quota_subject"),
            require_id(category, "quota_category"),
        )
        with self._transaction, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM collaboration_admission_quotas WHERE tenant_id=? AND workspace_id=? "
                "AND actor_binding_id=? AND category=?",
                keys,
            )
        return int(cursor.rowcount)

    @staticmethod
    def _consume_quota_connection(
        connection: sqlite3.Connection,
        tenant_id: str,
        workspace_id: str,
        actor_binding_id: str,
        *,
        category: str,
        now: float,
        window_seconds: int,
        maximum: int,
    ) -> dict[str, Any]:
        window = int(float(now) // window_seconds) * window_seconds
        keys = (tenant_id, workspace_id, actor_binding_id, category)
        row = connection.execute(
            "SELECT count FROM collaboration_admission_quotas WHERE tenant_id=? AND workspace_id=? "
            "AND actor_binding_id=? AND category=? AND window_start=?",
            (*keys, window),
        ).fetchone()
        count = int(row[0]) if row else 0
        if count >= maximum:
            raise CollaborationStoreConflict("collaboration_admission_rate_limited")
        count += 1
        connection.execute(
            "INSERT INTO collaboration_admission_quotas(tenant_id,workspace_id,actor_binding_id,category,"
            "window_start,count) VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id,actor_binding_id,"
            "category,window_start) DO UPDATE SET count=excluded.count",
            (*keys, window, count),
        )
        connection.execute(
            "DELETE FROM collaboration_admission_quotas WHERE window_start<?",
            (window - (2 * window_seconds),),
        )
        return {"category": category, "count": count, "maximum": maximum, "window_start": window}
