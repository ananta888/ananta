"""Hub-owned, one-shot approval bindings for scientific skill mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent.common.audit import log_audit
from agent.services.approval_request_service import ApprovalRequestService

_TOOL_NAME = "scientific_skill.controlled_execution"


@dataclass(frozen=True)
class ScientificSkillExecutionIntent:
    task_id: str
    catalog_digest: str
    entry_id: str
    upstream_pin: str
    skill_sha256: str
    action: str
    target: str

    def arguments(self) -> dict[str, str]:
        return {
            "catalog_digest": self.catalog_digest,
            "entry_id": self.entry_id,
            "upstream_pin": self.upstream_pin,
            "skill_sha256": self.skill_sha256,
            "action": self.action,
            "target": self.target,
        }


@dataclass(frozen=True)
class ScientificSkillExecutionApproval:
    approved: bool
    reason_code: str
    request_id: str | None = None


class ScientificSkillExecutionApprovalService:
    """Bridges scientific execution intents to the existing Hub approval store."""

    def __init__(
        self,
        approvals: ApprovalRequestService,
        audit_sink: Callable[[str, dict], None] = log_audit,
    ) -> None:
        self._approvals = approvals
        self._audit = audit_sink

    def request(self, intent: ScientificSkillExecutionIntent, *, ttl_seconds: int = 900) -> str:
        request = self._approvals.create_pending_request(
            task_id=intent.task_id,
            tool_name=_TOOL_NAME,
            arguments=intent.arguments(),
            target_fingerprint=intent.target,
            risk_class="high",
            ttl_seconds=ttl_seconds,
            # Scientific external actions never use the generic auto-grant
            # policy. A granted request is always an explicit Hub decision.
            agent_cfg={"approval_lifecycle": {"human_required_tools": [_TOOL_NAME]}},
            scope={
                "approval_class": "scientific_skill_external_action",
                "catalog_digest": intent.catalog_digest,
                "entry_id": intent.entry_id,
                "upstream_pin": intent.upstream_pin,
                "target": intent.target,
            },
        )
        self._audit("scientific_skill_approval_requested", _audit_payload(intent, request.id, "pending"))
        return request.id

    def consume(self, intent: ScientificSkillExecutionIntent) -> ScientificSkillExecutionApproval:
        grant = self._approvals.resolve_granted_request(
            task_id=intent.task_id,
            goal_id=None,
            tool_name=_TOOL_NAME,
            arguments=intent.arguments(),
            target_fingerprint=intent.target,
        )
        if grant is None:
            self._audit("scientific_skill_approval_rejected", _audit_payload(intent, None, "not_granted"))
            return ScientificSkillExecutionApproval(False, "scientific_skill_approval_not_granted")
        consumed = self._approvals.consume_request(grant.id)
        if consumed is None or consumed.status != "consumed":
            self._audit("scientific_skill_approval_rejected", _audit_payload(intent, grant.id, "replay"))
            return ScientificSkillExecutionApproval(False, "scientific_skill_approval_replay_rejected", grant.id)
        self._audit("scientific_skill_approval_consumed", _audit_payload(intent, grant.id, "consumed"))
        return ScientificSkillExecutionApproval(True, "scientific_skill_approval_granted", grant.id)


def _audit_payload(intent: ScientificSkillExecutionIntent, request_id: str | None, status: str) -> dict[str, str | None]:
    return {
        "task_id": intent.task_id,
        "catalog_digest": intent.catalog_digest,
        "entry_id": intent.entry_id,
        "upstream_pin": intent.upstream_pin,
        "skill_sha256": intent.skill_sha256,
        "action": intent.action,
        "target": intent.target,
        "request_id": request_id,
        "status": status,
    }


__all__ = [
    "ScientificSkillExecutionApproval",
    "ScientificSkillExecutionApprovalService",
    "ScientificSkillExecutionIntent",
]
