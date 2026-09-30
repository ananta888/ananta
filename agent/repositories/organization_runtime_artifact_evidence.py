"""Verified artifact-version reads and assignment evidence verification for handoffs."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from sqlmodel import select

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.db_models.blueprints import ArtifactDB, ArtifactVersionDB
from agent.db_models.tasks import TaskDB
from agent.db_models.workers import WorkerJobDB, WorkerSlotLeaseDB
from agent.ports.artifact_handoff import VerifiedArtifactVersion
from agent.repositories.organization_runtime_support import (
    GROUNDING_REF,
    SessionFactory,
    default_session,
)


class SqlArtifactVersionReader:
    """Adapter over the goal graph and existing artifact/version tables.

    The goal graph owns goal/provenance/verification membership; the SQL
    artifact tables own immutable version bytes and their SHA-256.  A handoff
    is released only when both authorities name the same digest.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        goal_artifacts: GoalArtifactService | None = None,
    ) -> None:
        self._session_factory = session_factory or default_session
        self._goal_artifacts = goal_artifacts or GoalArtifactService()

    def get_verified_version(
        self,
        *,
        goal_id: str,
        artifact_id: str,
        version: str,
    ) -> VerifiedArtifactVersion | None:
        try:
            graph = self._goal_artifacts.find_goal_graph(goal_id)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if graph is None:
            return None
        candidates = [
            dict(row)
            for row in list(graph.get("output_artifacts") or [])
            if isinstance(row, Mapping)
            and (
                str(row.get("output_artifact_id") or "") == artifact_id
                or str(row.get("artifact_ref") or "") == artifact_id
            )
        ]
        if len(candidates) != 1:
            return None
        output = candidates[0]
        if str(output.get("goal_id") or "") != goal_id or str(output.get("status") or "") != "verified":
            return None
        with self._session_factory() as session:
            artifact = session.get(ArtifactDB, artifact_id)
            if artifact is None:
                return None
            statement = select(ArtifactVersionDB).where(ArtifactVersionDB.artifact_id == artifact_id)
            rows = session.exec(statement).all()
            matching = [row for row in rows if str(row.id) == version or str(row.version_number) == version]
            if len(matching) != 1:
                return None
            selected = matching[0]
            output_extensions = output.get("extensions")
            declared_version_ref = (
                str(output_extensions.get("artifact_version_ref") or "")
                if isinstance(output_extensions, Mapping)
                else ""
            )
            if declared_version_ref:
                if declared_version_ref != str(selected.id):
                    return None
            elif str(artifact.latest_version_id or "") != str(selected.id):
                return None
            output_hash = str(output.get("content_hash") or "").removeprefix("sha256:")
            if not output_hash or output_hash != str(selected.sha256 or ""):
                return None
            evidence_refs, context_scope_refs = self._grounding_refs(
                graph=graph,
                output=output,
            )
            return VerifiedArtifactVersion(
                artifact_id=artifact_id,
                version=version,
                digest=f"sha256:{selected.sha256}",
                verification_status="hub_verified",
                evidence_refs=evidence_refs,
                context_scope_refs=context_scope_refs,
                producer_task_id=(str(output.get("task_id") or "") or None),
            )

    @staticmethod
    def _grounding_refs(
        *,
        graph: Mapping[str, Any],
        output: Mapping[str, Any],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        evidence: set[str] = set()
        context: set[str] = set()

        def collect_extensions(value: Mapping[str, Any]) -> None:
            extensions = value.get("extensions")
            if not isinstance(extensions, Mapping):
                return
            evidence.update(
                str(item)
                for item in list(extensions.get("evidence_refs") or [])
                if GROUNDING_REF.fullmatch(str(item)) is not None
            )
            context.update(str(item) for item in list(extensions.get("context_scope_refs") or []) if str(item))

        collect_extensions(output)
        usage_refs = {str(value) for value in list(output.get("input_usage_refs") or []) if str(value)}
        provenance_id = str(output.get("provenance_id") or "")
        for raw in list(dict(graph.get("extensions") or {}).get("execution_provenance") or []):
            if not isinstance(raw, Mapping) or str(raw.get("provenance_id") or "") != provenance_id:
                continue
            usage_refs.update(str(value) for value in list(raw.get("input_usage_refs") or []) if str(value))
            collect_extensions(raw)
        for raw in list(graph.get("source_usages") or []):
            if not isinstance(raw, Mapping) or str(raw.get("usage_id") or "") not in usage_refs:
                continue
            artifact_ref = str(raw.get("artifact_ref") or "")
            if GROUNDING_REF.fullmatch(artifact_ref) is not None:
                evidence.add(artifact_ref)
        return tuple(sorted(evidence)), tuple(sorted(context))


class SqlAssignmentEvidenceVerifier:
    """Fail-closed exact allowlist check bound to a Hub dispatch lease."""

    def __init__(self, *, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory or default_session

    def verify(
        self,
        *,
        evidence_refs: tuple[str, ...],
        context_scope_refs: tuple[str, ...],
        assignment_id: str,
        dispatch_lease_id: str,
    ) -> tuple[bool, tuple[str, ...]]:
        with self._session_factory() as session:
            job = session.get(WorkerJobDB, dispatch_lease_id)
            slot_lease = (
                session.get(WorkerSlotLeaseDB, str(job.slot_lease_id or ""))
                if job is not None and job.slot_lease_id
                else None
            )
            if (
                job is None
                or str(job.subtask_id or "") != assignment_id
                or not job.parent_task_id
                or str(job.status or "") not in {"delegated", "running"}
                or job.finished_at is not None
                or (job.slot_lease_id and slot_lease is None)
                or (
                    slot_lease is not None
                    and (
                        slot_lease.status != "active"
                        or float(slot_lease.deadline_at) <= time.time()
                        or slot_lease.released_at is not None
                        or str(slot_lease.parent_task_id or "") not in {"", str(job.parent_task_id or "")}
                        or str(slot_lease.worker_job_id or "") not in {"", dispatch_lease_id}
                    )
                )
            ):
                return False, ("handoff_dispatch_lease_binding_invalid",)
            task = session.get(TaskDB, job.parent_task_id)
            if task is None:
                return False, ("handoff_source_task_not_found",)
            if str(task.current_worker_job_id or "") != str(job.id) or str(task.status or "").strip().lower() in {
                "completed",
                "failed",
                "cancelled",
                "verification_failed",
                "skipped",
                "aborted",
                "timeout",
                "archived",
            }:
                return False, ("handoff_dispatch_lease_stale",)
            context = dict(task.worker_execution_context or {})
            allowed_evidence = {
                str(value)
                for key in ("allowed_source_refs", "allowed_run_refs")
                for value in list(context.get(key) or [])
                if str(value)
            }
            allowed_context = {str(value) for value in list(context.get("allowed_context_refs") or []) if str(value)}
        reasons: list[str] = []
        if not evidence_refs or any(
            GROUNDING_REF.fullmatch(reference) is None or reference not in allowed_evidence
            for reference in evidence_refs
        ):
            reasons.append("handoff_evidence_allowlist_mismatch")
        if not context_scope_refs or any(reference not in allowed_context for reference in context_scope_refs):
            reasons.append("handoff_context_scope_not_allowed")
        return not reasons, tuple(reasons)


__all__ = [
    "SqlArtifactVersionReader",
    "SqlAssignmentEvidenceVerifier",
]
