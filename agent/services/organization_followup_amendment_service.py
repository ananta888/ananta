from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent.db_models import PlanningAmendmentInputDB
from agent.services.planning_category_contract_service import stable_planning_digest
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)


class OrganizationFollowupAmendmentService:
    """Turn legacy Hub follow-up input into non-executable planning evidence."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
    ) -> None:
        self._uow_factory = uow_factory or PlanningControlUnitOfWork

    def stage_manual_followups(
        self,
        *,
        parent_task: Mapping[str, Any],
        items: Sequence[Any],
        actor: str,
    ) -> dict[str, Any]:
        worker_context = dict(parent_task.get("worker_execution_context") or {})
        lineage = dict(worker_context.get("planning_lineage") or {})
        scope = {
            "tenant_id": str(parent_task.get("tenant_id") or "").strip(),
            "project_id": str(parent_task.get("project_id") or "").strip(),
            "organization_id": str(parent_task.get("organization_id") or "").strip(),
            "goal_id": str(lineage.get("organization_goal_id") or parent_task.get("goal_id") or "").strip(),
            "source_task_id": str(parent_task.get("id") or "").strip(),
        }
        if any(not value for value in scope.values()):
            raise ValueError("organization_followup_planning_binding_required")
        normalized_items = [
            {
                "description": self._value(item, "description")[:10000],
                "priority_hint": self._value(item, "priority")[:32] or "Medium",
                # Direct worker addressing is deliberately not copied.
                "target_hints_authoritative": False,
            }
            for item in items
            if self._value(item, "description")
        ]
        if not normalized_items:
            raise ValueError("organization_followup_items_required")
        payload = {
            "schema": "planning_amendment_input.v1",
            "input_kind": "manual_followup",
            "source_task_id": scope["source_task_id"],
            "source_category_item_ids": list(lineage.get("source_category_item_ids") or []),
            "items": normalized_items,
            "grounding_status": "unverified",
            "next_stage": "research_category_todo",
            "direct_task_creation_authorized": False,
        }
        content_digest = stable_planning_digest(payload)
        idempotency_key = content_digest
        aggregate_key = f"manual-followup:{scope['organization_id']}:{scope['source_task_id']}"
        with planning_scope_lock(aggregate_key), self._uow_factory() as uow:
            assert uow.planning is not None
            uow.planning.acquire_scope_lock(aggregate_key)
            existing = uow.planning.get_amendment_input_by_idempotency(
                organization_id=scope["organization_id"],
                source_task_id=scope["source_task_id"],
                input_kind="manual_followup",
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                if existing.content_digest != content_digest or dict(existing.payload or {}) != payload:
                    raise ValueError("planning_amendment_idempotency_conflict")
                return self._response(existing, replayed=True)
            amendment = PlanningAmendmentInputDB(
                id=self._stable_id(
                    scope["organization_id"],
                    scope["source_task_id"],
                    content_digest,
                ),
                tenant_id=scope["tenant_id"],
                project_id=scope["project_id"],
                organization_id=scope["organization_id"],
                goal_id=scope["goal_id"],
                source_task_id=scope["source_task_id"],
                input_kind="manual_followup",
                idempotency_key=idempotency_key,
                content_digest=content_digest,
                payload=payload,
                state="pending_research",
                created_by=str(actor or "hub"),
            )
            uow.planning.add_amendment_input(amendment)
        return self._response(amendment, replayed=False)

    @staticmethod
    def _value(item: Any, field: str) -> str:
        if isinstance(item, Mapping):
            value = item.get(field)
        else:
            value = getattr(item, field, None)
        return str(value or "").strip()

    @staticmethod
    def _stable_id(*values: str) -> str:
        digest = hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()
        return f"pamd-{digest[:24]}"

    @staticmethod
    def _response(
        amendment: PlanningAmendmentInputDB,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "amendment_input_id": amendment.id,
            "amendment_input_revision": amendment.revision,
            "amendment_input_digest": amendment.content_digest,
            "state": amendment.state,
            "next_stage": "research_category_todo",
            "replayed": replayed,
            "created_task_ids": [],
            "ignored_direct_worker_assignment": True,
        }


__all__ = ["OrganizationFollowupAmendmentService"]
