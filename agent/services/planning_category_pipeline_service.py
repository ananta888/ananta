from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from sqlmodel import select

from agent.db_models import (
    PlanningArtifactRevisionDB,
    TaskDB,
    WorkerJobDB,
    WorkerResultDB,
    WorkerSlotLeaseDB,
)
from agent.services.planning_category_contract_service import (
    PlanningCategoryContractService,
    stable_planning_digest,
)
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)
from agent.services.planning_evidence_resolver_service import (
    AssignmentEvidenceContext,
    PlanningEvidenceResolverService,
)
from agent.services.planning_principal_identity_service import (
    canonical_planning_worker_id,
)
from agent.services.planning_utils import extract_json_payload


def build_category_repair_prompt(*, raw_output: str, issues: list[dict[str, str]]) -> str:
    bounded = list(issues or [])[:30]
    return (
        "Repair this planning category todo. Return exactly one JSON object and no markdown.\n"
        "It must validate against todos/todo.schema.json and preserve planning_quality_profile evidence IDs.\n"
        "Never invent SRC_* or RUN_* identifiers.\n"
        "Validation issues:\n"
        + "\n".join(f"- {row.get('path')}: {row.get('reason_code')} ({row.get('human_message')})" for row in bounded)
        + "\nOriginal output:\n"
        + str(raw_output or "")
    )


