"""Hub control-plane facade for expert-bank status and governed commands."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class KnowledgeExpertControlPort(Protocol):
    def snapshot(self, *, tenant_id: str) -> Mapping[str, Any]: ...

    def command(
        self,
        *,
        tenant_id: str,
        action: str,
        bank_id: str,
        generation_id: str,
        expected_generation_id: str,
        reason: str,
    ) -> Mapping[str, Any]: ...


class KnowledgeExpertControlService:
    """Validate operator intent before invoking a Hub-owned orchestration port."""

    def __init__(self, delegate: KnowledgeExpertControlPort, *, enabled: bool) -> None:
        self._delegate = delegate
        self._enabled = bool(enabled)

    def snapshot(self, *, tenant_id: str) -> dict[str, Any]:
        raw = dict(self._delegate.snapshot(tenant_id=tenant_id))
        return {
            "schema": "ananta.knowledge-expert-control-snapshot.v1",
            "enabled": self._enabled,
            "rollout_state": str(raw.get("rollout_state") or "blocked"),
            "active_banks": list(raw.get("active_banks") or []),
            "candidate_banks": list(raw.get("candidate_banks") or []),
            "gates": dict(raw.get("gates") or {}),
            "fallback_mode": "rag_only",
        }

    def command(
        self,
        *,
        tenant_id: str,
        action: str,
        bank_id: str,
        generation_id: str,
        expected_generation_id: str,
        reason: str,
    ) -> dict[str, Any]:
        if action not in {"activate", "rollback", "revoke", "disable"}:
            raise ValueError("knowledge_expert_control_action_invalid")
        if not all((tenant_id.strip(), bank_id.strip(), reason.strip())) or len(reason) > 1000:
            raise ValueError("knowledge_expert_control_binding_invalid")
        if action != "disable" and not self._enabled:
            raise ValueError("knowledge_expert_control_disabled")
        if action in {"activate", "rollback"} and not generation_id.strip():
            raise ValueError("knowledge_expert_control_generation_required")
        result = dict(
            self._delegate.command(
                tenant_id=tenant_id,
                action=action,
                bank_id=bank_id,
                generation_id=generation_id,
                expected_generation_id=expected_generation_id,
                reason=reason,
            )
        )
        return {
            "schema": "ananta.knowledge-expert-control-result.v1",
            "action": action,
            "reason_code": str(result.get("reason_code") or "command_submitted"),
            "task_id": str(result.get("task_id") or ""),
            "activation_performed_by_worker": False,
        }


__all__ = ["KnowledgeExpertControlPort", "KnowledgeExpertControlService"]
