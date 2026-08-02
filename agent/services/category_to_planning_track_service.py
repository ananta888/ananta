from __future__ import annotations

import hashlib
import re
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlmodel import Session

from agent.db_models import (
    PlanningArtifactRevisionDB,
    PlanningLineageDB,
    TaskDB,
    WorkerJobDB,
)
from agent.services.planning_category_contract_service import stable_planning_digest
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
    planning_scope_lock,
)
from agent.services.planning_principal_identity_service import (
    canonical_planning_actor_id,
    canonical_planning_worker_id,
)
from agent.services.planning_track_contract_service import planning_contract_hash
from agent.services.planning_track_pipeline_service import (
    evaluate_planning_quality_gates,
    validate_planning_track_with_details,
    validate_summary_consistency,
)


class CategoryToPlanningTrackError(ValueError):
    def __init__(self, reason_code: str, details: Sequence[str] | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.details = tuple(details or ())


class CategoryToPlanningTrackService:
    """Derive validated Track artifacts from one promoted Category revision."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], PlanningControlUnitOfWork] | None = None,
    ) -> None:
        self._uow_factory = uow_factory or PlanningControlUnitOfWork

    def derive_tracks(
        self,
        *,
        category_revision_id: str,
        expected_category_digest: str,
        expected_policy_hash: str,
        track_candidates: Sequence[Mapping[str, Any]],
        exclusions: Mapping[str, str] | None,
        worker_id: str | None,
        assignment_id: str | None,
        dispatch_lease_id: str | None,
        prompt_hash: str,
        principal_id: str | None = None,
        idempotency_key: str | None = None,
        source_task_id: str | None = None,
        required_source_category_item_ids: Sequence[str] | None = None,
        result_payload_digest: str | None = None,
        require_authoritative_task: bool = False,
    ) -> dict[str, Any]:
        if not track_candidates:
            raise CategoryToPlanningTrackError("planning_track_candidates_required")
        if bool(str(worker_id or "").strip()) == bool(str(principal_id or "").strip()):
            raise CategoryToPlanningTrackError("planning_track_creator_identity_invalid")
        request_payload: dict[str, Any] = {
            "category_revision_id": category_revision_id,
            "expected_category_digest": expected_category_digest,
            "expected_policy_hash": expected_policy_hash,
            "track_candidates": list(track_candidates),
            "exclusions": dict(exclusions or {}),
        }
        if require_authoritative_task or source_task_id:
            request_payload.update(
                {
                    "source_task_id": str(source_task_id or ""),
                    "required_source_category_item_ids": list(required_source_category_item_ids or []),
                    "result_payload_digest": str(result_payload_digest or ""),
                }
            )
        request_digest = stable_planning_digest(request_payload)
        with planning_scope_lock(f"planning-track-derive:{category_revision_id}"), self._uow_factory() as uow:
            assert uow.planning is not None
            uow.planning.acquire_scope_lock(f"planning-track-derive:{category_revision_id}")
            category = uow.planning.get_revision(category_revision_id, for_update=True)
            if category is None or category.artifact_type != "planning_category_todo":
                raise CategoryToPlanningTrackError("category_revision_not_found")
            if category.status != "promoted":
                raise CategoryToPlanningTrackError("category_revision_not_promoted")
            if category.content_digest != str(expected_category_digest or ""):
                raise CategoryToPlanningTrackError("category_digest_mismatch")
            if category.policy_hash != str(expected_policy_hash or ""):
                raise CategoryToPlanningTrackError("category_policy_hash_stale")
            authoritative_task: TaskDB | None = None
            authoritative_job: WorkerJobDB | None = None
            authority_ceiling: Mapping[str, Any] | None = None
            if require_authoritative_task:
                if uow.session is None:
                    raise CategoryToPlanningTrackError("track_planning_authoritative_session_required")
                (
                    authoritative_task,
                    authoritative_job,
                    authoritative_binding,
                ) = validate_authoritative_track_planning_assignment(
                    uow.session,
                    category=category,
                    source_task_id=str(source_task_id or ""),
                    assignment_id=str(assignment_id or ""),
                    dispatch_lease_id=str(dispatch_lease_id or ""),
                    worker_id=str(worker_id or ""),
                    required_source_category_item_ids=list(required_source_category_item_ids or []),
                    prompt_hash=prompt_hash,
                    result_payload_digest=str(result_payload_digest or ""),
                )
                raw_ceiling = authoritative_binding.get("worker_authority_ceiling")
                authority_ceiling = dict(raw_ceiling) if isinstance(raw_ceiling, Mapping) else {}
            if idempotency_key:
                prior_rows = [
                    row
                    for row in uow.planning.list_revisions(
                        goal_id=category.goal_id,
                        organization_id=category.organization_id,
                        artifact_type="planning_track",
                    )
                    if row.parent_revision_id == category.id
                    and str(dict(row.execution_provenance or {}).get("derivation_idempotency_key") or "")
                    == str(idempotency_key)
                ]
                if prior_rows:
                    if any(
                        str(dict(row.execution_provenance or {}).get("derivation_request_digest") or "")
                        != request_digest
                        for row in prior_rows
                    ):
                        raise CategoryToPlanningTrackError("planning_track_derivation_idempotency_conflict")
                    expected_artifact_ids = {str(row.get("artifact_id") or "").strip() for row in track_candidates}
                    if (
                        len(expected_artifact_ids) != len(track_candidates)
                        or {row.artifact_id for row in prior_rows} != expected_artifact_ids
                    ):
                        raise CategoryToPlanningTrackError("planning_track_derivation_replay_incomplete")
                    self._mark_authoritative_result(
                        task=authoritative_task,
                        job=authoritative_job,
                        result_payload_digest=str(result_payload_digest or ""),
                        track_revision_ids=[row.id for row in prior_rows],
                        replayed=True,
                    )
                    return self._response(
                        category_revision_id=category_revision_id,
                        revisions=prior_rows,
                        exclusions=dict(exclusions or {}),
                        replayed=True,
                    )

            normalized, lineage_specs, issues = self._validate_candidates(
                category=category,
                candidates=track_candidates,
                exclusions=dict(exclusions or {}),
                required_source_category_item_ids=required_source_category_item_ids,
                authority_ceiling=authority_ceiling,
            )
            if issues:
                raise CategoryToPlanningTrackError("planning_track_derivation_invalid", issues)

            created: list[PlanningArtifactRevisionDB] = []
            revision_by_artifact: dict[str, PlanningArtifactRevisionDB] = {}
            for candidate in normalized:
                artifact_id = str(candidate["artifact_id"])
                payload = dict(candidate["payload"])
                digest = stable_planning_digest(payload)
                revision_number = uow.planning.next_revision_number(artifact_id=artifact_id)
                previous = uow.planning.latest_revision(artifact_id=artifact_id)
                if previous is not None and (
                    previous.artifact_type != "planning_track"
                    or previous.tenant_id != category.tenant_id
                    or previous.project_id != category.project_id
                    or previous.organization_id != category.organization_id
                    or previous.goal_id != category.goal_id
                    or (require_authoritative_task and previous.parent_revision_id != category.id)
                ):
                    raise CategoryToPlanningTrackError("planning_track_artifact_id_scope_conflict")
                creator_id = (
                    canonical_planning_actor_id(principal_id)
                    if principal_id
                    else canonical_planning_worker_id(worker_id)
                )
                revision = PlanningArtifactRevisionDB(
                    id=self._revision_id(artifact_id=artifact_id, revision=revision_number, digest=digest),
                    artifact_id=artifact_id,
                    revision=revision_number,
                    artifact_type="planning_track",
                    tenant_id=category.tenant_id,
                    project_id=category.project_id,
                    organization_id=category.organization_id,
                    goal_id=category.goal_id,
                    status="valid",
                    payload=payload,
                    content_digest=digest,
                    schema_ref="todos/todo.track.schema.json",
                    schema_hash=planning_contract_hash(),
                    prompt_hash=str(prompt_hash or ""),
                    policy_hash=category.policy_hash,
                    source_catalog_id=category.source_catalog_id,
                    source_catalog_hash=category.source_catalog_hash,
                    allowed_source_refs=list(category.allowed_source_refs or []),
                    allowed_run_refs=list(category.allowed_run_refs or []),
                    source_category_item_ids=list(candidate["source_category_item_ids"]),
                    execution_provenance={
                        "schema": "planning_execution_provenance.v1",
                        "worker_id": str(worker_id or "") or None,
                        "assignment_id": str(assignment_id or "") or None,
                        "dispatch_lease_id": str(dispatch_lease_id or "") or None,
                        "created_by_hub_actor": str(principal_id or "") or None,
                        "source_category_revision_id": category.id,
                        "source_category_digest": category.content_digest,
                        "source_task_id": str(source_task_id or "") or None,
                        "worker_result_payload_digest": str(result_payload_digest or "") or None,
                        "derivation_idempotency_key": str(idempotency_key or ""),
                        "derivation_request_digest": request_digest,
                    },
                    validation_result={
                        "valid": True,
                        "summary_recalculation_status": candidate["summary_recalculation_status"],
                        "quality_gate_warnings": list(candidate["quality_gate_warnings"]),
                    },
                    parent_revision_id=category.id,
                    supersedes_revision_id=str(getattr(previous, "id", "") or "") or None,
                    created_by=creator_id,
                    created_by_principal_id=creator_id,
                )
                uow.planning.add_revision(revision)
                created.append(revision)
                revision_by_artifact[artifact_id] = revision

            lineage_rows = [
                PlanningLineageDB(
                    tenant_id=category.tenant_id,
                    project_id=category.project_id,
                    organization_id=category.organization_id,
                    goal_id=category.goal_id,
                    category_revision_id=category.id,
                    track_revision_id=revision_by_artifact[spec["artifact_id"]].id,
                    source_category_item_id=spec["source_category_item_id"],
                    plan_task_id=spec["plan_task_id"],
                )
                for spec in lineage_specs
            ]
            uow.planning.add_lineage(lineage_rows)
            self._mark_authoritative_result(
                task=authoritative_task,
                job=authoritative_job,
                result_payload_digest=str(result_payload_digest or ""),
                track_revision_ids=[row.id for row in created],
                replayed=False,
            )

        return self._response(
            category_revision_id=category_revision_id,
            revisions=created,
            exclusions=dict(exclusions or {}),
            replayed=False,
        )

    @staticmethod
    def _response(
        *,
        category_revision_id: str,
        revisions: Sequence[PlanningArtifactRevisionDB],
        exclusions: Mapping[str, str],
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "category_revision_id": category_revision_id,
            "track_revisions": [
                {
                    "artifact_revision_id": row.id,
                    "artifact_id": row.artifact_id,
                    "revision": row.revision,
                    "content_digest": row.content_digest,
                    "status": row.status,
                    "source_category_item_ids": list(row.source_category_item_ids or []),
                }
                for row in sorted(
                    revisions,
                    key=lambda item: (item.artifact_id, item.revision, item.id),
                )
            ],
            "excluded_category_items": dict(exclusions),
            "materialized_task_ids": [],
            "replayed": replayed,
        }

    @staticmethod
    def _validate_candidates(  # noqa: C901 - one deterministic cross-track validation pass
        *,
        category: PlanningArtifactRevisionDB,
        candidates: Sequence[Mapping[str, Any]],
        exclusions: Mapping[str, str],
        required_source_category_item_ids: Sequence[str] | None = None,
        authority_ceiling: Mapping[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
        category_items = {
            str(item.get("id") or ""): dict(item)
            for group in list(category.payload.get("categories") or [])
            if isinstance(group, Mapping)
            for item in list(group.get("items") or [])
            if isinstance(item, Mapping) and str(item.get("id") or "")
        }
        issues: list[str] = []
        normalized: list[dict[str, Any]] = []
        artifact_ids: set[str] = set()
        ownership: dict[str, str] = {}
        task_to_item: dict[str, str] = {}
        task_dependencies: dict[str, set[str]] = {}
        lineage_specs: list[dict[str, str]] = []

        for candidate_index, raw_candidate in enumerate(candidates):
            artifact_id = str(raw_candidate.get("artifact_id") or "").strip()
            payload = dict(raw_candidate.get("payload") or {})
            if not artifact_id:
                issues.append(f"candidate_{candidate_index}:artifact_id_required")
                continue
            if artifact_id in artifact_ids:
                issues.append(f"planning_track_artifact_id_duplicate:{artifact_id}")
            artifact_ids.add(artifact_id)
            source_ids = [str(value) for value in list(payload.get("source_category_item_ids") or []) if str(value)]
            if not source_ids:
                issues.append(f"{artifact_id}:source_category_item_ids_required")
            for source_id in source_ids:
                if source_id not in category_items:
                    issues.append(f"{artifact_id}:source_category_item_unknown:{source_id}")
                if source_id in ownership:
                    issues.append(f"category_item_mapped_more_than_once:{source_id}")
                ownership[source_id] = artifact_id

            schema_issues = validate_planning_track_with_details(payload)
            if authority_ceiling is not None:
                issues.extend(
                    CategoryToPlanningTrackService._worker_authority_issues(
                        artifact_id=artifact_id,
                        payload=payload,
                        authority_ceiling=authority_ceiling,
                    )
                )
            summary = validate_summary_consistency(payload, repair_mode=True)
            repaired_payload = dict(summary.get("repaired_payload") or payload)
            quality = evaluate_planning_quality_gates(
                repaired_payload,
                large_goal_mode=bool(repaired_payload.get("large_goal_mode")),
                small_goal_mode=bool(repaired_payload.get("small_goal_mode")),
            )
            if schema_issues:
                issues.extend(f"{artifact_id}:{row.get('reason_code')}:{row.get('path')}" for row in schema_issues)
            if not bool(quality.get("ok")):
                issues.extend(
                    f"{artifact_id}:{row.get('reason_code')}:{row.get('path')}"
                    for row in list(quality.get("blocking_issues") or [])
                )

            for task in list(repaired_payload.get("tasks") or []):
                if not isinstance(task, Mapping):
                    continue
                task_id = str(task.get("id") or "").strip()
                raw_task_source_ids = list(task.get("source_category_item_ids") or [])
                if not raw_task_source_ids and str(task.get("source_category_item_id") or "").strip():
                    raw_task_source_ids = [str(task.get("source_category_item_id"))]
                task_source_ids = [str(value).strip() for value in raw_task_source_ids if str(value).strip()]
                if not task_id or not task_source_ids:
                    issues.append(f"{artifact_id}:task_lineage_required")
                    continue
                if len(set(task_source_ids)) != len(task_source_ids):
                    issues.append(f"{artifact_id}:task_source_category_item_duplicate:{task_id}")
                if task_id in task_to_item:
                    issues.append(f"plan_task_id_duplicate:{task_id}")
                for source_id in task_source_ids:
                    if source_id not in source_ids:
                        issues.append(f"{artifact_id}:task_source_outside_track_scope:{source_id}")
                task_to_item[task_id] = task_source_ids[0]
                task_dependencies[task_id] = {
                    str(dep).split(":", 1)[-1] for dep in list(task.get("depends_on") or []) if str(dep)
                }
                lineage_specs.extend(
                    {
                        "artifact_id": artifact_id,
                        "source_category_item_id": source_id,
                        "plan_task_id": task_id,
                    }
                    for source_id in task_source_ids
                )
            normalized.append(
                {
                    "artifact_id": artifact_id,
                    "payload": repaired_payload,
                    "source_category_item_ids": source_ids,
                    "summary_recalculation_status": str(summary.get("summary_recalculation_status") or "not_needed"),
                    "quality_gate_warnings": list(quality.get("warnings") or []),
                }
            )

        excluded = {str(key): str(reason or "").strip() for key, reason in exclusions.items()}
        for item_id, reason in excluded.items():
            if item_id not in category_items:
                issues.append(f"excluded_category_item_unknown:{item_id}")
            if not reason:
                issues.append(f"excluded_category_item_reason_required:{item_id}")
            if item_id in ownership:
                issues.append(f"category_item_both_mapped_and_excluded:{item_id}")
        required_scope = (
            {str(value) for value in required_source_category_item_ids or []}
            if required_source_category_item_ids is not None
            else {
                item_id
                for item_id, item in category_items.items()
                if str(item.get("status") or "").strip().lower() != "deferred"
            }
        )
        if required_source_category_item_ids is not None:
            if not required_scope or not required_scope.issubset(category_items):
                issues.append("track_planning_category_scope_invalid")
            outside_scope = (set(ownership) | set(excluded)) - required_scope
            issues.extend(f"track_planning_result_scope_expansion:{item_id}" for item_id in sorted(outside_scope))
        uncovered = sorted(required_scope - set(ownership) - set(excluded))
        issues.extend(f"category_item_uncovered:{item_id}" for item_id in uncovered)

        tasks_by_item: dict[str, set[str]] = {}
        for spec in lineage_specs:
            tasks_by_item.setdefault(spec["source_category_item_id"], set()).add(spec["plan_task_id"])
        for item_id in ownership:
            if not tasks_by_item.get(item_id):
                issues.append(f"category_item_has_no_track_task:{item_id}")
        for item_id, item in category_items.items():
            if item_id not in ownership:
                continue
            for parent_item in list(item.get("depends_on") or []):
                parent_id = str(parent_item or "")
                if parent_id not in ownership:
                    continue
                parent_tasks = tasks_by_item.get(parent_id, set())
                child_tasks = tasks_by_item.get(item_id, set())
                translated = any(task_dependencies.get(child_task, set()) & parent_tasks for child_task in child_tasks)
                if not translated:
                    issues.append(f"category_dependency_not_translated:{parent_id}->{item_id}")

                inverted = any(task_dependencies.get(parent_task, set()) & child_tasks for parent_task in parent_tasks)
                if inverted:
                    issues.append(f"category_dependency_inverted:{parent_id}->{item_id}")

        known_task_ids = set(task_dependencies)
        for task_id, dependencies in task_dependencies.items():
            if task_id in dependencies:
                issues.append(f"planning_task_dependency_self:{task_id}")
            issues.extend(
                f"planning_task_dependency_unknown:{task_id}->{dependency}"
                for dependency in sorted(dependencies - known_task_ids)
            )
        incoming = {task_id: 0 for task_id in known_task_ids}
        outgoing = {task_id: [] for task_id in known_task_ids}
        for task_id, dependencies in task_dependencies.items():
            for dependency in dependencies & known_task_ids:
                outgoing[dependency].append(task_id)
                incoming[task_id] += 1
        queue = deque(sorted(task_id for task_id, count in incoming.items() if count == 0))
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for child in sorted(outgoing[current]):
                incoming[child] -= 1
                if incoming[child] == 0:
                    queue.append(child)
        if visited != len(incoming):
            issues.append("planning_cross_track_dependency_cycle")
        return normalized, lineage_specs, issues

    @staticmethod
    def _worker_authority_issues(
        *,
        artifact_id: str,
        payload: Mapping[str, Any],
        authority_ceiling: Mapping[str, Any],
    ) -> list[str]:
        """Reject Worker-authored control-plane authority in Track payloads."""

        issues: list[str] = []
        forbidden_fields = {
            "organization_id",
            "unit_id",
            "team_id",
            "role_slot_id",
            "assignment_id",
            "agent_id",
            "agent_url",
            "worker_id",
            "worker_url",
            "requested_worker_id",
            "allowed_tools",
            "tool_rights",
            "budget",
            "budget_limits",
            "budget_estimate",
            "token_budget",
            "cost_budget",
        }
        allowed_capabilities = {
            str(value) for value in list(authority_ceiling.get("allowed_task_capabilities") or []) if str(value)
        }
        allowed_context_refs = {
            str(value) for value in list(authority_ceiling.get("allowed_context_refs") or []) if str(value)
        }
        grounding_ref_pattern = re.compile(r"\b(?:SRC|RUN)_[A-Za-z0-9_.:-]+\b")

        def visit(value: Any, path: str) -> None:
            if isinstance(value, Mapping):
                for raw_key, child in value.items():
                    key = str(raw_key)
                    child_path = f"{path}.{key}"
                    if key in forbidden_fields:
                        issues.append(f"{artifact_id}:worker_authority_expansion:{child_path}")
                        continue
                    if key == "required_capabilities":
                        requested = (
                            {str(item) for item in list(child or []) if str(item)}
                            if isinstance(child, Sequence) and not isinstance(child, (str, bytes))
                            else {"<invalid>"}
                        )
                        if not requested.issubset(allowed_capabilities):
                            issues.append(f"{artifact_id}:worker_capability_expansion:{child_path}")
                        continue
                    if key == "context_refs":
                        requested_refs = (
                            {str(item) for item in list(child or []) if str(item)}
                            if isinstance(child, Sequence) and not isinstance(child, (str, bytes))
                            else {"<invalid>"}
                        )
                        if not requested_refs.issubset(allowed_context_refs):
                            issues.append(f"{artifact_id}:worker_context_expansion:{child_path}")
                        continue
                    visit(child, child_path)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                for index, child in enumerate(value):
                    visit(child, f"{path}[{index}]")
            elif isinstance(value, str):
                unknown_refs = set(grounding_ref_pattern.findall(value)) - allowed_context_refs
                issues.extend(
                    f"{artifact_id}:worker_grounding_ref_unknown:{path}:{ref}" for ref in sorted(unknown_refs)
                )

        visit(payload, "payload")
        return issues

    @staticmethod
    def _mark_authoritative_result(
        *,
        task: TaskDB | None,
        job: WorkerJobDB | None,
        result_payload_digest: str,
        track_revision_ids: Sequence[str],
        replayed: bool,
    ) -> None:
        if task is None or job is None:
            return
        now = time.time()
        worker_context = dict(task.worker_execution_context or {})
        raw_binding = worker_context.get("planning_track_binding")
        binding = dict(raw_binding) if isinstance(raw_binding, Mapping) else {}
        prior_digest = str(binding.get("accepted_result_payload_digest") or "")
        if prior_digest and prior_digest != result_payload_digest:
            raise CategoryToPlanningTrackError("track_planning_result_idempotency_conflict")
        if prior_digest:
            return
        binding.update(
            {
                "accepted_result_payload_digest": result_payload_digest,
                "derived_track_revision_ids": sorted({str(value) for value in track_revision_ids if str(value)}),
            }
        )
        worker_context["planning_track_binding"] = binding
        task.worker_execution_context = worker_context
        task.status = "completed"
        task.updated_at = now
        task.history = [
            *list(task.history or []),
            {
                "timestamp": now,
                "status": "completed",
                "event_type": (
                    "organization_track_planning_result_recovered"
                    if replayed
                    else "organization_track_planning_result_accepted"
                ),
                "actor": "hub:organization_planning",
                "details": {
                    "result_payload_digest": result_payload_digest,
                    "track_revision_ids": sorted({str(value) for value in track_revision_ids if str(value)}),
                },
            },
        ]
        job.status = "completed"
        job.finished_at = job.finished_at or now
        job.updated_at = now

    @staticmethod
    def _revision_id(*, artifact_id: str, revision: int, digest: str) -> str:
        seed = f"{artifact_id}:{revision}:{digest}".encode("utf-8")
        return f"ptrk-{hashlib.sha256(seed).hexdigest()[:24]}"


def validate_authoritative_track_planning_assignment(
    session: Session,
    *,
    category: PlanningArtifactRevisionDB,
    source_task_id: str,
    assignment_id: str,
    dispatch_lease_id: str,
    worker_id: str,
    required_source_category_item_ids: Sequence[str],
    prompt_hash: str,
    result_payload_digest: str,
) -> tuple[TaskDB, WorkerJobDB, dict[str, Any]]:
    """Re-read the exact Task/WorkerJob binding inside the Track write UoW."""

    task = session.get(TaskDB, source_task_id)
    if task is None or task.task_kind != "planning_track_task":
        raise CategoryToPlanningTrackError("track_planning_task_not_found")
    if str(task.status or "") not in {
        "todo",
        "created",
        "assigned",
        "in_progress",
        "completed",
    }:
        raise CategoryToPlanningTrackError("track_planning_task_not_active")
    if (
        task.tenant_id != category.tenant_id
        or task.project_id != category.project_id
        or task.organization_id != category.organization_id
        or task.goal_id != category.goal_id
    ):
        raise CategoryToPlanningTrackError("track_planning_task_scope_mismatch")
    job = session.get(WorkerJobDB, dispatch_lease_id)
    if (
        job is None
        or str(job.parent_task_id or "") != task.id
        or str(job.subtask_id or "") != assignment_id
        or str(job.worker_url or "") != worker_id
        or str(task.current_worker_job_id or "") != job.id
    ):
        raise CategoryToPlanningTrackError("track_planning_assignment_invalid")
    raw_binding = dict(task.worker_execution_context or {}).get("planning_track_binding")
    if not isinstance(raw_binding, Mapping):
        raise CategoryToPlanningTrackError("track_planning_binding_missing")
    binding = dict(raw_binding)
    expected_source_ids = [str(value) for value in required_source_category_item_ids]
    category_source_ids = [
        str(item.get("id") or "").strip()
        for group in list(category.payload.get("categories") or [])
        if isinstance(group, Mapping)
        for item in list(group.get("items") or [])
        if isinstance(item, Mapping) and str(item.get("status") or "").strip().lower() != "deferred"
    ]
    if (
        any(not value for value in category_source_ids)
        or len(set(category_source_ids)) != len(category_source_ids)
        or expected_source_ids != sorted(category_source_ids)
    ):
        raise CategoryToPlanningTrackError("track_planning_category_scope_invalid")
    raw_ceiling = binding.get("worker_authority_ceiling")
    ceiling = dict(raw_ceiling) if isinstance(raw_ceiling, Mapping) else {}
    expected_context_refs = set(category.allowed_source_refs or []) | set(category.allowed_run_refs or [])
    if (
        binding.get("schema") != "organization_track_planning_binding.v1"
        or str(binding.get("category_revision_id") or "") != category.id
        or int(binding.get("category_revision") or 0) != category.revision
        or str(binding.get("category_digest") or "") != category.content_digest
        or str(binding.get("category_schema_hash") or "") != category.schema_hash
        or str(binding.get("policy_hash") or "") != category.policy_hash
        or str(binding.get("prompt_hash") or "") != str(prompt_hash or "")
        or binding.get("prompt_template_ref") != "prompts/planning/organization_track_planning.j2"
        or binding.get("result_schema") != "organization_track_planning_result.v1"
        or binding.get("result_payload_schema_ref") != "todos/todo.track.schema.json"
        or binding.get("result_digest_algorithm") != "sha256-canonical-json-v1"
        or list(binding.get("source_category_item_ids") or []) != expected_source_ids
        or str(binding.get("organization_id") or "") != category.organization_id
        or str(binding.get("goal_id") or "") != category.goal_id
        or str(binding.get("unit_id") or "") != str(task.unit_id or "")
        or str(binding.get("team_id") or "") != str(task.team_id or "")
        or str(binding.get("role_slot_id") or "") != str(task.role_slot_id or "")
        or str(binding.get("source_catalog_id") or "") != str(category.source_catalog_id or "")
        or str(binding.get("source_catalog_hash") or "") != str(category.source_catalog_hash or "")
        or list(binding.get("allowed_source_refs") or []) != list(category.allowed_source_refs or [])
        or list(binding.get("allowed_run_refs") or []) != list(category.allowed_run_refs or [])
        or list(ceiling.get("allowed_task_capabilities") or []) != []
        or list(ceiling.get("allowed_tools") or []) != []
        or set(ceiling.get("allowed_context_refs") or []) != expected_context_refs
        or ceiling.get("worker_controls_routing") is not False
        or ceiling.get("worker_controls_budget") is not False
    ):
        raise CategoryToPlanningTrackError("track_planning_binding_stale")
    if stable_planning_digest(dict(binding.get("source_category_todo") or {})) != category.content_digest:
        raise CategoryToPlanningTrackError("track_planning_category_payload_stale")
    prior_digest = str(binding.get("accepted_result_payload_digest") or "")
    if prior_digest and prior_digest != str(result_payload_digest or ""):
        raise CategoryToPlanningTrackError("track_planning_result_idempotency_conflict")
    replay = bool(prior_digest)
    if replay:
        if str(task.status or "") != "completed" or str(job.status or "") != "completed":
            raise CategoryToPlanningTrackError("track_planning_result_receipt_inconsistent")
    elif (
        str(task.status or "") == "completed"
        or str(job.status or "") not in {"delegated", "running"}
        or job.finished_at is not None
    ):
        raise CategoryToPlanningTrackError("track_planning_dispatch_lease_inactive")
    return task, job, binding


__all__ = [
    "CategoryToPlanningTrackError",
    "CategoryToPlanningTrackService",
    "validate_authoritative_track_planning_assignment",
]
