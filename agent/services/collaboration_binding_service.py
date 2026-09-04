"""Hub-validated domain and branch bindings for collaboration rooms."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationStoreConflict, CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import CollaborationRoomV1, canonical_digest, require_id

_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_REF = re.compile(r"^(?![./])(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]{1,255}(?<![./])$")


class CollaborationBindingAuthority(Protocol):
    def verify(self, *, tenant_id: str, principal_actor_id: str, binding: Mapping[str, Any]) -> Mapping[str, Any]: ...


class CollaborationBindingService:
    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        policy: CollaborationWorkspacePolicy,
        authority: CollaborationBindingAuthority,
    ) -> None:
        self._store = store
        self._policy = policy
        self._authority = authority

    def bind_room(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        principal_actor_id: str,
        binding: Mapping[str, Any],
        expected_revision: int,
    ) -> dict[str, Any]:
        self._policy.require(self._store.membership(tenant_id, workspace_id, principal_actor_id), "room.manage")
        normalized = self._normalize(binding)
        value = self._verified(
            tenant_id=tenant_id,
            principal_actor_id=principal_actor_id,
            binding=normalized,
        )
        return self._store.put_room_binding(
            tenant_id,
            workspace_id,
            room_id,
            value,
            expected_revision=expected_revision,
        )

    def create_branch_room(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        title: str,
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = self._normalize(binding)
        if normalized["binding_kind"] != "branch":
            raise ValueError("collaboration_branch_binding_required")
        self._policy.require(self._store.membership(tenant_id, workspace_id, principal_actor_id), "room.manage")
        verified = self._verified(
            tenant_id=tenant_id,
            principal_actor_id=principal_actor_id,
            binding=normalized,
        )
        existing = self._store.room_for_binding(tenant_id, workspace_id, "branch", normalized["binding_id"])
        if existing:
            return {**existing, "replayed": True}
        room_id = f"branch-{canonical_digest(normalized)[:24]}"
        room = CollaborationRoomV1.from_mapping(
            {
                "schema": CollaborationRoomV1.SCHEMA,
                "room_id": room_id,
                "room_kind": "branch",
                "title": str(title or "").strip(),
                "binding_kind": "branch",
                "binding_id": normalized["binding_id"],
            }
        )
        try:
            self._store.add_room(tenant_id, workspace_id, room.to_dict())
        except CollaborationStoreConflict:
            existing = self._store.room_for_binding(tenant_id, workspace_id, "branch", normalized["binding_id"])
            if existing:
                return {**existing, "replayed": True}
            raise
        return self._store.put_room_binding(
            tenant_id,
            workspace_id,
            room_id,
            verified,
            expected_revision=0,
        )

    def reconcile_room(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        room_id: str,
        principal_actor_id: str,
    ) -> dict[str, Any]:
        self._policy.require(self._store.membership(tenant_id, workspace_id, principal_actor_id), "room.manage")
        stored = self._store.room_binding(tenant_id, workspace_id, room_id)
        if stored is None:
            raise KeyError("collaboration_room_binding_not_found")
        request = {
            "binding_kind": stored["binding_kind"],
            "binding_id": stored["binding_id"],
            "project_id": stored["project_id"],
            "lifecycle": stored["lifecycle"],
            "revision": stored["authoritative_revision"],
            "metadata": stored["metadata"],
        }
        decision = self._authority_decision(
            tenant_id=tenant_id,
            principal_actor_id=principal_actor_id,
            binding=request,
        )
        if decision["verified"] is True:
            return {"state": "current", "room_id": room_id, "retention_action": "none"}
        reason = decision["reason_code"]
        lifecycle = decision.get("authoritative_lifecycle")
        if reason == "collaboration_binding_revision_stale" and lifecycle == "active":
            updated = {
                **request,
                "revision": decision["authoritative_revision"],
                "lifecycle": "active",
                "authority_reason_code": "collaboration_binding_verified",
                "authoritative_revision": decision["authoritative_revision"],
            }
            value = self._store.put_room_binding(
                tenant_id,
                workspace_id,
                room_id,
                updated,
                expected_revision=stored["revision"],
            )
            return {"state": "updated", "room_id": room_id, "binding": value, "retention_action": "none"}
        if reason == "collaboration_binding_not_found" or lifecycle in {"archived", "deleted"}:
            room_state = self._store.room_lifecycle(tenant_id, workspace_id, room_id)
            if room_state["state"] != "archived":
                room_state = self._store.transition_room(
                    tenant_id,
                    workspace_id,
                    room_id,
                    target_state="archived",
                    expected_revision=room_state["revision"],
                )
            return {
                "state": "archived",
                "room_id": room_id,
                "room": room_state,
                "retention_action": "retain_canonical_history",
                "reason_code": "collaboration_bound_object_unavailable",
            }
        raise PermissionError(reason)

    def _verified(
        self,
        *,
        tenant_id: str,
        principal_actor_id: str,
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        decision = self._authority_decision(
            tenant_id=tenant_id,
            principal_actor_id=principal_actor_id,
            binding=binding,
        )
        if decision["verified"] is not True:
            raise PermissionError(require_id(decision["reason_code"], "binding_reason_code"))
        return {
            **dict(binding),
            "authority_reason_code": require_id(decision["reason_code"], "binding_reason_code"),
            "authoritative_revision": require_id(decision["authoritative_revision"], "authoritative_revision"),
        }

    def _authority_decision(
        self,
        *,
        tenant_id: str,
        principal_actor_id: str,
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        decision = dict(
            self._authority.verify(
                tenant_id=tenant_id,
                principal_actor_id=principal_actor_id,
                binding=binding,
            )
        )
        required = {"verified", "reason_code", "authoritative_revision"}
        if not required.issubset(decision) or set(decision) - (required | {"authoritative_lifecycle"}):
            raise ValueError("collaboration_binding_authority_response_invalid")
        if not isinstance(decision["verified"], bool):
            raise ValueError("collaboration_binding_authority_response_invalid")
        require_id(decision["reason_code"], "binding_reason_code")
        require_id(decision["authoritative_revision"], "authoritative_revision")
        if decision.get("authoritative_lifecycle") not in {None, "active", "archived", "deleted"}:
            raise ValueError("collaboration_binding_authority_response_invalid")
        return decision

    @staticmethod
    def _normalize(binding: Mapping[str, Any]) -> dict[str, Any]:
        required = {"binding_kind", "binding_id", "project_id", "lifecycle", "revision", "metadata"}
        if set(binding) != required or binding.get("binding_kind") not in {
            "organization",
            "project",
            "goal",
            "task",
            "branch",
        }:
            raise ValueError("collaboration_domain_binding_invalid")
        if binding.get("lifecycle") not in {"active", "archived", "deleted"}:
            raise ValueError("collaboration_domain_binding_lifecycle_invalid")
        metadata = binding.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("collaboration_domain_binding_metadata_invalid")
        normalized = {
            "binding_kind": str(binding["binding_kind"]),
            "binding_id": require_id(binding["binding_id"], "binding_id"),
            "project_id": require_id(binding["project_id"], "project_id"),
            "lifecycle": str(binding["lifecycle"]),
            "revision": require_id(binding["revision"], "binding_revision"),
            "metadata": dict(metadata),
        }
        if normalized["binding_kind"] == "branch":
            expected = {"repository_id", "remote", "branch_ref", "base_ref", "head_sha", "fork_id", "change"}
            if set(metadata) != expected:
                raise ValueError("collaboration_branch_binding_fields_invalid")
            for key in ("repository_id", "remote"):
                require_id(metadata[key], key)
            for key in ("branch_ref", "base_ref"):
                if not _REF.fullmatch(str(metadata[key] or "")):
                    raise ValueError("collaboration_git_ref_invalid")
            if not _SHA.fullmatch(str(metadata["head_sha"] or "").lower()):
                raise ValueError("collaboration_git_head_invalid")
            if metadata["fork_id"] is not None:
                require_id(metadata["fork_id"], "fork_id")
            if metadata["change"] not in {"create", "update", "rename", "delete", "force_push"}:
                raise ValueError("collaboration_git_change_invalid")
        return normalized


__all__ = ["CollaborationBindingAuthority", "CollaborationBindingService"]
