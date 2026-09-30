"""Hub admission of immutable source-revision (v2) knowledge-index jobs.

``KnowledgeIndexBoundJobAdmission`` validates the source-access intent,
destination and assignment, stores the content-addressed payload and admits
exactly one execution record through the binding service.  Projecting the
Hub queue task stays with ``KnowledgeIndexJobService``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.services.knowledge_index_bound_dispatch_gate import (
    verify_destination_binding,
)
from agent.services.knowledge_index_bound_task_projection import (
    validate_bound_task_projection,
)
from agent.services.knowledge_index_job_contract import (
    MAX_JOB_PAYLOAD_BYTES,
    canonical_json,
    fingerprint,
    normalize_graph_visual_metrics_options,
)
from agent.services.knowledge_index_job_payload_storage import (
    KnowledgeIndexJobPayloadStorage,
)
from agent.services.knowledge_index_job_ports import (
    KnowledgeIndexJobRepositoryPort,
    KnowledgeIndexWorkerDirectoryPort,
)


@dataclass(frozen=True)
class KnowledgeIndexBoundAdmission:
    """One admitted execution plus the queue intent bound into its payload."""

    record: Any
    assigned_worker_url: str
    destination_selection: dict[str, Any] = field(default_factory=dict)
    source_access_intent: dict[str, Any] = field(default_factory=dict)


class KnowledgeIndexBoundJobAdmission:
    """Validate and admit one bound source-revision execution."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], KnowledgeIndexJobRepositoryPort],
        payload_storage: KnowledgeIndexJobPayloadStorage,
        execution_binding_service: Any | None,
        destination_resolution_service: Any | None = None,
        worker_directory: KnowledgeIndexWorkerDirectoryPort | None = None,
        allow_legacy_unresolved_destination: bool = False,
    ) -> None:
        self._repository_provider = repository_provider
        self._payload_storage = payload_storage
        self._execution_binding_service = execution_binding_service
        self._destination_resolution_service = (
            destination_resolution_service
        )
        self._worker_directory = worker_directory
        self._allow_legacy_unresolved_destination = bool(
            allow_legacy_unresolved_destination
        )

    def admit(
        self,
        *,
        hub_task_id: str,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        source_revision_id: str,
        source_revision_digest: str,
        admission_digest: str,
        policy_snapshot_id: str,
        policy_snapshot_digest: str,
        destination_id: str,
        destination_digest: str,
        source_access_grant_id: str,
        source_access_grant_digest: str,
        files: list[dict[str, Any]],
        resource_budget: Mapping[str, Any],
        assignment: Mapping[str, Any],
        destination_selection: Mapping[str, Any] | None = None,
        idempotency_key: str,
        source_scope: str,
        source_id: str,
        records: list[dict[str, Any]],
        created_by: str,
        profile_name: str = "default",
        source_operation: str = "index",
        source_transformation: str = "redacted",
        source_purpose: str = "knowledge-index",
        source_policy_version: str | None = None,
    ) -> KnowledgeIndexBoundAdmission:
        """Admit one execution record; the owner projects its Hub task."""

        from agent.services.knowledge_index_execution_binding_service import (
            CurrentKnowledgeIndexAuthority,
        )
        from ananta_contracts.knowledge_index_execution import (
            KnowledgeIndexExecutionAssignment,
            KnowledgeIndexResourceBudget,
        )

        service = self._execution_binding_service
        if service is None:
            raise RuntimeError(
                "knowledge_index_execution_binding_service_unavailable"
            )
        raw_key = str(idempotency_key or "")
        if not raw_key:
            raise ValueError("knowledge_index_idempotency_key_required")
        idempotency_key_digest = hashlib.sha256(
            raw_key.encode("utf-8")
        ).hexdigest()
        effective_source_policy_version = str(
            source_policy_version or policy_snapshot_id
        )
        if effective_source_policy_version != str(policy_snapshot_id):
            raise ValueError(
                "knowledge_index_source_policy_binding_mismatch"
            )
        from ananta_contracts.source_control import (
            GrantOperation,
            GrantTransformation,
        )

        try:
            normalized_operation = GrantOperation(
                str(source_operation)
            ).value
            normalized_transformation = GrantTransformation(
                str(source_transformation)
            ).value
        except ValueError:
            raise ValueError(
                "knowledge_index_source_access_intent_invalid"
            ) from None
        normalized_purpose = str(source_purpose or "").strip()
        if not normalized_purpose or len(normalized_purpose) > 160:
            raise ValueError(
                "knowledge_index_source_access_intent_invalid"
            )
        source_access_intent = {
            "operation": normalized_operation,
            "transformation": normalized_transformation,
            "purpose": normalized_purpose,
            "policy_version": effective_source_policy_version,
        }
        assignment_contract = (
            KnowledgeIndexExecutionAssignment.model_validate(
                dict(assignment)
            )
        )
        resource_contract = KnowledgeIndexResourceBudget.model_validate(
            dict(resource_budget)
        )
        normalized_destination_selection: dict[str, Any] = {}
        resolver = self._destination_resolution_service
        if resolver is not None:
            verify_destination_binding(
                resolver,
                destination_selection=destination_selection,
                preview_destination_digest=str(destination_digest),
                expected_destination_id=str(destination_id),
                expected_worker_id=assignment_contract.worker_id,
            )
            normalized_destination_selection = dict(
                destination_selection
            )
        elif not self._allow_legacy_unresolved_destination:
            raise RuntimeError(
                "knowledge_index_destination_resolution_unavailable"
            )
        elif destination_selection is not None:
            if not isinstance(destination_selection, Mapping):
                raise ValueError(
                    "knowledge_index_destination_selection_invalid"
                )
            normalized_destination_selection = dict(
                destination_selection
            )
        if self._worker_directory is None:
            raise RuntimeError(
                "knowledge_index_worker_directory_unavailable"
            )
        # Resolve the immutable assignment before storing a payload or
        # admitting an execution record. A disappeared/ambiguous Worker must
        # not leave an orphaned capability or binding behind.
        assigned_worker_url = self._worker_directory.resolve_worker_url(
            assignment_contract.worker_id
        )
        if not isinstance(records, list) or any(
            not isinstance(item, Mapping) for item in records
        ):
            raise ValueError("source_records_invalid")
        normalized_records = [dict(item) for item in records]
        payload = {
            "source_scope": str(source_scope),
            "source_id": f"bound-source:{source_revision_id}",
            "records": normalized_records,
            "source_metadata": {
                "source_revision_id": source_revision_id,
                "source_revision_digest": source_revision_digest,
                "connection_source_id": str(source_id),
                # Queue-only intent participates in the immutable payload
                # identity, so replaying the same key with altered dispatch
                # semantics is rejected even after a queue-write failure.
                "dispatch_context_digest": fingerprint(
                    {
                        "destination_selection": (
                            normalized_destination_selection
                        ),
                        "source_access_intent": source_access_intent,
                    }
                ),
            },
            "codecompass_prerender": False,
            "graph_visual_metrics": normalize_graph_visual_metrics_options(
                None
            ),
        }
        content = canonical_json(payload)
        if len(content) > MAX_JOB_PAYLOAD_BYTES:
            raise ValueError("knowledge_index_job_payload_too_large")
        payload_fingerprint = hashlib.sha256(content).hexdigest()
        payload_reference = self._payload_storage.prepare_reference(
            content=content,
            fingerprint=payload_fingerprint,
        )
        authority_contract = CurrentKnowledgeIndexAuthority(
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=source_revision_id,
            source_revision_digest=source_revision_digest,
            admission_digest=admission_digest,
            policy_snapshot_id=policy_snapshot_id,
            policy_snapshot_digest=policy_snapshot_digest,
            destination_id=destination_id,
            destination_digest=destination_digest,
            source_access_grant_id=source_access_grant_id,
            source_access_grant_digest=source_access_grant_digest,
        )
        prepared = service.prepare_issue(
            hub_task_id=hub_task_id,
            owner_id=owner_id,
            idempotency_key_digest=idempotency_key_digest,
            authority=authority_contract,
            files=files,
            resources=resource_contract,
            payload_artifact_ref=payload_reference,
            assignment=assignment_contract,
            scope_id=str(source_id),
            source_scope=str(source_scope),
            profile_name=str(profile_name),
            created_by=str(created_by),
        )
        preexisting_task = self._repository_provider().get_by_id(
            prepared.job.job_id
        )
        if preexisting_task is not None:
            validate_bound_task_projection(
                preexisting_task,
                record=prepared,
                assigned_worker_url=assigned_worker_url,
                destination_selection=normalized_destination_selection,
                source_access_intent=source_access_intent,
            )
        existing = service.get_by_idempotency(
            tenant_id=tenant_id,
            project_id=project_id,
            idempotency_key_digest=idempotency_key_digest,
        )
        if existing is not None:
            if not service.same_submission(existing, prepared):
                from agent.services.knowledge_index_execution_binding_service import (
                    KnowledgeIndexExecutionBindingError,
                )

                raise KnowledgeIndexExecutionBindingError(
                    "knowledge_index_execution_idempotency_conflict"
                )
            record = existing
        else:
            # Every deterministic contract and current-authority check runs
            # before the first external write. Admission rechecks authority to
            # close the race between payload storage and the SQL transaction.
            service.validate_prepared_issue(prepared)
            self._payload_storage.store_prepared(
                content=content,
                fingerprint=payload_fingerprint,
                created_by=str(created_by),
                expected_reference=payload_reference,
            )
            record = service.admit_prepared_issue(prepared)
        return KnowledgeIndexBoundAdmission(
            record=record,
            assigned_worker_url=assigned_worker_url,
            destination_selection=normalized_destination_selection,
            source_access_intent=source_access_intent,
        )


__all__ = [
    "KnowledgeIndexBoundAdmission",
    "KnowledgeIndexBoundJobAdmission",
]
