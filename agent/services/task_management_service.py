from __future__ import annotations

import time
import uuid
from typing import Any

from flask import current_app, g

from agent.common.audit import log_audit
from agent.common.governance_codes import GovernanceReasonCode
from agent.metrics import TASK_RECEIVED
from agent.research_backend import resolve_research_backend_config
from agent.routes.tasks.dependency_policy import followup_exists, normalize_depends_on, validate_dependencies_and_cycles
from agent.routes.tasks.orchestration_policy import (
    derive_required_capabilities,
    enforce_assignment_policy,
    evaluate_worker_routing_policy,
    persist_policy_decision,
)
from agent.services.approval_policy_service import get_approval_policy_service
from agent.services.commit_followup_service import maybe_create_git_commit_followup
from agent.services.context_bundle_ingress_policy import (
    find_reserved_context_bundle_marker,
    preserve_hub_context_bundle_fields,
    reserved_context_bundle_ingress_error,
)
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.execution_risk_policy_service import evaluate_execution_risk
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.mutation_gate_service import get_mutation_gate_service
from agent.services.recovery_task_mutation_policy import (
    RecoveryTaskMutationConflict,
    ensure_external_recovery_mutation_allowed,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.retrieval_vector_scope_ingress_policy import (
    find_reserved_retrieval_vector_scope_marker,
    preserve_hub_retrieval_vector_scope,
    reserved_retrieval_vector_scope_ingress_error,
)
from agent.services.task_queue_service import get_task_queue_service
from agent.services.task_runtime_service import get_local_task_status, update_local_task_status
from agent.services.task_status_service import normalize_task_status
from agent.services.vector_index_task_ingress_policy import (
    find_reserved_vector_index_marker,
    reserved_vector_index_ingress_error,
)
from agent.services.vector_store_authorization_policy import (
    VectorAdminAuthorizationContext,
    get_vector_store_authorization_policy,
    has_reserved_vector_index_marker,
)
from agent.services.vector_task_admin_guard_service import (
    require_authoritative_vector_task,
)


class TaskManagementService:
    """Hub-owned task management use-cases for mutation-heavy task endpoints."""

    def actor_username(self) -> str:
        user = getattr(g, "user", {}) or {}
        return str(user.get("sub") or user.get("username") or "system")

    @staticmethod
    def _vector_admin_error(
        task: Any,
        *,
        authorization: (VectorAdminAuthorizationContext | None),
    ) -> dict[str, Any] | None:
        if not has_reserved_vector_index_marker(task):
            return None
        try:
            get_vector_store_authorization_policy().require_task_admin(
                authorization,
                task,
            )
        except PermissionError as exc:
            reason = str(exc)
            return {
                "error": reason,
                "code": 403,
                "data": {"reason_code": reason},
            }

        try:
            require_authoritative_vector_task(task)
        except ValueError as exc:
            reason = str(exc)
            return {
                "error": reason,
                "code": 409,
                "data": {"reason_code": reason},
            }
        return None

    @staticmethod
    def _recovery_mutation_conflict(
        task: Any,
        *,
        action: str,
    ) -> dict[str, Any] | None:
        try:
            ensure_external_recovery_mutation_allowed(
                task,
                action=action,
            )
        except RecoveryTaskMutationConflict as exc:
            return {
                "error": exc.reason_code,
                "code": 409,
                "data": exc.as_data(),
            }
        return None

    @staticmethod
    def _critical_state_mutation(status: str | None) -> bool:
        return str(status or "").strip().lower() in {"completed", "failed", "blocked", "cancelled"}

    def _enforce_task_state_mutation_gate(
        self,
        *,
        task_id: str,
        requested_status: str | None,
        task: dict | None,
    ) -> tuple[bool, str | None]:
        if not self._critical_state_mutation(requested_status):
            return True, None
        cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        tool_calls = [
            {
                "name": "task_state_update",
                "args": {"task_id": task_id, "to_status": str(requested_status or "").strip().lower()},
            }
        ]
        approval = get_approval_policy_service().evaluate(
            command=None,
            tool_calls=tool_calls,
            task=dict(task or {}),
            agent_cfg=cfg,
        )
        risk = evaluate_execution_risk(
            command=None,
            tool_calls=tool_calls,
            task=dict(task or {}),
            agent_cfg=cfg,
        )
        decision = (
            get_mutation_gate_service()
            .evaluate(
                command=None,
                tool_calls=tool_calls,
                task=dict(task or {}),
                agent_cfg=cfg,
                approval_decision=approval,
                risk_decision=risk,
                trace_id=str((task or {}).get("goal_trace_id") or "").strip() or None,
                actor=self.actor_username(),
            )
            .as_dict()
        )
        get_execution_audit_service().emit(
            operation_type="mutation_gate_decision",
            outcome=str(decision.get("classification") or "unknown"),
            trace_id=str((task or {}).get("goal_trace_id") or "").strip() or None,
            goal_id=(task or {}).get("goal_id"),
            task_id=task_id,
            actor_role="hub",
            details={
                "reason_code": decision.get("reason_code"),
                "mutation_class": decision.get("mutation_class"),
                "normalized_target": decision.get("normalized_target"),
                "approval_scope": decision.get("approval_scope"),
                "source": "task_management_service",
                "requested_status": str(requested_status or "").strip().lower(),
            },
        )
        if decision.get("classification") in {"blocked", "confirm_required"}:
            return False, str(decision.get("reason_code") or "mutation_gate_blocked")
        return True, None

    def _apply_instruction_selection_to_payload(
        self,
        payload: dict[str, Any],
        *,
        default_owner: str | None = None,
    ) -> None:
        owner_username = (
            str(
                payload.pop(
                    "instruction_owner_username",
                    "",
                )
                or ""
            ).strip()
            or default_owner
        )
        profile_id = str(payload.pop("instruction_profile_id", "") or "").strip() or None
        overlay_id = str(payload.pop("instruction_overlay_id", "") or "").strip() or None
        if not owner_username and not profile_id and not overlay_id:
            return
        worker_execution_context = dict(payload.get("worker_execution_context") or {})
        instruction_context = dict(worker_execution_context.get("instruction_context") or {})
        if owner_username:
            instruction_context["owner_username"] = owner_username
        instruction_context["profile_id"] = profile_id
        instruction_context["overlay_id"] = overlay_id
        instruction_context["updated_at"] = time.time()
        worker_execution_context["instruction_context"] = instruction_context
        payload["worker_execution_context"] = worker_execution_context

    def _validate_instruction_selection(self, payload: dict[str, Any]) -> tuple[str | None, int]:
        worker_execution_context = dict(payload.get("worker_execution_context") or {})
        instruction_context = dict(worker_execution_context.get("instruction_context") or {})
        owner_username = str(instruction_context.get("owner_username") or "").strip() or None
        profile_id = str(instruction_context.get("profile_id") or "").strip() or None
        overlay_id = str(instruction_context.get("overlay_id") or "").strip() or None
        if not (profile_id or overlay_id):
            return None, 200
        if not owner_username:
            return "instruction_owner_username_required", 400
        repos = get_repository_registry()
        if profile_id:
            profile = repos.user_instruction_profile_repo.get_by_id(profile_id)
            if profile is None:
                return "instruction_profile_not_found", 404
            if str(profile.owner_username or "").strip() != owner_username:
                return "instruction_profile_owner_mismatch", 409
            validation = get_instruction_layer_service().validate_user_layer_payload(
                prompt_content=str(profile.prompt_content or ""),
                metadata=dict(profile.profile_metadata or {}),
            )
            if not validation.get("ok"):
                return "instruction_profile_policy_conflict", 409
        if overlay_id:
            overlay = repos.instruction_overlay_repo.get_by_id(overlay_id)
            if overlay is None:
                return "instruction_overlay_not_found", 404
            if str(overlay.owner_username or "").strip() != owner_username:
                return "instruction_overlay_owner_mismatch", 409
            validation = get_instruction_layer_service().validate_user_layer_payload(
                prompt_content=str(overlay.prompt_content or ""),
                metadata=dict(overlay.overlay_metadata or {}),
            )
            if not validation.get("ok"):
                return "instruction_overlay_policy_conflict", 409
        return None, 200

    def derivation_backfill(self) -> dict[str, Any]:
        repos = get_repository_registry()
        active = [t.model_dump() for t in repos.task_repo.get_all()]
        by_id = {t["id"]: t for t in active}
        updated_ids: list[str] = []
        skipped_reserved_ids: list[str] = []

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
            if has_reserved_vector_index_marker(item):
                skipped_reserved_ids.append(item["id"])
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
        return {
            "updated_count": len(updated_ids),
            "updated_ids": updated_ids,
            "skipped_reserved_count": len(skipped_reserved_ids),
            "skipped_reserved_ids": skipped_reserved_ids,
        }

    def create_task(self, *, data: Any, source: str, created_by: str) -> dict[str, Any]:
        input_data = data.model_dump()
        reserved_marker = find_reserved_vector_index_marker(input_data, source=source)
        if reserved_marker:
            return reserved_vector_index_ingress_error(reserved_marker)
        reserved_scope_marker = find_reserved_retrieval_vector_scope_marker(input_data)
        if reserved_scope_marker:
            return reserved_retrieval_vector_scope_ingress_error(reserved_scope_marker)
        reserved_context_marker = find_reserved_context_bundle_marker(input_data)
        if reserved_context_marker:
            return reserved_context_bundle_ingress_error(reserved_context_marker)
        task_id = data.id or str(uuid.uuid4())
        if data.id:
            repos = get_repository_registry()
            if (
                repos.task_repo.get_by_id(task_id) is not None
                or repos.archived_task_repo.get_by_id(task_id) is not None
            ):
                return {
                    "error": "task_id_already_exists",
                    "code": 409,
                    "data": {
                        "reason_code": "task_id_already_exists",
                        "task_id": task_id,
                    },
                }
        status = normalize_task_status(data.status, default="created")
        safe_data = {k: v for k, v in input_data.items() if v is not None and k not in ["id", "status"]}
        self._apply_instruction_selection_to_payload(
            safe_data,
            default_owner=self.actor_username(),
        )
        selection_error, selection_code = self._validate_instruction_selection(safe_data)
        if selection_error:
            return {"error": selection_error, "code": selection_code}
        instruction_context = dict((safe_data.get("worker_execution_context") or {}).get("instruction_context") or {})
        owner_username = str(instruction_context.get("owner_username") or "").strip() or None
        profile_id = str(instruction_context.get("profile_id") or "").strip() or None
        overlay_id = str(instruction_context.get("overlay_id") or "").strip() or None
        if owner_username and (profile_id or overlay_id):
            validation = get_instruction_layer_service().assemble_for_task(
                task={
                    "id": task_id,
                    "goal_id": safe_data.get("goal_id"),
                    "worker_execution_context": safe_data.get("worker_execution_context"),
                },
                base_prompt=str(safe_data.get("description") or safe_data.get("title") or ""),
                system_prompt=None,
            )
            safe_data.setdefault("verification_status", {})
            safe_data["verification_status"] = {
                **dict(safe_data.get("verification_status") or {}),
                "instruction_layers": dict(validation.get("diagnostics") or {}),
            }
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
        reserved_marker = find_reserved_vector_index_marker(update_data)
        if reserved_marker:
            return reserved_vector_index_ingress_error(reserved_marker)
        reserved_scope_marker = find_reserved_retrieval_vector_scope_marker(update_data)
        if reserved_scope_marker:
            return reserved_retrieval_vector_scope_ingress_error(reserved_scope_marker)
        reserved_context_marker = find_reserved_context_bundle_marker(update_data)
        if reserved_context_marker:
            return reserved_context_bundle_ingress_error(reserved_context_marker)
        existing = get_local_task_status(task_id)
        if not existing:
            return {"error": "not_found", "code": 404}
        reserved_marker = find_reserved_vector_index_marker(existing)
        if reserved_marker:
            return reserved_vector_index_ingress_error(reserved_marker)
        recovery_conflict = self._recovery_mutation_conflict(
            existing,
            action="task_patch",
        )
        if recovery_conflict:
            return recovery_conflict
        preserve_hub_retrieval_vector_scope(
            existing_task=existing,
            update_data=update_data,
        )
        preserve_hub_context_bundle_fields(
            existing_task=existing,
            update_data=update_data,
        )
        self._apply_instruction_selection_to_payload(
            update_data,
            default_owner=self.actor_username(),
        )
        selection_error, selection_code = self._validate_instruction_selection(update_data)
        if selection_error:
            return {"error": selection_error, "code": selection_code}
        status = normalize_task_status(update_data.pop("status", None), default="updated")
        gate_ok, gate_reason = self._enforce_task_state_mutation_gate(
            task_id=task_id,
            requested_status=status,
            task=existing,
        )
        if not gate_ok:
            return {"error": "mutation_gate_blocked", "code": 409, "data": {"reason_code": gate_reason}}
        if "depends_on" in update_data:
            update_data["depends_on"] = normalize_depends_on(update_data.get("depends_on"), tid=task_id)
            ok, reason = validate_dependencies_and_cycles(task_id, update_data.get("depends_on") or [])
            if not ok:
                return {"error": reason, "code": 400}
        update_local_task_status(task_id, status, **update_data)
        if status == "completed":
            task = get_local_task_status(task_id) or {}
            from agent.services.organization_workflow_completion_policy_service import (
                ORGANIZATION_WORKFLOW_WAITING_REASON,
            )

            if (
                str(task.get("status") or "") == "waiting_for_review"
                and str(task.get("status_reason_code") or "") == ORGANIZATION_WORKFLOW_WAITING_REASON
            ):
                return {
                    "error": ORGANIZATION_WORKFLOW_WAITING_REASON,
                    "code": 409,
                    "data": {
                        "id": task_id,
                        "status": "waiting_for_review",
                        "reason_code": ORGANIZATION_WORKFLOW_WAITING_REASON,
                    },
                }
            maybe_create_git_commit_followup(
                task=task,
                task_queue_service=get_task_queue_service(),
                actor=self.actor_username(),
            )
        return {"data": {"id": task_id, "status": "updated"}}

    def review_task_proposal(
        self,
        *,
        task_id: str,
        action: str,
        comment: str | None,
        vector_authorization: (VectorAdminAuthorizationContext | None) = None,
    ) -> dict[str, Any]:
        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}
        if has_reserved_vector_index_marker(task):
            vector_error = self._vector_admin_error(
                task,
                authorization=vector_authorization,
            )
            if vector_error is not None:
                return vector_error
            return {
                "error": ("vector_index_task_intervention_forbidden"),
                "code": 409,
                "data": {"reason_code": ("vector_index_task_intervention_forbidden")},
            }
        recovery_conflict = self._recovery_mutation_conflict(
            task,
            action=f"proposal_{action}",
        )
        if recovery_conflict:
            return recovery_conflict
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
        gate_ok, gate_reason = self._enforce_task_state_mutation_gate(
            task_id=task_id,
            requested_status=new_status,
            task=task,
        )
        if not gate_ok:
            return {"error": "mutation_gate_blocked", "code": 409, "data": {"reason_code": gate_reason}}
        update_local_task_status(
            task_id,
            new_status,
            last_proposal=proposal,
            history=history,
            manual_override_until=time.time() + 600,
            status_reason_code=GovernanceReasonCode.POLICY_VIOLATION.value if action == "reject" else None,
            status_reason_details={"comment": comment} if action == "reject" else {},
        )
        log_audit("task_proposal_reviewed", {"task_id": task_id, "action": action, "actor": self.actor_username()})
        return {"data": {"id": task_id, "review": review, "status": new_status}}

    def assign_task(
        self,
        *,
        task_id: str,
        data: Any,
        vector_authorization: (VectorAdminAuthorizationContext | None) = None,
    ) -> dict[str, Any]:
        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}
        vector_task = has_reserved_vector_index_marker(task)
        vector_error = self._vector_admin_error(
            task,
            authorization=vector_authorization,
        )
        if vector_error is not None:
            return vector_error
        if vector_task and (data.token or data.task_kind or data.required_capabilities):
            return {
                "error": ("vector_index_task_assignment_override_forbidden"),
                "code": 409,
                "data": {"reason_code": ("vector_index_task_assignment_override_forbidden")},
            }
        recovery_conflict = self._recovery_mutation_conflict(
            task,
            action="assign",
        )
        if recovery_conflict:
            return recovery_conflict
        effective_task_kind = task.get("task_kind") if vector_task else data.task_kind
        effective_required_capabilities = (
            task.get("required_capabilities") if vector_task else data.required_capabilities
        )
        can_assign, reasons, _worker = enforce_assignment_policy(
            task,
            data.agent_url,
            task_kind=effective_task_kind,
            required_capabilities=effective_required_capabilities,
        )
        decision_status = "approved" if can_assign else "blocked"
        persist_policy_decision(
            decision_type="assignment",
            status=decision_status,
            policy_name="worker_assignment_policy",
            policy_version="assignment-v1",
            reasons=reasons,
            details={
                "task_kind": effective_task_kind,
                "required_capabilities": (effective_required_capabilities),
                "manual_override": True,
            },
            task_id=task_id,
            worker_url=data.agent_url,
        )
        if not can_assign:
            return {
                "error": "assignment_policy_blocked",
                "code": 409,
                "data": {"reasons": reasons, "status_reason_code": GovernanceReasonCode.POLICY_VIOLATION.value},
            }
        update_local_task_status(
            task_id,
            "assigned",
            assigned_agent_url=data.agent_url,
            assigned_agent_token=data.token,
            manual_override_until=time.time() + 600,
            task_kind=(effective_task_kind or task.get("task_kind")),
            required_capabilities=(effective_required_capabilities or task.get("required_capabilities")),
            event_type="task_assigned",
            event_actor="system",
            event_details={"agent_url": data.agent_url, "policy_reasons": reasons},
        )
        return {"data": {"status": "assigned", "agent_url": data.agent_url}}

    def auto_assign_task(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        agent_registry_service,
        worker_contract_service,
        vector_authorization: (VectorAdminAuthorizationContext | None) = None,
    ) -> dict[str, Any]:
        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}
        vector_task = has_reserved_vector_index_marker(task)
        vector_error = self._vector_admin_error(
            task,
            authorization=vector_authorization,
        )
        if vector_error is not None:
            return vector_error
        if vector_task and ("task_kind" in payload or "required_capabilities" in payload):
            return {
                "error": ("vector_index_task_assignment_override_forbidden"),
                "code": 409,
                "data": {"reason_code": ("vector_index_task_assignment_override_forbidden")},
            }
        recovery_conflict = self._recovery_mutation_conflict(
            task,
            action="auto_assign",
        )
        if recovery_conflict:
            return recovery_conflict
        effective_task_kind = (
            task.get("task_kind") if vector_task else payload.get("task_kind") or task.get("task_kind")
        )
        effective_required_capabilities = (
            task.get("required_capabilities")
            if vector_task
            else payload.get("required_capabilities")
            or task.get("required_capabilities")
            or derive_required_capabilities(
                task,
                effective_task_kind,
            )
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
            update_local_task_status(
                task_id,
                "blocked",
                status_reason_code=GovernanceReasonCode.RESOURCE_UNAVAILABLE.value,
                status_reason_details={"reasons": selection.reasons},
            )
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

    def unassign_task(
        self,
        *,
        task_id: str,
        vector_authorization: (VectorAdminAuthorizationContext | None) = None,
    ) -> dict[str, Any]:
        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}
        vector_error = self._vector_admin_error(
            task,
            authorization=vector_authorization,
        )
        if vector_error is not None:
            return vector_error
        recovery_conflict = self._recovery_mutation_conflict(
            task,
            action="unassign",
        )
        if recovery_conflict:
            return recovery_conflict
        update_local_task_status(
            task_id,
            "todo",
            assigned_agent_url=None,
            assigned_agent_token=None,
            assigned_to=None,
            manual_override_until=time.time() + 600,
        )
        return {"data": {"status": "todo", "unassigned": True}}

    def subtask_callback(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        vector_authorization: (VectorAdminAuthorizationContext | None) = None,
    ) -> dict[str, Any]:
        subtask_id = payload.get("id")
        new_status = payload.get("status")
        if not subtask_id or not new_status:
            return {"error": "invalid_payload", "code": 400}
        parent_task = get_local_task_status(task_id)
        if not parent_task:
            return {"error": "parent_task_not_found", "code": 404}
        if has_reserved_vector_index_marker(parent_task):
            vector_error = self._vector_admin_error(
                parent_task,
                authorization=vector_authorization,
            )
            if vector_error is not None:
                return vector_error
            return {
                "error": ("vector_index_task_intervention_forbidden"),
                "code": 409,
                "data": {"reason_code": ("vector_index_task_intervention_forbidden")},
            }
        recovery_conflict = self._recovery_mutation_conflict(
            parent_task,
            action="subtask_callback",
        )
        if recovery_conflict:
            return recovery_conflict
        subtasks = list(parent_task.get("subtasks") or [])
        updated = False
        for item in subtasks:
            if item.get("id") == subtask_id:
                item["status"] = new_status
                if "last_output" in payload:
                    item["last_output"] = payload["last_output"]
                if "last_exit_code" in payload:
                    item["last_exit_code"] = payload["last_exit_code"]
                if "worker_job_id" in payload:
                    item["worker_job_id"] = payload["worker_job_id"]
                if isinstance(payload.get("artifacts"), list):
                    item["artifacts"] = payload.get("artifacts")
                updated = True
                break
        if not updated:
            return {"error": "subtask_not_found", "code": 404}
        update_local_task_status(task_id, parent_task.get("status", "in_progress"), subtasks=subtasks)
        return {"data": {"status": "updated"}}

    def create_followups(
        self,
        *,
        task_id: str,
        data: Any,
        vector_authorization: (VectorAdminAuthorizationContext | None) = None,
    ) -> dict[str, Any]:
        parent_task = get_local_task_status(task_id)
        if not parent_task:
            return {"error": "parent_task_not_found", "code": 404}
        if has_reserved_vector_index_marker(parent_task):
            vector_error = self._vector_admin_error(
                parent_task,
                authorization=vector_authorization,
            )
            if vector_error is not None:
                return vector_error
            return {
                "error": ("vector_index_task_intervention_forbidden"),
                "code": 409,
                "data": {"reason_code": ("vector_index_task_intervention_forbidden")},
            }
        recovery_conflict = self._recovery_mutation_conflict(
            parent_task,
            action="create_followups",
        )
        if recovery_conflict:
            return recovery_conflict
        from agent.services.organization_planning_adapter import (
            organization_id_from_task,
        )

        organization_id = organization_id_from_task(parent_task)
        if organization_id:
            from agent.services.organization_followup_amendment_service import (
                OrganizationFollowupAmendmentService,
            )

            try:
                staged = OrganizationFollowupAmendmentService().stage_manual_followups(
                    parent_task=parent_task,
                    items=list(data.items or []),
                    actor=self.actor_username(),
                )
            except ValueError as exc:
                return {
                    "error": str(exc),
                    "code": 409,
                    "data": {
                        "reason_code": str(exc),
                        "organization_id": organization_id,
                        "source_task_id": task_id,
                    },
                }
            return {
                "data": {
                    "status": "planning_amendment_staged",
                    "organization_id": organization_id,
                    "source_task_id": task_id,
                    **staged,
                },
            }

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
                "parent_task_id": task_id,
                "source_task_id": task_id,
                "derivation_reason": "manual_followup",
                "derivation_depth": int(parent_task.get("derivation_depth") or 0) + 1,
            }

            get_task_queue_service().ingest_task(
                task_id=subtask_id,
                status=status,
                title=desc[:200],
                description=desc,
                priority=str(item.priority or "Medium"),
                created_by=self.actor_username(),
                source="agent",
                team_id=parent_task.get("team_id"),
                event_type="task_ingested",
                event_channel="followup_management",
                event_details={
                    "parent_task_id": task_id,
                    "source_task_id": task_id,
                    "derivation_reason": "manual_followup",
                },
                extra_fields=create_payload,
            )
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
