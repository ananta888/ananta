"""Workspace, room, membership, identity and resource-offer persistence."""

# SQL statements retain complete column/key declarations on one line for auditability.
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from agent.services.collaboration_workspace_store_contracts import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import canonical_digest, canonical_json, require_id


class CollaborationWorkspaceCatalogStoreMixin:
    """Persist collaboration catalog and identity state."""

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

    def room_binding(self, tenant_id: str, workspace_id: str, room_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collaboration_room_bindings WHERE tenant_id=? AND workspace_id=? "
                "AND room_id=?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(workspace_id, "workspace_id"),
                    require_id(room_id, "room_id"),
                ),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def room_lifecycle(self, tenant_id: str, workspace_id: str, room_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collaboration_room_lifecycle WHERE tenant_id=? AND workspace_id=? "
                "AND room_id=?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(workspace_id, "workspace_id"),
                    require_id(room_id, "room_id"),
                ),
            ).fetchone()
        if row is None:
            raise KeyError("collaboration_room_not_found")
        return json.loads(row[0])

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

    def resource_offers(
        self, tenant_id: str, workspace_id: str, *, now: float, limit: int = 100
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("collaboration_resource_offer_limit_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM collaboration_resource_offers WHERE tenant_id=? AND workspace_id=? "
                "AND status='active' AND expires_at>? ORDER BY offer_id LIMIT ?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(workspace_id, "workspace_id"),
                    float(now),
                    limit,
                ),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
