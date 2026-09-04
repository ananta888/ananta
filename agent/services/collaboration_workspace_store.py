"""Tenant-scoped append-only collaboration store with outbox and dedupe."""

# SQL statements retain complete column/key declarations on one line for auditability.
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.collaboration_workspace import canonical_digest, canonical_json, require_id


class CollaborationStoreConflict(RuntimeError):
    pass


class CollaborationWorkspaceStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def create_workspace(self, workspace: Mapping[str, Any], owner: Mapping[str, Any]) -> dict[str, Any]:
        tenant = require_id(workspace.get("tenant_id"), "tenant_id")
        workspace_id = require_id(workspace.get("workspace_id"), "workspace_id")
        with self._transaction, self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM collaboration_workspaces WHERE tenant_id=? AND workspace_id=?",
                (tenant, workspace_id),
            ).fetchone():
                raise CollaborationStoreConflict("collaboration_workspace_exists")
            connection.execute(
                "INSERT INTO collaboration_workspaces(tenant_id,workspace_id,revision,payload_json) VALUES(?,?,?,?)",
                (tenant, workspace_id, 1, canonical_json({**dict(workspace), "revision": 1})),
            )
            self._upsert_actor(connection, tenant, owner)
            connection.execute(
                "INSERT INTO collaboration_memberships(tenant_id,workspace_id,actor_binding_id,revision,role,status,payload_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    tenant,
                    workspace_id,
                    owner["actor_binding_id"],
                    1,
                    "owner",
                    "active",
                    canonical_json(
                        {
                            "actor_binding_id": owner["actor_binding_id"],
                            "role": "owner",
                            "status": "active",
                            "revision": 1,
                        }
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO collaboration_membership_history(tenant_id,workspace_id,actor_binding_id,revision,"
                "payload_json) VALUES(?,?,?,?,?)",
                (
                    tenant,
                    workspace_id,
                    owner["actor_binding_id"],
                    1,
                    canonical_json(
                        {
                            "actor_binding_id": owner["actor_binding_id"],
                            "role": "owner",
                            "status": "active",
                            "revision": 1,
                            "effective_capabilities": sorted(
                                {
                                    "workspace.manage",
                                    "room.manage",
                                    "event.write",
                                    "event.read",
                                    "presence.write",
                                    "cursor.write",
                                }
                            ),
                        }
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO collaboration_security_epochs(tenant_id,workspace_id,epoch) VALUES(?,?,?)",
                (tenant, workspace_id, 1),
            )
        return self.get_workspace(tenant, workspace_id)

    def add_room(self, tenant_id: str, workspace_id: str, room: Mapping[str, Any]) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        room_id = require_id(room.get("room_id"), "room_id")
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            try:
                connection.execute(
                    "INSERT INTO collaboration_rooms(tenant_id,workspace_id,room_id,payload_json) VALUES(?,?,?,?)",
                    (tenant, workspace, room_id, canonical_json(dict(room))),
                )
            except sqlite3.IntegrityError as exc:
                raise CollaborationStoreConflict("collaboration_room_exists") from exc
            connection.execute(
                "INSERT INTO collaboration_room_access(tenant_id,workspace_id,room_id,access_mode,revision) "
                "VALUES(?,?,?,?,?)",
                (tenant, workspace, room_id, "workspace", 1),
            )
            connection.execute(
                "INSERT INTO collaboration_room_lifecycle(tenant_id,workspace_id,room_id,state,revision,"
                "snapshot_digest,payload_json) VALUES(?,?,?,?,?,?,?)",
                (
                    tenant,
                    workspace,
                    room_id,
                    "active",
                    1,
                    None,
                    canonical_json({"room_id": room_id, "state": "active", "revision": 1}),
                ),
            )
        return dict(room)

    def transition_room(
        self,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        *,
        target_state: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        if target_state not in {"active", "archived"}:
            raise ValueError("collaboration_room_lifecycle_state_invalid")
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(room_id, "room_id"),
        )
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT state,revision,payload_json FROM collaboration_room_lifecycle "
                "WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                keys,
            ).fetchone()
            if row is None:
                raise KeyError("collaboration_room_not_found")
            if int(row[1]) != expected_revision:
                raise CollaborationStoreConflict("collaboration_room_lifecycle_revision_conflict")
            if row[0] == target_state:
                return {**json.loads(row[2]), "replayed": True}
            events = connection.execute(
                "SELECT payload_json FROM collaboration_events WHERE tenant_id=? AND workspace_id=? "
                "AND room_id=? ORDER BY sequence",
                keys,
            ).fetchall()
            snapshot_digest = canonical_digest([json.loads(item[0]) for item in events])
            value = {
                "room_id": keys[2],
                "state": target_state,
                "revision": int(row[1]) + 1,
                "snapshot_digest": snapshot_digest,
                "checkpoint": max((json.loads(item[0])["sequence"] for item in events), default=0),
            }
            connection.execute(
                "UPDATE collaboration_room_lifecycle SET state=?,revision=?,snapshot_digest=?,payload_json=? "
                "WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                (
                    target_state,
                    value["revision"],
                    snapshot_digest,
                    canonical_json(value),
                    *keys,
                ),
            )
        return value

    def put_room_binding(
        self,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        binding: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(room_id, "room_id"),
        )
        digest = canonical_digest(dict(binding))
        with self._transaction, self._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM collaboration_rooms WHERE tenant_id=? AND workspace_id=? AND room_id=?", keys
            ).fetchone():
                raise KeyError("collaboration_room_not_found")
            current = connection.execute(
                "SELECT revision,binding_digest,payload_json FROM collaboration_room_bindings "
                "WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                keys,
            ).fetchone()
            actual = int(current[0]) if current else 0
            if actual != expected_revision:
                raise CollaborationStoreConflict("collaboration_room_binding_revision_conflict")
            if current and current[1] == digest:
                return {**json.loads(current[2]), "replayed": True}
            conflict = connection.execute(
                "SELECT room_id FROM collaboration_room_bindings WHERE tenant_id=? AND workspace_id=? "
                "AND binding_kind=? AND binding_id=? AND room_id<>?",
                (keys[0], keys[1], binding["binding_kind"], binding["binding_id"], keys[2]),
            ).fetchone()
            if conflict:
                raise CollaborationStoreConflict("collaboration_room_binding_conflict")
            value = {
                **dict(binding),
                "room_id": keys[2],
                "revision": actual + 1,
                "binding_digest": digest,
            }
            connection.execute(
                "INSERT INTO collaboration_room_bindings(tenant_id,workspace_id,room_id,binding_kind,binding_id,"
                "revision,binding_digest,payload_json) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id,"
                "room_id) DO UPDATE SET binding_kind=excluded.binding_kind,binding_id=excluded.binding_id,"
                "revision=excluded.revision,binding_digest=excluded.binding_digest,payload_json=excluded.payload_json",
                (
                    keys[0],
                    keys[1],
                    keys[2],
                    binding["binding_kind"],
                    binding["binding_id"],
                    value["revision"],
                    digest,
                    canonical_json(value),
                ),
            )
        return value

    def room_for_binding(
        self, tenant_id: str, workspace_id: str, binding_kind: str, binding_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collaboration_room_bindings WHERE tenant_id=? AND workspace_id=? "
                "AND binding_kind=? AND binding_id=?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(workspace_id, "workspace_id"),
                    require_id(binding_kind, "binding_kind"),
                    require_id(binding_id, "binding_id"),
                ),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put_room_access(
        self,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        *,
        access_mode: str,
        actor_binding_ids: list[str],
        expected_revision: int,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        room = require_id(room_id, "room_id")
        if access_mode not in {"workspace", "restricted"}:
            raise ValueError("collaboration_room_access_mode_invalid")
        actors = sorted({require_id(value, "actor_binding_id") for value in actor_binding_ids})
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            if not connection.execute(
                "SELECT 1 FROM collaboration_rooms WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                (tenant, workspace, room),
            ).fetchone():
                raise KeyError("collaboration_room_not_found")
            row = connection.execute(
                "SELECT revision FROM collaboration_room_access WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                (tenant, workspace, room),
            ).fetchone()
            actual = int(row[0]) if row else 0
            if actual != expected_revision:
                raise CollaborationStoreConflict("collaboration_room_access_revision_conflict")
            revision = actual + 1
            connection.execute(
                "INSERT INTO collaboration_room_access(tenant_id,workspace_id,room_id,access_mode,revision) "
                "VALUES(?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id,room_id) DO UPDATE SET "
                "access_mode=excluded.access_mode,revision=excluded.revision",
                (tenant, workspace, room, access_mode, revision),
            )
            connection.execute(
                "DELETE FROM collaboration_room_memberships WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                (tenant, workspace, room),
            )
            for actor in actors:
                membership = connection.execute(
                    "SELECT status FROM collaboration_memberships "
                    "WHERE tenant_id=? AND workspace_id=? AND actor_binding_id=?",
                    (tenant, workspace, actor),
                ).fetchone()
                if membership is None or membership[0] != "active":
                    raise ValueError("collaboration_room_actor_membership_invalid")
                connection.execute(
                    "INSERT INTO collaboration_room_memberships(tenant_id,workspace_id,room_id,actor_binding_id) "
                    "VALUES(?,?,?,?)",
                    (tenant, workspace, room, actor),
                )
        return {
            "room_id": room,
            "access_mode": access_mode,
            "actor_binding_ids": actors,
            "revision": revision,
        }

    def room_visible(self, tenant_id: str, workspace_id: str, room_id: str, actor_binding_id: str) -> bool:
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(room_id, "room_id"),
        )
        actor = require_id(actor_binding_id, "actor_binding_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT access_mode FROM collaboration_room_access WHERE tenant_id=? AND workspace_id=? AND room_id=?",
                keys,
            ).fetchone()
            if row is None:
                raise KeyError("collaboration_room_not_found")
            if row[0] == "workspace":
                return True
            return bool(
                connection.execute(
                    "SELECT 1 FROM collaboration_room_memberships "
                    "WHERE tenant_id=? AND workspace_id=? AND room_id=? AND actor_binding_id=?",
                    (*keys, actor),
                ).fetchone()
            )

    def put_membership(
        self,
        tenant_id: str,
        workspace_id: str,
        actor: Mapping[str, Any],
        *,
        role: str,
        status: str,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        actor_id = require_id(actor.get("actor_binding_id"), "actor_binding_id")
        role_capabilities = {
            "owner": {"workspace.manage", "room.manage", "event.write", "event.read", "presence.write", "cursor.write"},
            "maintainer": {"room.manage", "event.write", "event.read", "presence.write", "cursor.write"},
            "member": {"event.write", "event.read", "presence.write", "cursor.write"},
            "guest": {"event.write", "event.read", "presence.write", "cursor.write"},
            "observer": {"event.read", "presence.write", "cursor.write"},
            "editor": {"event.write", "event.read", "presence.write", "cursor.write"},
            "viewer": {"event.read", "presence.write", "cursor.write"},
        }
        if role not in role_capabilities or status not in {"active", "revoked"}:
            raise ValueError("collaboration_membership_invalid")
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            self._upsert_actor(connection, tenant, actor)
            current = connection.execute(
                "SELECT revision,payload_json FROM collaboration_memberships "
                "WHERE tenant_id=? AND workspace_id=? AND actor_binding_id=?",
                (tenant, workspace, actor_id),
            ).fetchone()
            actual = int(current[0]) if current else 0
            if expected_revision is not None and actual != expected_revision:
                raise CollaborationStoreConflict("collaboration_membership_revision_conflict")
            if current:
                current_payload = json.loads(current[1])
                if current_payload["role"] == role and current_payload["status"] == status:
                    return {**current_payload, "replayed": True}
            revision = actual + 1
            payload = {
                "actor_binding_id": actor_id,
                "role": role,
                "status": status,
                "revision": revision,
                "effective_capabilities": sorted(role_capabilities[role]) if status == "active" else [],
            }
            connection.execute(
                "INSERT INTO collaboration_memberships(tenant_id,workspace_id,actor_binding_id,revision,role,status,payload_json) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id,actor_binding_id) DO UPDATE SET "
                "revision=excluded.revision,role=excluded.role,status=excluded.status,payload_json=excluded.payload_json",
                (tenant, workspace, actor_id, revision, role, status, canonical_json(payload)),
            )
            connection.execute(
                "INSERT INTO collaboration_membership_history(tenant_id,workspace_id,actor_binding_id,revision,"
                "payload_json) VALUES(?,?,?,?,?)",
                (tenant, workspace, actor_id, revision, canonical_json(payload)),
            )
            if status == "revoked":
                connection.execute(
                    "DELETE FROM collaboration_room_memberships WHERE tenant_id=? AND workspace_id=? "
                    "AND actor_binding_id=?",
                    (tenant, workspace, actor_id),
                )
                connection.execute(
                    "DELETE FROM collaboration_presence WHERE tenant_id=? AND workspace_id=? AND actor_binding_id=?",
                    (tenant, workspace, actor_id),
                )
                connection.execute(
                    "UPDATE collaboration_security_epochs SET epoch=epoch+1 WHERE tenant_id=? AND workspace_id=?",
                    (tenant, workspace),
                )
        return payload

    def membership(self, tenant_id: str, workspace_id: str, actor_binding_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collaboration_memberships WHERE tenant_id=? AND workspace_id=? AND actor_binding_id=?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(workspace_id, "workspace_id"),
                    require_id(actor_binding_id, "actor_binding_id"),
                ),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def actor(self, tenant_id: str, actor_binding_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collaboration_actors WHERE tenant_id=? AND actor_binding_id=?",
                (require_id(tenant_id, "tenant_id"), require_id(actor_binding_id, "actor_binding_id")),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put_external_identity(
        self,
        tenant_id: str,
        *,
        actor_binding_id: str,
        provider: str,
        external_subject: str,
        key_fingerprint: str,
        status: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        actor_id = require_id(actor_binding_id, "actor_binding_id")
        provider_id = require_id(provider, "identity_provider")
        subject = require_id(external_subject, "external_subject")
        fingerprint = str(key_fingerprint or "").strip().lower()
        if status not in {"active", "revoked"}:
            raise ValueError("collaboration_identity_link_status_invalid")
        if len(fingerprint) != 64 or any(character not in "0123456789abcdef" for character in fingerprint):
            raise ValueError("collaboration_identity_key_fingerprint_invalid")
        with self._transaction, self._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM collaboration_actors WHERE tenant_id=? AND actor_binding_id=?",
                (tenant, actor_id),
            ).fetchone():
                raise KeyError("collaboration_actor_not_found")
            current = connection.execute(
                "SELECT revision,payload_json FROM collaboration_external_identities "
                "WHERE tenant_id=? AND actor_binding_id=? AND provider=?",
                (tenant, actor_id, provider_id),
            ).fetchone()
            actual = int(current[0]) if current else 0
            if actual != expected_revision:
                raise CollaborationStoreConflict("collaboration_identity_link_revision_conflict")
            desired = {
                "actor_binding_id": actor_id,
                "provider": provider_id,
                "external_subject": subject,
                "key_fingerprint": fingerprint,
                "status": status,
            }
            if current:
                existing = json.loads(current[1])
                if all(existing[key] == desired[key] for key in desired):
                    return {**existing, "replayed": True}
            collision = connection.execute(
                "SELECT actor_binding_id FROM collaboration_external_identities WHERE tenant_id=? AND provider=? "
                "AND external_subject=? AND status='active' AND actor_binding_id<>?",
                (tenant, provider_id, subject, actor_id),
            ).fetchone()
            if collision:
                raise CollaborationStoreConflict("collaboration_external_identity_conflict")
            revision = actual + 1
            reason_code = (
                "identity_unlinked"
                if status == "revoked"
                else "identity_linked"
                if actual == 0
                else "identity_key_rotated"
            )
            payload = {**desired, "revision": revision, "reason_code": reason_code}
            connection.execute(
                "INSERT INTO collaboration_external_identities(tenant_id,actor_binding_id,provider,"
                "external_subject,key_fingerprint,revision,status,payload_json) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(tenant_id,actor_binding_id,provider) DO UPDATE SET "
                "external_subject=excluded.external_subject,key_fingerprint=excluded.key_fingerprint,"
                "revision=excluded.revision,status=excluded.status,payload_json=excluded.payload_json",
                (
                    tenant,
                    actor_id,
                    provider_id,
                    subject,
                    fingerprint,
                    revision,
                    status,
                    canonical_json(payload),
                ),
            )
            connection.execute(
                "INSERT INTO collaboration_external_identity_history(tenant_id,actor_binding_id,provider,revision,"
                "payload_json) VALUES(?,?,?,?,?)",
                (tenant, actor_id, provider_id, revision, canonical_json(payload)),
            )
        return payload

    def external_identities(self, tenant_id: str, actor_binding_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM collaboration_external_identities WHERE tenant_id=? AND actor_binding_id=? "
                "ORDER BY provider",
                (require_id(tenant_id, "tenant_id"), require_id(actor_binding_id, "actor_binding_id")),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put_resource_offer(self, tenant_id: str, offer: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(offer.get("workspace_id"), "workspace_id")
        offer_id = require_id(offer.get("offer_id"), "offer_id")
        digest = canonical_digest(dict(offer))
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            current = connection.execute(
                "SELECT payload_digest,payload_json FROM collaboration_resource_offers "
                "WHERE tenant_id=? AND workspace_id=? AND offer_id=?",
                (tenant, workspace, offer_id),
            ).fetchone()
            if current:
                if current[0] != digest:
                    raise CollaborationStoreConflict("collaboration_resource_offer_conflict")
                return json.loads(current[1]), True
            value = {**dict(offer), "status": "active", "payload_digest": digest}
            connection.execute(
                "INSERT INTO collaboration_resource_offers(tenant_id,workspace_id,offer_id,owner_actor_binding_id,"
                "resource_id,status,expires_at,payload_digest,payload_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    tenant,
                    workspace,
                    offer_id,
                    offer["owner_actor_binding_id"],
                    offer["resource_id"],
                    "active",
                    offer["expires_at"],
                    digest,
                    canonical_json(value),
                ),
            )
        return value, False

    def resource_offer(self, tenant_id: str, workspace_id: str, offer_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collaboration_resource_offers WHERE tenant_id=? AND workspace_id=? "
                "AND offer_id=?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(workspace_id, "workspace_id"),
                    require_id(offer_id, "offer_id"),
                ),
            ).fetchone()
        return json.loads(row[0]) if row else None

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

    def get_workspace(self, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._workspace_row(
                connection, require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id")
            )
            rooms = connection.execute(
                "SELECT r.payload_json,a.access_mode,a.revision,l.state,l.revision,l.snapshot_digest "
                "FROM collaboration_rooms r "
                "JOIN collaboration_room_access a ON a.tenant_id=r.tenant_id "
                "AND a.workspace_id=r.workspace_id AND a.room_id=r.room_id "
                "JOIN collaboration_room_lifecycle l ON l.tenant_id=r.tenant_id "
                "AND l.workspace_id=r.workspace_id AND l.room_id=r.room_id "
                "WHERE r.tenant_id=? AND r.workspace_id=? ORDER BY r.room_id",
                (tenant_id, workspace_id),
            ).fetchall()
            members = connection.execute(
                "SELECT m.payload_json,a.payload_json FROM collaboration_memberships m JOIN collaboration_actors a "
                "ON a.tenant_id=m.tenant_id AND a.actor_binding_id=m.actor_binding_id "
                "WHERE m.tenant_id=? AND m.workspace_id=? ORDER BY m.actor_binding_id",
                (tenant_id, workspace_id),
            ).fetchall()
        value = json.loads(row[0])
        value["rooms"] = [
            {
                **json.loads(item[0]),
                "access_mode": item[1],
                "access_revision": int(item[2]),
                "lifecycle_state": item[3],
                "lifecycle_revision": int(item[4]),
                "snapshot_digest": item[5],
            }
            for item in rooms
        ]
        value["memberships"] = [{**json.loads(item[0]), "actor": json.loads(item[1])} for item in members]
        return value

    def list_workspaces(self, tenant_id: str, actor_binding_id: str, *, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("collaboration_workspace_list_limit_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT w.payload_json FROM collaboration_workspaces w JOIN collaboration_memberships m "
                "ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id "
                "WHERE w.tenant_id=? AND m.actor_binding_id=? AND m.status='active' ORDER BY w.workspace_id LIMIT ?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(actor_binding_id, "actor_binding_id"),
                    limit,
                ),
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "limit": limit}

    def operational_snapshot(self, tenant_id: str, workspace_id: str) -> dict[str, int]:
        """Return bounded, content-free workspace counters for observability."""

        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        with self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            event_count, latest_sequence = connection.execute(
                "SELECT COUNT(*),COALESCE(MAX(sequence),0) FROM collaboration_events "
                "WHERE tenant_id=? AND workspace_id=?",
                (tenant, workspace),
            ).fetchone()
            pending_outbox = connection.execute(
                "SELECT COUNT(*) FROM collaboration_outbox o JOIN collaboration_events e "
                "ON e.tenant_id=o.tenant_id AND e.event_id=o.event_id "
                "WHERE e.tenant_id=? AND e.workspace_id=? AND o.status!='completed'",
                (tenant, workspace),
            ).fetchone()[0]
            retry_outbox = connection.execute(
                "SELECT COUNT(*) FROM collaboration_outbox o JOIN collaboration_events e "
                "ON e.tenant_id=o.tenant_id AND e.event_id=o.event_id "
                "WHERE e.tenant_id=? AND e.workspace_id=? AND o.status='retry'",
                (tenant, workspace),
            ).fetchone()[0]
            inbox_count = connection.execute(
                "SELECT COUNT(*) FROM collaboration_inbox WHERE tenant_id=?",
                (tenant,),
            ).fetchone()[0]
            projection_rows = connection.execute(
                "SELECT projection_name,checkpoint FROM collaboration_projection_checkpoints "
                "WHERE tenant_id=? AND workspace_id=?",
                (tenant, workspace),
            ).fetchall()
            search = connection.execute(
                "SELECT checkpoint FROM collaboration_search_manifests WHERE tenant_id=? AND workspace_id=?",
                (tenant, workspace),
            ).fetchone()
        projection_lag = max([int(latest_sequence) - int(row[1]) for row in projection_rows] or [int(latest_sequence)])
        search_lag = int(latest_sequence) - int(search[0]) if search else int(latest_sequence)
        return {
            "events": int(event_count),
            "latest_sequence": int(latest_sequence),
            "outbox_pending": int(pending_outbox),
            "outbox_retry": int(retry_outbox),
            "inbox_admitted": int(inbox_count),
            "projection_lag": max(0, projection_lag),
            "search_lag": max(0, search_lag),
        }

    @staticmethod
    def _upsert_actor(connection: sqlite3.Connection, tenant_id: str, actor: Mapping[str, Any]) -> None:
        current = connection.execute(
            "SELECT payload_json FROM collaboration_actors WHERE tenant_id=? AND actor_binding_id=?",
            (tenant_id, actor["actor_binding_id"]),
        ).fetchone()
        if current:
            existing = json.loads(current[0])
            identity_fields = ("actor_kind", "authority_kind", "authority_subject")
            if any(existing[field] != actor[field] for field in identity_fields):
                raise CollaborationStoreConflict("collaboration_actor_binding_conflict")
        connection.execute(
            "INSERT INTO collaboration_actors(tenant_id,actor_binding_id,payload_json) VALUES(?,?,?) "
            "ON CONFLICT(tenant_id,actor_binding_id) DO UPDATE SET payload_json=excluded.payload_json",
            (tenant_id, actor["actor_binding_id"], canonical_json(dict(actor))),
        )

    @staticmethod
    def _workspace_row(connection: sqlite3.Connection, tenant_id: str, workspace_id: str):
        row = connection.execute(
            "SELECT payload_json FROM collaboration_workspaces WHERE tenant_id=? AND workspace_id=?",
            (tenant_id, workspace_id),
        ).fetchone()
        if not row:
            raise KeyError("collaboration_workspace_not_found")
        return row

    @staticmethod
    def _thread_projection(
        connection: sqlite3.Connection, tenant_id: str, workspace_id: str, thread_id: str
    ) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT event_id,room_id,event_type FROM collaboration_events "
            "WHERE tenant_id=? AND workspace_id=? AND (event_id=? OR thread_id=?) ORDER BY sequence",
            (tenant_id, workspace_id, thread_id, thread_id),
        ).fetchall()
        if not rows or rows[0][0] != thread_id or rows[0][2] != "message.posted":
            raise KeyError("collaboration_thread_not_found")
        status = "open"
        for _event_id, _room_id, event_type in rows[1:]:
            if event_type == "thread.resolved":
                status = "resolved"
            elif event_type == "thread.reopened":
                status = "open"
            elif event_type == "thread.tombstoned":
                status = "tombstoned"
        return {
            "thread_id": thread_id,
            "room_id": rows[0][1],
            "status": status,
            "revision": len(rows),
        }

    def _transition_outbox(
        self,
        tenant_id: str,
        event_id: str,
        attempt_id: str,
        *,
        status: str,
        next_attempt_at: float,
        error_code: str | None,
        completed_at: float | None,
    ) -> dict[str, Any]:
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(event_id, "event_id"),
            require_id(attempt_id, "attempt_id"),
        )
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT status,attempt_id,attempts FROM collaboration_outbox WHERE tenant_id=? AND event_id=?",
                keys[:2],
            ).fetchone()
            if row is None:
                raise KeyError("collaboration_outbox_event_not_found")
            if row[0] != "delivering" or row[1] != keys[2]:
                raise CollaborationStoreConflict("collaboration_outbox_attempt_conflict")
            connection.execute(
                "UPDATE collaboration_outbox SET status=?,next_attempt_at=?,last_error_code=?,completed_at=?,"
                "leased_by=NULL,lease_expires_at=0 WHERE tenant_id=? AND event_id=?",
                (status, next_attempt_at, error_code, completed_at, *keys[:2]),
            )
        return {
            "tenant_id": keys[0],
            "event_id": keys[1],
            "attempt_id": keys[2],
            "attempt": int(row[2]),
            "status": status,
            "next_attempt_at": next_attempt_at,
            "error_code": error_code,
        }

    @staticmethod
    def _project(projection_name: str, events: list[dict[str, Any]]) -> Any:
        if projection_name == "timeline":
            return [{"event_id": event["event_id"], "sequence": event["sequence"]} for event in events]
        if projection_name == "search":
            return [
                {
                    "event_id": event["event_id"],
                    "sequence": event["sequence"],
                    "text": canonical_json(event.get("payload") or {}).casefold(),
                }
                for event in events
                if event["event_type"] != "thread.tombstoned"
            ]
        threads: dict[str, dict[str, Any]] = {}
        for event in events:
            if event["event_type"] == "message.posted":
                threads[event["event_id"]] = {
                    "thread_id": event["event_id"],
                    "room_id": event.get("room_id"),
                    "status": "open",
                    "revision": 1,
                }
                continue
            root = threads.get(event.get("thread_id"))
            if root is None:
                continue
            root["revision"] += 1
            if event["event_type"] == "thread.resolved":
                root["status"] = "resolved"
            elif event["event_type"] == "thread.reopened":
                root["status"] = "open"
            elif event["event_type"] == "thread.tombstoned":
                root["status"] = "tombstoned"
        return [threads[key] for key in sorted(threads)]

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS collaboration_workspaces(tenant_id TEXT,workspace_id TEXT,revision INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id));
                CREATE TABLE IF NOT EXISTS collaboration_actors(tenant_id TEXT,actor_binding_id TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_memberships(tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,revision INTEGER,role TEXT,status TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_membership_history(tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,revision INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,actor_binding_id,revision));
                CREATE TABLE IF NOT EXISTS collaboration_external_identities(tenant_id TEXT,actor_binding_id TEXT,provider TEXT,external_subject TEXT,key_fingerprint TEXT,revision INTEGER,status TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,actor_binding_id,provider));
                CREATE UNIQUE INDEX IF NOT EXISTS collaboration_external_identity_subject ON collaboration_external_identities(tenant_id,provider,external_subject) WHERE status='active';
                CREATE TABLE IF NOT EXISTS collaboration_external_identity_history(tenant_id TEXT,actor_binding_id TEXT,provider TEXT,revision INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,actor_binding_id,provider,revision));
                CREATE TABLE IF NOT EXISTS collaboration_resource_offers(tenant_id TEXT,workspace_id TEXT,offer_id TEXT,owner_actor_binding_id TEXT,resource_id TEXT,status TEXT,expires_at REAL,payload_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,offer_id));
                CREATE TABLE IF NOT EXISTS collaboration_agent_intents(tenant_id TEXT,workspace_id TEXT,intent_id TEXT,correlation_id TEXT,causation_id TEXT,hop_count INTEGER,state TEXT,payload_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,intent_id));
                CREATE INDEX IF NOT EXISTS collaboration_agent_intent_correlation ON collaboration_agent_intents(tenant_id,workspace_id,correlation_id);
                CREATE TABLE IF NOT EXISTS collaboration_resource_leases(tenant_id TEXT,workspace_id TEXT,lease_id TEXT,resource_id TEXT,task_id TEXT,assignment_id TEXT,fencing_token INTEGER,status TEXT,expires_at REAL,payload_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,lease_id));
                CREATE TABLE IF NOT EXISTS collaboration_command_decisions(tenant_id TEXT,workspace_id TEXT,request_id TEXT,task_id TEXT,binding_digest TEXT,state TEXT,policy_revision INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,request_id));
                CREATE TABLE IF NOT EXISTS collaboration_admission_quotas(tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,category TEXT,window_start INTEGER,count INTEGER,PRIMARY KEY(tenant_id,workspace_id,actor_binding_id,category,window_start));
                CREATE TABLE IF NOT EXISTS collaboration_rooms(tenant_id TEXT,workspace_id TEXT,room_id TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,room_id));
                CREATE TABLE IF NOT EXISTS collaboration_room_access(tenant_id TEXT,workspace_id TEXT,room_id TEXT,access_mode TEXT,revision INTEGER,PRIMARY KEY(tenant_id,workspace_id,room_id));
                CREATE TABLE IF NOT EXISTS collaboration_room_memberships(tenant_id TEXT,workspace_id TEXT,room_id TEXT,actor_binding_id TEXT,PRIMARY KEY(tenant_id,workspace_id,room_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_room_lifecycle(tenant_id TEXT,workspace_id TEXT,room_id TEXT,state TEXT,revision INTEGER,snapshot_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,room_id));
                CREATE TABLE IF NOT EXISTS collaboration_room_bindings(tenant_id TEXT,workspace_id TEXT,room_id TEXT,binding_kind TEXT,binding_id TEXT,revision INTEGER,binding_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,room_id),UNIQUE(tenant_id,workspace_id,binding_kind,binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_events(tenant_id TEXT,workspace_id TEXT,sequence INTEGER,event_id TEXT,idempotency_key TEXT,room_id TEXT,thread_id TEXT,event_type TEXT,actor_binding_id TEXT,causation_id TEXT,visibility TEXT,occurred_at REAL,admitted_at REAL,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,sequence),UNIQUE(tenant_id,workspace_id,event_id),UNIQUE(tenant_id,workspace_id,idempotency_key));
                CREATE TABLE IF NOT EXISTS collaboration_outbox(tenant_id TEXT,event_id TEXT,topic TEXT,status TEXT,payload_json TEXT,attempt_id TEXT,attempts INTEGER NOT NULL DEFAULT 0,leased_by TEXT,lease_expires_at REAL NOT NULL DEFAULT 0,next_attempt_at REAL NOT NULL DEFAULT 0,last_error_code TEXT,completed_at REAL,PRIMARY KEY(tenant_id,event_id));
                CREATE TABLE IF NOT EXISTS collaboration_inbox(tenant_id TEXT,adapter_id TEXT,external_event_id TEXT,payload_digest TEXT,origin TEXT,mapping_version TEXT,admitted_at REAL,PRIMARY KEY(tenant_id,adapter_id,external_event_id));
                CREATE TABLE IF NOT EXISTS collaboration_cursors(tenant_id TEXT,workspace_id TEXT,room_id TEXT,actor_binding_id TEXT,sequence INTEGER,PRIMARY KEY(tenant_id,workspace_id,room_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_presence(tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,epoch INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_security_epochs(tenant_id TEXT,workspace_id TEXT,epoch INTEGER,PRIMARY KEY(tenant_id,workspace_id));
                CREATE TABLE IF NOT EXISTS collaboration_projection_checkpoints(tenant_id TEXT,workspace_id TEXT,projection_name TEXT,checkpoint INTEGER,state_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,projection_name));
                CREATE TABLE IF NOT EXISTS collaboration_search_documents(tenant_id TEXT,workspace_id TEXT,event_id TEXT,room_id TEXT,sequence INTEGER,document_text TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,event_id));
                CREATE INDEX IF NOT EXISTS collaboration_search_text_scope ON collaboration_search_documents(tenant_id,workspace_id,sequence);
                CREATE TABLE IF NOT EXISTS collaboration_search_manifests(tenant_id TEXT,workspace_id TEXT,checkpoint INTEGER,index_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id));
                """
            )
            self._migrate_event_columns(connection)
            self._migrate_delivery_columns(connection)
            self._backfill_room_access(connection)
            self._backfill_room_lifecycle(connection)
            connection.execute(
                "INSERT INTO collaboration_security_epochs(tenant_id,workspace_id,epoch) "
                "SELECT tenant_id,workspace_id,1 FROM collaboration_workspaces WHERE NOT EXISTS "
                "(SELECT 1 FROM collaboration_security_epochs e WHERE e.tenant_id=collaboration_workspaces.tenant_id "
                "AND e.workspace_id=collaboration_workspaces.workspace_id)"
            )

    @staticmethod
    def _migrate_event_columns(connection: sqlite3.Connection) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(collaboration_events)")}
        definitions = {
            "room_id": "TEXT",
            "thread_id": "TEXT",
            "event_type": "TEXT",
            "actor_binding_id": "TEXT",
            "causation_id": "TEXT",
            "visibility": "TEXT",
            "occurred_at": "REAL",
            "admitted_at": "REAL",
        }
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE collaboration_events ADD COLUMN {name} {definition}")
        rows = connection.execute(
            "SELECT tenant_id,workspace_id,sequence,payload_json FROM collaboration_events "
            "WHERE event_type IS NULL OR visibility IS NULL OR admitted_at IS NULL OR occurred_at IS NULL"
        ).fetchall()
        for tenant_id, workspace_id, sequence, payload_json in rows:
            payload = json.loads(payload_json)
            admitted_at = payload.get("admitted_at", payload.get("occurred_at", 0.0))
            payload["admitted_at"] = float(admitted_at)
            connection.execute(
                "UPDATE collaboration_events SET room_id=?,thread_id=?,event_type=?,actor_binding_id=?,"
                "causation_id=?,visibility=?,occurred_at=?,admitted_at=?,payload_json=? "
                "WHERE tenant_id=? AND workspace_id=? AND sequence=?",
                (
                    payload.get("room_id"),
                    payload.get("thread_id"),
                    payload.get("event_type"),
                    payload.get("actor_binding_id"),
                    payload.get("causation_id"),
                    payload.get("visibility", "workspace"),
                    float(payload.get("occurred_at", admitted_at)),
                    float(admitted_at),
                    canonical_json(payload),
                    tenant_id,
                    workspace_id,
                    sequence,
                ),
            )

    @staticmethod
    def _backfill_room_access(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO collaboration_room_access(tenant_id,workspace_id,room_id,access_mode,revision) "
            "SELECT r.tenant_id,r.workspace_id,r.room_id,'workspace',1 FROM collaboration_rooms r "
            "WHERE NOT EXISTS (SELECT 1 FROM collaboration_room_access a WHERE a.tenant_id=r.tenant_id "
            "AND a.workspace_id=r.workspace_id AND a.room_id=r.room_id)"
        )

    @staticmethod
    def _backfill_room_lifecycle(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT r.tenant_id,r.workspace_id,r.room_id FROM collaboration_rooms r WHERE NOT EXISTS "
            "(SELECT 1 FROM collaboration_room_lifecycle l WHERE l.tenant_id=r.tenant_id "
            "AND l.workspace_id=r.workspace_id AND l.room_id=r.room_id)"
        ).fetchall()
        for tenant_id, workspace_id, room_id in rows:
            connection.execute(
                "INSERT INTO collaboration_room_lifecycle(tenant_id,workspace_id,room_id,state,revision,"
                "snapshot_digest,payload_json) VALUES(?,?,?,?,?,?,?)",
                (
                    tenant_id,
                    workspace_id,
                    room_id,
                    "active",
                    1,
                    None,
                    canonical_json({"room_id": room_id, "state": "active", "revision": 1}),
                ),
            )

    @staticmethod
    def _migrate_delivery_columns(connection: sqlite3.Connection) -> None:
        tables = {
            "collaboration_outbox": {
                "attempt_id": "TEXT",
                "attempts": "INTEGER NOT NULL DEFAULT 0",
                "leased_by": "TEXT",
                "lease_expires_at": "REAL NOT NULL DEFAULT 0",
                "next_attempt_at": "REAL NOT NULL DEFAULT 0",
                "last_error_code": "TEXT",
                "completed_at": "REAL",
            },
            "collaboration_inbox": {
                "origin": "TEXT",
                "mapping_version": "TEXT",
                "admitted_at": "REAL",
            },
        }
        for table, definitions in tables.items():
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for name, definition in definitions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    def backup_to(self, destination: str | Path) -> dict[str, Any]:
        target = Path(destination)
        if not target.name or target.resolve() == self._path.resolve():
            raise ValueError("collaboration_backup_destination_invalid")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction, self._connect() as source, sqlite3.connect(target) as backup:
            source.backup(backup)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        with sqlite3.connect(target) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            sequence = int(
                connection.execute("SELECT COALESCE(MAX(sequence),0) FROM collaboration_events").fetchone()[0]
            )
        if integrity != "ok":
            raise RuntimeError("collaboration_backup_integrity_failed")
        return {
            "schema": "ananta.collaboration-backup.v1",
            "filename": target.name,
            "digest": digest,
            "event_sequence": sequence,
            "integrity": "ok",
        }

    def restore_from(self, source: str | Path, *, expected_digest: str) -> dict[str, Any]:
        backup = Path(source)
        if not backup.is_file() or hashlib.sha256(backup.read_bytes()).hexdigest() != expected_digest:
            raise ValueError("collaboration_restore_digest_mismatch")
        with sqlite3.connect(backup) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("collaboration_restore_integrity_failed")
            required = {"collaboration_workspaces", "collaboration_events", "collaboration_memberships"}
            tables = {
                row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if not required <= tables:
                raise ValueError("collaboration_restore_schema_invalid")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".restore", dir=self._path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        rollback = self._path.with_suffix(f"{self._path.suffix}.rollback")
        sidecars = [
            (Path(f"{self._path}-wal"), Path(f"{rollback}-wal")),
            (Path(f"{self._path}-shm"), Path(f"{rollback}-shm")),
        ]
        try:
            with sqlite3.connect(backup) as source_connection, sqlite3.connect(temporary) as target_connection:
                source_connection.backup(target_connection)
            with self._transaction:
                if self._path.exists():
                    os.replace(self._path, rollback)
                for current_sidecar, rollback_sidecar in sidecars:
                    if current_sidecar.exists():
                        os.replace(current_sidecar, rollback_sidecar)
                os.replace(temporary, self._path)
            self._initialize()
        except Exception:
            temporary.unlink(missing_ok=True)
            if rollback.exists() and not self._path.exists():
                os.replace(rollback, self._path)
                for current_sidecar, rollback_sidecar in sidecars:
                    if rollback_sidecar.exists():
                        os.replace(rollback_sidecar, current_sidecar)
            raise
        return {
            "schema": "ananta.collaboration-restore.v1",
            "digest": expected_digest,
            "rollback_filename": rollback.name if rollback.exists() else None,
            "restored": True,
        }


__all__ = ["CollaborationStoreConflict", "CollaborationWorkspaceStore"]
