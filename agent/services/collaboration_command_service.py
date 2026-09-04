"""Bounded, auditable and fully headless command decisions for collaboration rooms."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import canonical_digest, require_digest, require_id


class HeadlessCommandPolicy(Protocol):
    @property
    def revision(self) -> int: ...

    def decide(self, request: Mapping[str, Any]) -> tuple[bool, str]: ...


@dataclass(frozen=True, slots=True)
class PreauthorizedCommandPolicy:
    """Explicit Hub policy. An empty allowlist deterministically denies all commands."""

    allowed_tool_ids: frozenset[str] = frozenset()
    revision: int = 1

    def decide(self, request: Mapping[str, Any]) -> tuple[bool, str]:
        if request["tool_id"] in self.allowed_tool_ids:
            return True, "command_pre_authorized"
        return False, "command_policy_blocked"


class CollaborationCommandService:
    REQUIRED_FIELDS = {
        "request_id",
        "workspace_id",
        "room_id",
        "actor_binding_id",
        "task_id",
        "tool_id",
        "operation",
        "plan_digest",
        "artifact_digest",
        "policy_revision",
    }

    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        workspace_policy: CollaborationWorkspacePolicy,
        command_policy: HeadlessCommandPolicy,
    ) -> None:
        self._store = store
        self._workspace_policy = workspace_policy
        self._command_policy = command_policy

    def decide(
        self,
        *,
        tenant_id: str,
        principal_actor_id: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        if set(request) != self.REQUIRED_FIELDS:
            raise ValueError("collaboration_command_request_fields_invalid")
        workspace_id = require_id(request.get("workspace_id"), "workspace_id")
        room_id = require_id(request.get("room_id"), "room_id")
        actor_id = require_id(request.get("actor_binding_id"), "actor_binding_id")
        if actor_id != principal_actor_id:
            raise PermissionError("collaboration_command_actor_mismatch")
        self._workspace_policy.require(
            self._store.membership(tenant_id, workspace_id, principal_actor_id), "event.write"
        )
        if not self._store.room_visible(tenant_id, workspace_id, room_id, principal_actor_id):
            raise PermissionError("collaboration_room_visibility_denied")
        policy_revision = request.get("policy_revision")
        if (
            not isinstance(policy_revision, int)
            or isinstance(policy_revision, bool)
            or policy_revision != self._command_policy.revision
        ):
            raise PermissionError("collaboration_command_policy_revision_stale")
        normalized = {
            **dict(request),
            "request_id": require_id(request.get("request_id"), "request_id"),
            "task_id": require_id(request.get("task_id"), "task_id"),
            "tool_id": require_id(request.get("tool_id"), "tool_id"),
            "operation": require_id(request.get("operation"), "operation"),
            "plan_digest": require_digest(request.get("plan_digest"), "plan_digest"),
            "artifact_digest": require_digest(request.get("artifact_digest"), "artifact_digest")
            if request.get("artifact_digest") is not None
            else None,
        }
        allowed, reason_code = self._command_policy.decide(normalized)
        decision = {
            **normalized,
            "state": "approved" if allowed else "blocked",
            "reason_code": require_id(reason_code, "command_reason_code"),
            "binding_digest": canonical_digest(normalized),
            "human_intervention_required": False,
            "terminal": True,
        }
        persisted, replayed = self._store.record_command_decision(tenant_id, decision)
        return {**persisted, "replayed": replayed}


__all__ = ["CollaborationCommandService", "HeadlessCommandPolicy", "PreauthorizedCommandPolicy"]
