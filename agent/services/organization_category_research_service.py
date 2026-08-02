"""Hub-owned Category research task creation and result admission."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from agent.db_models import (
    GoalDB,
    OrganizationInstanceDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    TaskDB,
    WorkerJobDB,
)
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.planning_category_pipeline_service import PlanningCategoryPipelineService
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)
from agent.services.planning_evidence_resolver_service import AssignmentEvidenceContext
from agent.services.source_catalog_authority_service import SourceCatalogAuthorityService
from agent.services.worker_task_proposal_policy_service import (
    WorkerTaskProposalPolicyService,
)

_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_PATH = _ROOT / "prompts" / "planning" / "category_research_planning.j2"
_CATALOG_TASK_SOURCES = frozenset({"agent", "api", "system", "ui"})
_CATALOG_TASK_KINDS = frozenset({"knowledge", "planning_research", "research", "retrieval", "source_catalog"})
# The generic delegation facade currently preserves the parent Task status
# while attaching the authoritative WorkerJob lease.  ``todo`` is therefore
# an admissible callback state only together with the exact current-job,
# assignment and Worker checks below; it is not sufficient on its own.
_ACTIVE_RESULT_TASK_STATES = frozenset({"todo", "assigned", "in_progress", "completed"})


class OrganizationCategoryResearchService:
    """Create exactly one scoped research Task and accept its bound result."""

    def __init__(
        self,
        *,
        source_catalog_authority: SourceCatalogAuthorityService | None = None,
        category_pipeline: PlanningCategoryPipelineService | None = None,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
        task_reader: Callable[[str], Any | None] | None = None,
    ) -> None:
        self._catalog_authority = source_catalog_authority or SourceCatalogAuthorityService()
        self._pipeline = category_pipeline or PlanningCategoryPipelineService()
        self._uow_factory = uow_factory or PlanningControlUnitOfWork
        self._task_reader = task_reader or self._default_task_reader

    @staticmethod
    def _default_task_reader(task_id: str) -> Any | None:
        from agent.repository import task_repo

        return task_repo.get_by_id(task_id)

    def create_task(
        self,
        *,
        context: PlanningOperationContext,
        goal_id: str,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
        catalog_binding: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._authorize(context)
        self._require_idempotency_key(idempotency_key)
        resolved = self._catalog_authority.resolve(
            principal=ChatSessionPrincipal.from_values(
                context.tenant_id,
                context.subject_id,
            ),
            catalog_task_id=str(catalog_binding.get("catalog_task_id") or ""),
            catalog_id=str(catalog_binding.get("catalog_id") or ""),
            catalog_hash=str(catalog_binding.get("catalog_hash") or ""),
            repository_revision=str(catalog_binding.get("repository_revision") or ""),
            manifest_hash=str(catalog_binding.get("manifest_hash") or ""),
            source_allowlist_version=str(catalog_binding.get("source_allowlist_version") or ""),
            source_scope=str(catalog_binding.get("source_scope") or ""),
            allowed_task_sources=_CATALOG_TASK_SOURCES,
            allowed_task_kinds=_CATALOG_TASK_KINDS,
        )
        source_catalog = self._authoritative_catalog_payload(
            resolved.catalog_task_id,
            catalog_id=resolved.catalog_id,
            catalog_hash=resolved.catalog_hash,
            allowed_ids={row.source_id for row in resolved.source_refs},
        )
        source_ids = sorted(row.source_id for row in resolved.source_refs)
        source_refs = [value for value in source_ids if value.startswith("SRC_")]
        run_refs = [value for value in source_ids if value.startswith("RUN_")]
        prompt_hash = hashlib.sha256(_PROMPT_PATH.read_bytes()).hexdigest()
        request_binding = {
            "schema": "organization_category_research_binding.v1",
            "goal_id": goal_id,
            "unit_id": unit_id,
            "team_id": team_id,
            "role_slot_id": role_slot_id,
            "source_catalog_id": resolved.catalog_id,
            "source_catalog_hash": resolved.catalog_hash,
            "repository_revision": resolved.repository_revision,
            "manifest_hash": resolved.manifest_hash,
            "source_allowlist_version": resolved.source_allowlist_version,
            "allowed_source_refs": source_refs,
            "allowed_run_refs": run_refs,
            "prompt_hash": prompt_hash,
        }
        request_digest = self._digest(request_binding)
        task_id = self._stable_id(
            "presearch",
            context.tenant_id,
            context.project_id,
            context.organization_id,
            goal_id,
            idempotency_key,
        )
        artifact_id = self._stable_id(
            "pcategory",
            context.tenant_id,
            context.project_id,
            context.organization_id,
            goal_id,
        )
        with (
            planning_scope_lock(f"planning-category-research:{context.organization_id}:{goal_id}"),
            self._uow_factory() as uow,
        ):
            assert uow.session is not None
            self._validate_goal_and_binding(
                uow.session,
                context=context,
                goal_id=goal_id,
                unit_id=unit_id,
                team_id=team_id,
                role_slot_id=role_slot_id,
            )
            existing = uow.session.get(TaskDB, task_id)
            if existing is not None:
                binding = dict(existing.worker_execution_context or {}).get("planning_research_binding")
                if not isinstance(binding, Mapping) or str(binding.get("request_digest") or "") != request_digest:
                    raise PlanningTransitionError("category_research_idempotency_conflict")
                return self._task_response(existing, replayed=True)
            active = uow.session.exec(
                select(TaskDB).where(
                    TaskDB.tenant_id == context.tenant_id,
                    TaskDB.project_id == context.project_id,
                    TaskDB.organization_id == context.organization_id,
                    TaskDB.goal_id == goal_id,
                    TaskDB.task_kind == "planning_research",
                    TaskDB.status.in_(("todo", "created", "assigned", "in_progress")),
                )
            ).first()
            if active is not None:
                raise PlanningTransitionError("category_research_already_active")
            organization = uow.session.get(
                OrganizationInstanceDB,
                context.organization_id,
            )
            if (
                organization is None
                or organization.tenant_id != context.tenant_id
                or organization.project_id != context.project_id
            ):
                raise PlanningTransitionError("organization_planning_not_found")
            task = TaskDB(
                id=task_id,
                title="Research and produce the Organization Category-Todo",
                description=(
                    "Perform the assignment-bound research phase and return exactly one "
                    "JSON object conforming to todos/todo.schema.json. Use only the "
                    "Hub-provided source and run references; never invent identifiers."
                ),
                status="todo",
                priority="High",
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                organization_id=context.organization_id,
                unit_id=unit_id,
                team_id=team_id,
                role_slot_id=role_slot_id,
                goal_id=goal_id,
                task_kind="planning_research",
                required_capabilities=["research", "planning"],
                worker_execution_context={
                    "planning_research_binding": {
                        **request_binding,
                        "request_digest": request_digest,
                        "artifact_id": artifact_id,
                        "policy_hash": organization.effective_limit_profile_hash,
                        "source_catalog": source_catalog,
                        "artifact_hashes": {},
                    },
                    "allowed_source_refs": source_refs,
                    "allowed_run_refs": run_refs,
                    "allowed_tools": [],
                    "expected_output_schema": {
                        "schema_ref": "todos/todo.schema.json",
                        "artifact_type": "planning_category_todo",
                    },
                    "planning_result_callback": {
                        "schema": "organization_planning_result_callback.v1",
                        "method": "POST",
                        "path_template": (
                            "/api/worker-results/tasks/{source_task_id}/assignments/{assignment_id}/planning/category"
                        ),
                        "authorization": "worker_result_capability",
                    },
                    "task_proposal_policy": WorkerTaskProposalPolicyService.default_deny_policy(),
                },
                history=[
                    {
                        "timestamp": time.time(),
                        "status": "todo",
                        "event_type": "organization_category_research_created",
                        "actor": "hub:organization_planning",
                        "details": {
                            "organization_id": context.organization_id,
                            "goal_id": goal_id,
                            "artifact_id": artifact_id,
                            "request_digest": request_digest,
                            "source_catalog_id": resolved.catalog_id,
                            "source_catalog_hash": resolved.catalog_hash,
                        },
                    }
                ],
            )
            uow.session.add(task)
            uow.session.flush()
        return self._task_response(task, replayed=False)

    def accept_result(
        self,
        *,
        source_task_id: str,
        assignment_id: str,
        capability_claims: Mapping[str, Any],
        raw_output: str,
        raw_output_digest: str,
        idempotency_key: str,
        runtime_artifact_hashes: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        self._require_idempotency_key(idempotency_key)
        if self._digest_text(raw_output) != str(raw_output_digest or ""):
            raise PlanningTransitionError("category_research_result_digest_mismatch")
        with self._uow_factory() as uow:
            assert uow.session is not None
            task = uow.session.get(TaskDB, source_task_id)
            if task is None or task.task_kind != "planning_research":
                raise PlanningTransitionError("category_research_task_not_found")
            if str(task.status or "") not in _ACTIVE_RESULT_TASK_STATES:
                raise PlanningTransitionError("category_research_task_not_active")
            job = uow.session.get(
                WorkerJobDB,
                str(capability_claims.get("dispatch_lease_id") or ""),
            )
            if (
                job is None
                or str(job.parent_task_id or "") != task.id
                or str(job.subtask_id or "") != assignment_id
                or str(job.worker_url or "") != str(capability_claims.get("worker_id") or "")
                or str(task.current_worker_job_id or "") != job.id
            ):
                raise PlanningTransitionError("category_research_assignment_invalid")
            binding = dict(task.worker_execution_context or {}).get("planning_research_binding")
            if not isinstance(binding, Mapping):
                raise PlanningTransitionError("category_research_binding_missing")
            bound = dict(binding)
            evidence_context = AssignmentEvidenceContext(
                task_id=task.id,
                assignment_id=assignment_id,
                dispatch_lease_id=job.id,
                tenant_id=str(task.tenant_id or ""),
                scope=f"organization:{task.organization_id}",
                source_catalog_id=str(bound.get("source_catalog_id") or ""),
                source_catalog_hash=str(bound.get("source_catalog_hash") or ""),
                allowed_source_refs=frozenset(
                    str(value) for value in list(bound.get("allowed_source_refs") or []) if str(value)
                ),
                allowed_run_refs=frozenset(
                    str(value) for value in list(bound.get("allowed_run_refs") or []) if str(value)
                ),
                artifact_hashes=dict(bound.get("artifact_hashes") or {}),
            )
            arguments = {
                "tenant_id": str(task.tenant_id or ""),
                "project_id": str(task.project_id or ""),
                "organization_id": str(task.organization_id or ""),
                "goal_id": str(task.goal_id or ""),
                "task_id": task.id,
                "assignment_id": assignment_id,
                "dispatch_lease_id": job.id,
                "worker_id": str(capability_claims.get("worker_id") or ""),
                "artifact_id": str(bound.get("artifact_id") or ""),
                "raw_output": raw_output,
                "evidence_context": evidence_context,
                "source_catalog": dict(bound.get("source_catalog") or {}),
                "tool_run_catalog": [],
                "prompt_hash": str(bound.get("prompt_hash") or ""),
                "policy_hash": str(bound.get("policy_hash") or ""),
                "runtime_artifact_hashes": dict(runtime_artifact_hashes or {}),
                "result_idempotency_key": idempotency_key,
                "raw_output_digest": raw_output_digest,
                "require_authoritative_task": True,
            }
        return self._pipeline.persist_research_result(**arguments)

    def _authoritative_catalog_payload(
        self,
        catalog_task_id: str,
        *,
        catalog_id: str,
        catalog_hash: str,
        allowed_ids: set[str],
    ) -> dict[str, Any]:
        task = self._task_reader(catalog_task_id)
        raw = task.model_dump() if hasattr(task, "model_dump") else dict(task or {})
        catalog = dict(dict(raw.get("verification_status") or {}).get("source_catalog") or {})
        if (
            str(catalog.get("source_catalog_id") or "") != catalog_id
            or str(catalog.get("source_catalog_hash") or "") != catalog_hash
        ):
            raise PlanningTransitionError("category_research_source_catalog_stale")
        sources = [
            dict(row)
            for row in list(catalog.get("sources") or [])
            if isinstance(row, Mapping) and str(row.get("source_id") or "") in allowed_ids
        ]
        if {str(row.get("source_id") or "") for row in sources} != allowed_ids:
            raise PlanningTransitionError("category_research_source_catalog_incomplete")
        return {
            "schema": "source_catalog.v2",
            "source_catalog_id": catalog_id,
            "source_catalog_hash": catalog_hash,
            "sources": sources,
        }

    @staticmethod
    def _validate_goal_and_binding(
        session: Session,
        *,
        context: PlanningOperationContext,
        goal_id: str,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
    ) -> None:
        goal = session.exec(
            select(GoalDB).where(
                GoalDB.id == goal_id,
                GoalDB.tenant_id == context.tenant_id,
                GoalDB.project_id == context.project_id,
                GoalDB.organization_id == context.organization_id,
            )
        ).one_or_none()
        if goal is None or str(goal.goal_kind or "") != "organization":
            raise PlanningTransitionError("organization_goal_not_found")
        team = session.exec(
            select(OrganizationTeamLinkDB).where(
                OrganizationTeamLinkDB.tenant_id == context.tenant_id,
                OrganizationTeamLinkDB.project_id == context.project_id,
                OrganizationTeamLinkDB.organization_id == context.organization_id,
                OrganizationTeamLinkDB.unit_id == unit_id,
                OrganizationTeamLinkDB.team_id == team_id,
                OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")),
            )
        ).one_or_none()
        slot = session.exec(
            select(OrganizationRoleSlotDB).where(
                OrganizationRoleSlotDB.id == role_slot_id,
                OrganizationRoleSlotDB.tenant_id == context.tenant_id,
                OrganizationRoleSlotDB.project_id == context.project_id,
                OrganizationRoleSlotDB.organization_id == context.organization_id,
                OrganizationRoleSlotDB.unit_id == unit_id,
                OrganizationRoleSlotDB.lifecycle == "active",
            )
        ).one_or_none()
        if team is None or slot is None:
            raise PlanningTransitionError("category_research_role_binding_invalid")

    @staticmethod
    def _authorize(context: PlanningOperationContext) -> None:
        if not context.hub_owned:
            raise PlanningTransitionError("planning_hub_authority_required")
        if "organization_admin" not in context.roles and "category_research" not in context.allowed_operations:
            raise PlanningTransitionError("planning_organization_admin_required")

    @staticmethod
    def _require_idempotency_key(value: str) -> None:
        normalized = str(value or "").strip()
        if not 8 <= len(normalized) <= 191 or any(char.isspace() for char in normalized):
            raise PlanningTransitionError("planning_idempotency_key_required")

    @staticmethod
    def _task_response(task: TaskDB, *, replayed: bool) -> dict[str, Any]:
        binding = dict(task.worker_execution_context or {}).get("planning_research_binding")
        bound = dict(binding) if isinstance(binding, Mapping) else {}
        return {
            "task_id": task.id,
            "goal_id": task.goal_id,
            "organization_id": task.organization_id,
            "artifact_id": bound.get("artifact_id"),
            "status": task.status,
            "task_kind": task.task_kind,
            "allowed_source_refs": list(bound.get("allowed_source_refs") or []),
            "allowed_run_refs": list(bound.get("allowed_run_refs") or []),
            "replayed": replayed,
            "materialized_task_ids": [],
        }

    @staticmethod
    def _stable_id(prefix: str, *values: str) -> str:
        digest = hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()[:24]
        return f"{prefix}-{digest}"

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _digest_text(value: str) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


__all__ = ["OrganizationCategoryResearchService"]
