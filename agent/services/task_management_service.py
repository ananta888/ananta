from __future__ import annotations

import time
import uuid
from typing import Any

from flask import current_app, g

from agent.common.audit import log_audit
from agent.metrics import TASK_RECEIVED
from agent.research_backend import resolve_research_backend_config
from agent.routes.tasks.dependency_policy import followup_exists, normalize_depends_on, validate_dependencies_and_cycles
from agent.routes.tasks.orchestration_policy import (
    derive_required_capabilities,
    enforce_assignment_policy,
    evaluate_worker_routing_policy,
    persist_policy_decision,
)
from agent.services.task_queue_service import get_task_queue_service
from agent.services.repository_registry import get_repository_registry
from agent.services.task_runtime_service import get_local_task_status, update_local_task_status
from agent.services.task_status_service import normalize_task_status


class TaskManagementService:
    """Hub-owned task management use-cases for mutation-heavy task endpoints."""

    def actor_username(self) -> str:
        user = getattr(g, "user", {}) or {}
        return str(user.get("sub") or user.get("username") or "system")

    def derivation_backfill(self) -> dict[str, Any]:
        repos = get_repository_registry()
        active = [t.model_dump() for t in repos.task_repo.get_all()]
        by_id = {t["id"]: t for t in active}
        updated_ids: list[str] = []

        def _depth(task_id: str) -> int:
            depth = 0
            seen = {task_id}
            current = by_id.get(task_id, {})
            while current and current.get("parent_task_id"):
                pid = str(current.get("parent_task_id"))
                if pid in seen:
                    break
                seen.add(pid)
                depth += 1
                current = by_id.get(pid, {})
            return depth

        for item in active:
            parent_id = str(item.get("parent_task_id") or "").strip()
            if not parent_id:
                continue
            source_task_id = str(item.get("source_task_id") or "").strip() or parent_id
            derivation_reason = str(item.get("derivation_reason") or "").strip() or "parent_link_backfill"
            derivation_depth = int(item.get("derivation_depth") or _depth(item["id"]))
            update_local_task_status(
                item["id"],
                item.get("status") or "todo",
                source_task_id=source_task_id,
                derivation_reason=derivation_reason,
                derivation_depth=derivation_depth,
            )
            updated_ids.append(item["id"])
        return {"updated_count": len(updated_ids), "updated_ids": updated_ids}

    def create_task(self, *, data: Any, source: str, created_by: str) -> dict[str, Any]:
        task_id = data.id or str(uuid.uuid4())
        status = normalize_task_status(data.status, default="created")
        safe_data = {k: v for k, v in data.model_dump().items() if v is not None and k not in ["id", "status"]}
        safe_data["depends_on"] = normalize_depends_on(safe_data.get("depends_on"), tid=task_id)
        ok, reason = validate_dependencies_and_cycles(task_id, safe_data.get("depends_on") or [])
        if not ok:
            return {"error": reason, "code": 400}
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status=status,
            title=safe_data.pop("title", None),
            description=safe_data.pop("description", None),
            priority=str(safe_data.pop("priority", "medium")),
            created_by=created_by,
            source=source,
            team_id=safe_data.pop("team_id", None),
            tags=safe_data.pop("tags", None),
            event_type="task_ingested",
            event_channel="central_task_management",
            extra_fields=safe_data,
        )
        TASK_RECEIVED.inc()
        return {"data": {"id": task_id, "status": "created"}, "code": 201}

    def patch_task(self, *, task_id: str, data: Any) -> dict[str, Any]:
        update_data = {k: v for k, v in data.model_dump().items() if v is not None}
        status = normalize_task_status(update_data.pop("status", None), default="updated")
        if "depends_on" in update_data:
            update_data["depends_on"] = normalize_depends_on(update_data.get("depends_on"), tid=task_id)
            ok, reason = validate_dependencies_and_cycles(task_id, update_data.get("depends_on") or [])
            if not ok:
                return {"error": reason, "code": 400}
        update_local_task_status(task_id, status, **update_data)
        return {"data": {"id": task_id, "status": "updated"}}

    def review_task_proposal(self, *, task_id: str, action: str, comment: str | None) -> dict[str, Any]:
        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}
        proposal = dict(task.get("last_proposal") or {})
        research_artifact = proposal.get("research_artifact")
        if not isinstance(research_artifact, dict):
            return {"error": "no_research_artifact", "code": 400}

        review = dict(proposal.get("review") or {})
        review.update(
            {
                "status": "approved" if action == "approve" else "rejected",
                "reviewed_by": self.actor_username(),
                "reviewed_at": time.time(),
                "comment": comment,
            }
        )
        proposal["review"] = review

        history = list(task.get("history") or [])
        history.append(
            {
                "event_type": "proposal_review",
                "action": action,
                "actor": self.actor_username(),
                "comment": comment,
                "backend": proposal.get("backend"),
                "artifact_kind": research_artifact.get("kind"),
                "timestamp": time.time(),
            }
        )

        new_status = "blocked" if action == "reject" else normalize_task_status(task.get("status"), default="proposing")
        update_local_task_status(
            task_id,
            new_status,
            last_proposal=proposal,
            history=history,
            manual_override_until=time.time() + 600,
        )
        log_audit("task_proposal_reviewed", {"task_id": task_id, "action": action, "actor": self.actor_username()})
        return {"data": {"id": task_id, "review": review, "status": new_status}}

    def assign_task(self, *, task_id: str, data: Any) -> dict[str, Any]:
        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}
        can_assign, reasons, _worker = enforce_assignment_policy(
            task,
            data.agent_url,
            task_kind=data.task_kind,
            required_capabilities=data.required_capabilities,
        )
        decision_status = "approved" if can_assign else "blocked"
        persist_policy_decision(
            decision_type="assignment",
            status=decision_status,
            policy_name="worker_assignment_policy",
            policy_version="assignment-v1",
            reasons=reasons,
            details={
                "task_kind": data.task_kind,
                "required_capabilities": data.required_capabilities,
                "manual_override": True,
            },
            task_id=task_id,
            worker_url=data.agent_url,
        )
        if not can_assign:
            return {"error": "assignment_policy_blocked", "code": 409, "data": {"reasons": reasons}}
        update_local_task_status(
            task_id,
            "assigned",
            assigned_agent_url=data.agent_url,
            assigned_agent_token=data.token,
            manual_override_until=time.time() + 600,
            task_kind=data.task_kind or task.get("task_kind"),
            required_capabilities=data.required_capabilities or task.get("required_capabilities"),
            event_type="task_assigned",
            event_actor="system",
            event_details={"agent_url": data.agent_url, "policy_reasons": reasons},
        )
        return {"data": {"status": "assigned", "agent_url": data.agent_url}}

    def auto_assign_task(self, *, task_id: str, payload: dict[str, Any], agent_registry_service, worker_contract_service) -> dict[str, Any]:
        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}
        effective_task_kind = payload.get("task_kind") or task.get("task_kind")
        effective_required_capabilities = payload.get("required_capabilities") or task.get("required_capabilities") or derive_required_capabilities(
            task,
            effective_task_kind,
        )
        preferred_backend = (
            resolve_research_backend_config(agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {}).get("provider")
            if str(effective_task_kind or "").strip().lower() == "research"
            else None
        )
        repos = get_repository_registry()
        selection, _decision = evaluate_worker_routing_policy(
            task=task,
            workers=[
                agent_registry_service.build_directory_entry(agent=worker, timeout=300)
                for worker in repos.agent_repo.get_all()
            ],
            decision_type="assignment",
            task_kind=effective_task_kind,
            required_capabilities=effective_required_capabilities,
            task_id=task_id,
        )
        if not selection.worker_url:
            return {"error": "no_worker_available", "code": 409, "data": {"reasons": selection.reasons}}
        update_local_task_status(
            task_id,
            "assigned",
            assigned_agent_url=selection.worker_url,
            manual_override_until=time.time() + 600,
            task_kind=effective_task_kind,
            required_capabilities=effective_required_capabilities,
            event_type="task_assigned",
            event_actor="system",
            event_details={
                "agent_url": selection.worker_url,
                "selection_strategy": selection.strategy,
                "reasons": selection.reasons,
            },
        )
        return {
            "data": {
                "status": "assigned",
                "agent_url": selection.worker_url,
                "selected_by_policy": True,
                "selection_reasons": selection.reasons,
                "worker_selection": worker_contract_service.build_routing_decision(
                    agent_url=selection.worker_url,
                    selected_by_policy=True,
                    task_kind=effective_task_kind,
                    required_capabilities=effective_required_capabilities,
                    selection=selection,
                    preferred_backend=preferred_backend,
                ),
            }
        }

    def unassign_task(self, *, task_id: str) -> dict[str, Any]:
        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}
        update_local_task_status(
            task_id,
            "todo",
            assigned_agent_url=None,
            assigned_agent_token=None,
            assigned_to=None,
            manual_override_until=time.time() + 600,
        )
        return {"data": {"status": "todo", "unassigned": True}}

    def subtask_callback(self, *, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        subtask_id = payload.get("id")
        new_status = payload.get("status")
        if not subtask_id or not new_status:
            return {"error": "invalid_payload", "code": 400}
        parent_task = get_local_task_status(task_id)
        if not parent_task:
            return {"error": "parent_task_not_found", "code": 404}
        subtasks = list(parent_task.get("subtasks") or [])
        updated = False
        for item in subtasks:
            if item.get("id") == subtask_id:
                item["status"] = new_status
                if "last_output" in payload:
                    item["last_output"] = payload["last_output"]
                if "last_exit_code" in payload:
                    item["last_exit_code"] = payload["last_exit_code"]
                updated = True
                break
        if not updated:
            return {"error": "subtask_not_found", "code": 404}
        update_local_task_status(task_id, parent_task.get("status", "in_progress"), subtasks=subtasks)
        return {"data": {"status": "updated"}}

    def create_followups(self, *, task_id: str, data: Any) -> dict[str, Any]:
        parent_task = get_local_task_status(task_id)
        if not parent_task:
            return {"error": "parent_task_not_found", "code": 404}

        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        parent_done = normalize_task_status(parent_task.get("status")) == "completed"
        for item in data.items:
            desc = (item.description or "").strip()
            if not desc:
                skipped.append({"reason": "empty_description"})
                continue
            if followup_exists(task_id, desc):
                skipped.append({"description": desc, "reason": "duplicate"})
                continue

            subtask_id = f"sub-{uuid.uuid4()}"
            status = "todo" if parent_done else "blocked"
            create_payload = {
                "id": subtask_id,
                "description": desc,
                "priority": item.priority or "Medium",
                "parent_task_id": task_id,
                "source_task_id": task_id,
                "derivation_reason": "manual_followup",
                "derivation_depth": int(parent_task.get("derivation_depth") or 0) + 1,
            }

            update_local_task_status(subtask_id, status, **create_payload)
            if item.agent_url:
                update_local_task_status(
                    subtask_id,
                    "assigned" if status != "blocked" else "blocked",
                    assigned_agent_url=item.agent_url,
                    assigned_agent_token=item.agent_token,
                )

            created.append(
                {
                    "id": subtask_id,
                    "status": status,
                    "parent_task_id": task_id,
                    "description": desc,
                    "assigned_agent_url": item.agent_url,
                }
            )
        return {"data": {"parent_task_id": task_id, "created": created, "skipped": skipped}}


task_management_service = TaskManagementService()


def get_task_management_service() -> TaskManagementService:
    return task_management_service