class PlanningCategoryPipelineService:
    """Hub pipeline for research output; deliberately has no Task port."""

    def __init__(
        self,
        *,
        contract_service: PlanningCategoryContractService | None = None,
        evidence_resolver: PlanningEvidenceResolverService | None = None,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
    ) -> None:
        self._contract = contract_service or PlanningCategoryContractService()
        self._evidence_resolver = evidence_resolver or PlanningEvidenceResolverService()
        self._uow_factory = uow_factory or PlanningControlUnitOfWork

    def persist_research_result(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        goal_id: str,
        task_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        worker_id: str,
        artifact_id: str,
        raw_output: str,
        evidence_context: AssignmentEvidenceContext,
        source_catalog: Mapping[str, Any],
        tool_run_catalog: list[Mapping[str, Any]] | None,
        prompt_hash: str,
        policy_hash: str,
        runtime_artifact_hashes: Mapping[str, str] | None = None,
        repair_fn: Callable[[str], str] | None = None,
        result_idempotency_key: str | None = None,
        raw_output_digest: str | None = None,
        require_authoritative_task: bool = False,
    ) -> dict[str, Any]:
        self._require_scope(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            goal_id=goal_id,
        )
        if assignment_id != evidence_context.assignment_id:
            raise ValueError("category_assignment_mismatch")
        if dispatch_lease_id != evidence_context.dispatch_lease_id:
            raise ValueError("category_dispatch_lease_mismatch")
        self._evidence_resolver.validate_runtime_binding(
            expected=evidence_context,
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
            artifact_hashes=runtime_artifact_hashes,
        )

        resolved_tool_run_catalog = [
            dict(row)
            for row in list(tool_run_catalog or [])
            if isinstance(row, Mapping)
        ]
        if require_authoritative_task and evidence_context.allowed_run_refs:
            from agent.services.organization_category_run_evidence_service import (
                OrganizationCategoryRunEvidenceService,
            )

            resolved_tool_run_catalog = (
                OrganizationCategoryRunEvidenceService().build_catalog(
                    task_id=task_id,
                    assignment_id=assignment_id,
                    dispatch_lease_id=dispatch_lease_id,
                    worker_id=worker_id,
                    raw_output=raw_output,
                    raw_output_digest=str(raw_output_digest or ""),
                    allowed_run_refs=evidence_context.allowed_run_refs,
                    runtime_artifact_hashes=runtime_artifact_hashes,
                )
            )

        candidate, parse_issues = self._parse(raw_output)
        result = (
            self._contract.validate_and_recompute(
                candidate,
                evidence_context=evidence_context,
                source_catalog=source_catalog,
                tool_run_catalog=resolved_tool_run_catalog,
            )
            if candidate is not None
            else {"valid": False, "promotable": False, "issues": parse_issues, "payload": {}}
        )
        repair_attempt_count = 0
        if not result.get("valid") and repair_fn is not None:
            repair_attempt_count = 1
            repaired_output = str(
                repair_fn(
                    build_category_repair_prompt(
                        raw_output=raw_output,
                        issues=list(result.get("issues") or parse_issues),
                    )
                )
                or ""
            )
            repaired, repaired_parse_issues = self._parse(repaired_output)
            result = (
                self._contract.validate_and_recompute(
                    repaired,
                    evidence_context=evidence_context,
                    source_catalog=source_catalog,
                    tool_run_catalog=resolved_tool_run_catalog,
                )
                if repaired is not None
                else {
                    "valid": False,
                    "promotable": False,
                    "issues": repaired_parse_issues,
                    "payload": {},
                }
            )

        payload = dict(result.get("payload") or {})
        content_digest = str(result.get("content_digest") or stable_planning_digest(payload))
        execution_provenance = {
            "schema": "planning_execution_provenance.v1",
            "goal_id": goal_id,
            "task_id": task_id,
            "worker_id": worker_id,
            "assignment_id": assignment_id,
            "dispatch_lease_id": dispatch_lease_id,
            "source_catalog_id": evidence_context.source_catalog_id,
            "source_catalog_hash": evidence_context.source_catalog_hash,
            "allowed_source_refs": sorted(evidence_context.allowed_source_refs),
            "allowed_run_refs": sorted(evidence_context.allowed_run_refs),
            "artifact_hashes": dict(sorted(evidence_context.artifact_hashes.items())),
            "result_idempotency_key": str(result_idempotency_key or ""),
            "raw_output_digest": str(raw_output_digest or ""),
            "tool_run_catalog": resolved_tool_run_catalog,
        }
        with planning_scope_lock(f"planning-category-revision:{artifact_id}"), self._uow_factory() as uow:
            assert uow.planning is not None
            uow.planning.acquire_scope_lock(f"planning-category-revision:{artifact_id}")
            authoritative_task: TaskDB | None = None
            authoritative_job: WorkerJobDB | None = None
            if require_authoritative_task:
                if uow.session is None:
                    raise ValueError("category_authoritative_task_session_required")
                authoritative_task, authoritative_job = self._validate_authoritative_task(
                    uow.session,
                    task_id=task_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    goal_id=goal_id,
                    assignment_id=assignment_id,
                    dispatch_lease_id=dispatch_lease_id,
                    worker_id=worker_id,
                )
            previous = uow.planning.latest_revision(artifact_id=artifact_id)
            if result_idempotency_key:
                prior = next(
                    (
                        row
                        for row in uow.planning.list_revisions(
                            goal_id=goal_id,
                            organization_id=organization_id,
                            artifact_type="planning_category_todo",
                        )
                        if row.artifact_id == artifact_id
                        and str(dict(row.execution_provenance or {}).get("result_idempotency_key") or "")
                        == str(result_idempotency_key)
                    ),
                    None,
                )
                if prior is not None:
                    prior_provenance = dict(prior.execution_provenance or {})
                    if str(prior_provenance.get("raw_output_digest") or "") != str(raw_output_digest or ""):
                        raise ValueError("category_result_idempotency_conflict")
                    return self._revision_response(prior, replayed=True)
            if authoritative_task is not None and authoritative_job is not None:
                worker_context = dict(authoritative_task.worker_execution_context or {})
                raw_binding = worker_context.get("planning_research_binding")
                binding = dict(raw_binding) if isinstance(raw_binding, Mapping) else {}
                prior_digest = str(binding.get("accepted_result_payload_digest") or "")
                if prior_digest:
                    if prior_digest != str(raw_output_digest or ""):
                        raise ValueError("category_result_idempotency_conflict")
                    prior = next(
                        (
                            row
                            for row in uow.planning.list_revisions(
                                goal_id=goal_id,
                                organization_id=organization_id,
                                artifact_type="planning_category_todo",
                            )
                            if row.artifact_id == artifact_id
                            and str(dict(row.execution_provenance or {}).get("raw_output_digest") or "") == prior_digest
                        ),
                        None,
                    )
                    if prior is None:
                        raise ValueError("category_result_receipt_missing")
                    return self._revision_response(prior, replayed=True)
                active_job = (
                    str(authoritative_job.status or "") in {"delegated", "running"}
                    and authoritative_job.finished_at is None
                )
                completed_result_recovery = (
                    str(authoritative_job.status or "") == "completed"
                    and authoritative_job.finished_at is not None
                    and self._has_matching_completed_worker_result(
                        uow.session,
                        dispatch_lease_id=dispatch_lease_id,
                        assignment_id=assignment_id,
                        raw_output_digest=str(raw_output_digest or ""),
                    )
                )
                if (
                    (not active_job and not completed_result_recovery)
                    or str(authoritative_task.status or "") == "completed"
                ):
                    raise ValueError("category_authoritative_task_lease_inactive")
            revision_number = uow.planning.next_revision_number(artifact_id=artifact_id)
            revision_id = self._revision_id(artifact_id=artifact_id, revision=revision_number, digest=content_digest)
            revision = PlanningArtifactRevisionDB(
                id=revision_id,
                artifact_id=artifact_id,
                revision=revision_number,
                artifact_type="planning_category_todo",
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                goal_id=goal_id,
                status="valid" if bool(result.get("promotable")) else "failed",
                payload=payload,
                content_digest=content_digest,
                schema_ref="todos/todo.schema.json",
                schema_hash=str(result.get("schema_hash") or ""),
                prompt_hash=str(prompt_hash or ""),
                policy_hash=str(policy_hash or ""),
                source_catalog_id=evidence_context.source_catalog_id,
                source_catalog_hash=evidence_context.source_catalog_hash,
                allowed_source_refs=sorted(evidence_context.allowed_source_refs),
                allowed_run_refs=sorted(evidence_context.allowed_run_refs),
                execution_provenance=execution_provenance,
                validation_result={
                    "valid": bool(result.get("valid")),
                    "promotable": bool(result.get("promotable")),
                    "issues": list(result.get("issues") or [])[:100],
                    "repair_attempt_count": repair_attempt_count,
                    "grounding": dict(result.get("grounding") or {}),
                },
                supersedes_revision_id=str(getattr(previous, "id", "") or "") or None,
                created_by=canonical_planning_worker_id(worker_id),
                created_by_principal_id=canonical_planning_worker_id(worker_id),
            )
            uow.planning.add_revision(revision)
            if authoritative_task is not None and authoritative_job is not None:
                now = time.time()
                worker_context = dict(authoritative_task.worker_execution_context or {})
                raw_binding = worker_context.get("planning_research_binding")
                binding = dict(raw_binding) if isinstance(raw_binding, Mapping) else {}
                binding.update(
                    {
                        "accepted_result_payload_digest": str(raw_output_digest or ""),
                        "accepted_artifact_revision_id": revision.id,
                    }
                )
                worker_context["planning_research_binding"] = binding
                authoritative_task.worker_execution_context = worker_context
                authoritative_task.status = "completed"
                authoritative_task.last_output = raw_output
                authoritative_task.last_exit_code = 0
                authoritative_task.updated_at = now
                authoritative_task.history = [
                    *list(authoritative_task.history or []),
                    {
                        "timestamp": now,
                        "status": "completed",
                        "event_type": "organization_category_research_result_accepted",
                        "actor": "hub:organization_planning",
                        "details": {
                            "raw_output_digest": str(raw_output_digest or ""),
                            "artifact_revision_id": revision.id,
                        },
                    },
                ]
                authoritative_job.status = "completed"
                authoritative_job.finished_at = authoritative_job.finished_at or now
                authoritative_job.updated_at = now
                assignment_task = uow.session.get(TaskDB, assignment_id)
                if assignment_task is not None:
                    if (
                        str(assignment_task.parent_task_id or "")
                        != str(authoritative_task.id or "")
                        or str(assignment_task.current_worker_job_id or "")
                        != str(authoritative_job.id or "")
                        or str(assignment_task.status or "")
                        not in {
                            "todo",
                            "assigned",
                            "in_progress",
                            "blocked_by_dependency",
                            "completed",
                        }
                    ):
                        raise ValueError("category_assignment_task_binding_invalid")
                    assignment_task.status = "completed"
                    assignment_task.last_output = raw_output
                    assignment_task.last_exit_code = 0
                    assignment_task.updated_at = now
                    assignment_task.history = [
                        *list(assignment_task.history or []),
                        {
                            "timestamp": now,
                            "status": "completed",
                            "event_type": "organization_category_research_assignment_completed",
                            "actor": "hub:organization_planning",
                            "details": {
                                "source_task_id": authoritative_task.id,
                                "worker_job_id": authoritative_job.id,
                                "artifact_revision_id": revision.id,
                            },
                        },
                    ]
                    uow.session.add(assignment_task)
                if authoritative_job.slot_lease_id:
                    slot_lease = uow.session.get(
                        WorkerSlotLeaseDB,
                        str(authoritative_job.slot_lease_id),
                    )
                    if slot_lease is None or (
                        str(slot_lease.parent_task_id or "")
                        not in {"", str(authoritative_task.id or "")}
                        or str(slot_lease.worker_job_id or "")
                        not in {"", str(authoritative_job.id or "")}
                    ):
                        raise ValueError("category_dispatch_slot_lease_invalid")
                    if str(slot_lease.status or "") == "active":
                        slot_lease.status = "released"
                        slot_lease.released_at = now
                        uow.session.add(slot_lease)
                uow.session.add(authoritative_task)
                uow.session.add(authoritative_job)
        return self._revision_response(revision, replayed=False)

    @staticmethod
    def _validate_authoritative_task(
        session,
        *,
        task_id: str,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        goal_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        worker_id: str,
    ) -> tuple[TaskDB, WorkerJobDB]:
        task = session.get(TaskDB, task_id)
        job = session.get(WorkerJobDB, dispatch_lease_id)
        if (
            task is None
            or str(task.tenant_id or "") != tenant_id
            or str(task.project_id or "") != project_id
            or str(task.organization_id or "") != organization_id
            or str(task.goal_id or "") != goal_id
            or str(task.task_kind or "") != "planning_research"
            # Generic delegation currently preserves ``todo`` while binding
            # current_worker_job_id.  The exact WorkerJob checks below are the
            # authority boundary; status alone never admits a result.
            or str(task.status or "")
            not in {
                "todo",
                "assigned",
                "in_progress",
                "blocked_by_dependency",
                "completed",
            }
            or str(task.current_worker_job_id or "") != dispatch_lease_id
            or job is None
            or str(job.parent_task_id or "") != task_id
            or str(job.subtask_id or "") != assignment_id
            or str(job.worker_url or "") != worker_id
        ):
            raise ValueError("category_authoritative_task_binding_invalid")
        return task, job

    @staticmethod
    def _has_matching_completed_worker_result(
        session,
        *,
        dispatch_lease_id: str,
        assignment_id: str,
        raw_output_digest: str,
    ) -> bool:
        if len(raw_output_digest) != 64:
            return False
        rows = session.exec(
            select(WorkerResultDB).where(
                WorkerResultDB.worker_job_id == dispatch_lease_id,
                WorkerResultDB.task_id == assignment_id,
                WorkerResultDB.status == "completed",
            )
        ).all()
        return any(
            hashlib.sha256(str(row.output or "").encode("utf-8")).hexdigest()
            == raw_output_digest
            for row in rows
        )

    @staticmethod
    def _revision_response(
        revision: PlanningArtifactRevisionDB,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        validation = dict(revision.validation_result or {})
        return {
            "artifact_revision_id": revision.id,
            "artifact_id": revision.artifact_id,
            "revision": revision.revision,
            "status": revision.status,
            "promotable": bool(validation.get("promotable")),
            "content_digest": revision.content_digest,
            "repair_attempt_count": int(validation.get("repair_attempt_count") or 0),
            "issues": list(validation.get("issues") or []),
            "materialized_task_ids": [],
            "replayed": replayed,
        }

    @staticmethod
    def _parse(raw_output: str) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
        extracted = extract_json_payload(str(raw_output or ""))
        if not extracted:
            return None, [
                {"path": "$", "reason_code": "category_non_json_output", "human_message": "No JSON object found."}
            ]
        try:
            value = json.loads(extracted)
        except json.JSONDecodeError as exc:
            return None, [{"path": "$", "reason_code": "category_invalid_json", "human_message": str(exc)}]
        if not isinstance(value, dict):
            return None, [
                {"path": "$", "reason_code": "category_invalid_shape", "human_message": "JSON object required."}
            ]
        return value, []

    @staticmethod
    def _require_scope(**scope: str) -> None:
        for field, value in scope.items():
            if not str(value or "").strip():
                raise ValueError(f"{field}_required")

    @staticmethod
    def _revision_id(*, artifact_id: str, revision: int, digest: str) -> str:
        seed = f"{artifact_id}:{revision}:{digest}".encode("utf-8")
        return f"pcat-{hashlib.sha256(seed).hexdigest()[:24]}"


__all__ = ["PlanningCategoryPipelineService", "build_category_repair_prompt"]
