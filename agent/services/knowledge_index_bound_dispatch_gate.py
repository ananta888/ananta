"""Mandatory Hub gate before a bound knowledge-index job reaches a Worker.

``KnowledgeIndexBoundDispatchGate`` validates the durable execution binding,
the resolved destination, the remaining lease window and the source-access
capability, then atomically claims the execute phase.  It never transports
anything itself: the Hub dispatcher sends the returned context.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from agent.services.knowledge_index_job_contract import (
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
)
from agent.services.knowledge_index_job_ports import (
    KnowledgeIndexJobRepositoryPort,
)
from ananta_contracts.knowledge_index_execution import (
    KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS,
    KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON,
)


def verify_destination_binding(
    resolver: Any,
    *,
    destination_selection: Mapping[str, Any] | None,
    preview_destination_digest: str,
    expected_destination_id: Any,
    expected_worker_id: Any,
) -> None:
    """Require the dispatch selection to resolve to the admitted destination."""

    if not isinstance(destination_selection, Mapping):
        raise ValueError(
            "knowledge_index_destination_selection_required"
        )
    from agent.services.source_destination_resolution import (
        DestinationSelection,
    )

    resolved = resolver.verify_dispatch_binding(
        preview_destination_digest=preview_destination_digest,
        dispatch_selection=DestinationSelection(
            **dict(destination_selection)
        ),
    )
    if resolved.descriptor.destination_id != expected_destination_id:
        raise ValueError(
            "knowledge_index_destination_id_changed"
        )
    if resolved.descriptor.worker_id != expected_worker_id:
        raise ValueError(
            "knowledge_index_destination_assignment_mismatch"
        )


def persist_bound_execution_envelope(
    repository: KnowledgeIndexJobRepositoryPort,
    *,
    job_id: str,
    expected_envelope: Mapping[str, Any],
    envelope: Mapping[str, Any],
) -> None:
    atomic_replace = getattr(
        repository,
        "replace_bound_knowledge_index_envelope",
        None,
    )
    if not callable(atomic_replace):
        raise RuntimeError(
            "knowledge_index_atomic_task_envelope_repository_required"
        )
    atomic_replace(
        str(job_id),
        expected_envelope=dict(expected_envelope),
        replacement_envelope=dict(envelope),
    )


def build_source_access_request(
    *,
    envelope: Mapping[str, Any],
    intent: Mapping[str, Any],
) -> Any:
    """Project the admitted envelope and queue intent into one request."""

    from agent.services.source_access_enforcement import (
        SourceAccessRequest,
    )
    from ananta_contracts.source_control import (
        GrantOperation,
        GrantTransformation,
    )

    authority = dict(envelope.get("authority_binding") or {})
    assignment = dict(envelope.get("assignment") or {})
    manifest = dict(envelope.get("file_manifest") or {})
    return SourceAccessRequest(
        tenant_id=str(authority.get("tenant_id") or ""),
        project_id=str(authority.get("project_id") or ""),
        source_revision_id=str(
            authority.get("source_revision_id") or ""
        ),
        source_revision_digest=str(
            authority.get("source_revision_digest") or ""
        ),
        destination_id=str(
            authority.get("destination_id") or ""
        ),
        destination_digest=str(
            authority.get("destination_digest") or ""
        ),
        source_access_grant_id=str(
            authority.get("source_access_grant_id") or ""
        ),
        source_access_grant_digest=str(
            authority.get("source_access_grant_digest") or ""
        ),
        operation=GrantOperation(
            str(intent.get("operation") or "")
        ),
        transformation=GrantTransformation(
            str(intent.get("transformation") or "")
        ),
        purpose=str(intent.get("purpose") or ""),
        policy_version=str(intent.get("policy_version") or ""),
        policy_digest=str(
            authority.get("policy_snapshot_digest") or ""
        ),
        manifest_id=str(manifest.get("manifest_id") or ""),
        manifest_digest=str(
            manifest.get("manifest_digest") or ""
        ),
        assignment_id=str(
            assignment.get("assignment_id") or ""
        ),
        lease_id=str(assignment.get("lease_id") or ""),
    )


class KnowledgeIndexBoundDispatchGate:
    """Authorize one bound execution for proposal or execute dispatch."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], KnowledgeIndexJobRepositoryPort],
        execution_binding_service: Any | None,
        destination_resolution_service: Any | None = None,
        source_access_enforcement_service: Any | None = None,
        allow_legacy_unresolved_destination: bool = False,
        allow_legacy_unsigned_source_dispatch: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository_provider = repository_provider
        self._execution_binding_service = execution_binding_service
        self._destination_resolution_service = (
            destination_resolution_service
        )
        self._source_access_enforcement_service = (
            source_access_enforcement_service
        )
        self._allow_legacy_unresolved_destination = bool(
            allow_legacy_unresolved_destination
        )
        self._allow_legacy_unsigned_source_dispatch = bool(
            allow_legacy_unsigned_source_dispatch
        )
        self._clock = clock

    def authorize(
        self,
        *,
        job_id: str,
        authenticated_worker_id: str,
        destination_selection: Mapping[str, Any] | None = None,
        dispatch_phase: str = "execute",
    ) -> dict[str, Any]:
        """Return v2 execution context only after the mandatory Hub gate."""

        normalized_dispatch_phase = str(dispatch_phase or "").strip().lower()
        if normalized_dispatch_phase not in {"propose", "execute"}:
            raise ValueError("knowledge_index_dispatch_phase_invalid")

        task = self._repository_provider().get_by_id(str(job_id))
        if task is None:
            raise ValueError("knowledge_index_job_not_found")
        raw_task = (
            task.model_dump() if hasattr(task, "model_dump") else dict(task)
        )
        context = dict(raw_task.get("worker_execution_context") or {})
        envelope = dict(context.get("knowledge_index_job") or {})
        if (
            str(envelope.get("schema") or "")
            != KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
        ):
            raise ValueError("knowledge_index_execution_job_schema_invalid")
        service = self._execution_binding_service
        if service is None:
            raise RuntimeError(
                "knowledge_index_execution_binding_service_unavailable"
            )
        authorized = service.validate_before_dispatch(
            job_id=str(job_id),
            authenticated_worker_id=str(authenticated_worker_id),
        )
        current_envelope = authorized.job.to_wire()
        persisted_enforcement_manifest = envelope.pop(
            "source_access_enforcement_manifest",
            None,
        )
        if envelope != current_envelope:
            raise ValueError(
                "knowledge_index_execution_queue_context_stale"
            )
        exact_dispatch_replay = bool(
            normalized_dispatch_phase == "execute"
            and str(getattr(authorized, "state", "assigned"))
            .strip()
            .lower()
            == "running"
        )
        if (
            exact_dispatch_replay
            and persisted_enforcement_manifest is None
        ):
            raise ValueError(
                "knowledge_index_running_dispatch_manifest_missing"
            )
        authority = dict(
            current_envelope.get("authority_binding") or {}
        )
        assignment = dict(current_envelope.get("assignment") or {})
        resolver = self._destination_resolution_service
        if resolver is not None:
            verify_destination_binding(
                resolver,
                destination_selection=destination_selection,
                preview_destination_digest=str(
                    authority.get("destination_digest") or ""
                ),
                expected_destination_id=authority.get("destination_id"),
                expected_worker_id=assignment.get("worker_id"),
            )
        elif not self._allow_legacy_unresolved_destination:
            raise RuntimeError(
                "knowledge_index_destination_resolution_unavailable"
            )

        if normalized_dispatch_phase == "propose":
            # Proposal generation is metadata-only.  Source grants and payload
            # capabilities are deliberately withheld until the Hub atomically
            # claims the execute phase.
            return {"knowledge_index_job": current_envelope}

        required_dispatch_window_ms = (
            self._assert_dispatch_runtime_window(
                current_envelope,
                exact_replay=exact_dispatch_replay,
            )
        )

        enforcement = self._source_access_enforcement_service
        if enforcement is None:
            if persisted_enforcement_manifest is not None:
                raise RuntimeError(
                    "knowledge_index_source_access_enforcement_unavailable"
                )
            if not self._allow_legacy_unsigned_source_dispatch:
                raise RuntimeError(
                    "knowledge_index_source_access_enforcement_unavailable"
                )
            worker_envelope = current_envelope
            if not exact_dispatch_replay:
                self._claim_execution_dispatch(
                    service=service,
                    authorized_record=authorized,
                    job_id=str(job_id),
                    authenticated_worker_id=str(authenticated_worker_id),
                    dispatch_phase=normalized_dispatch_phase,
                )
            return {"knowledge_index_job": worker_envelope}

        intent = dict(context.get("source_access_intent") or {})
        if str(intent.get("policy_version") or "") != str(
            authority.get("policy_snapshot_id") or ""
        ):
            raise ValueError(
                "knowledge_index_source_policy_binding_mismatch"
            )
        request = build_source_access_request(
            envelope=current_envelope,
            intent=intent,
        )
        dispatch_time = datetime.fromtimestamp(
            float(self._clock()),
            tz=timezone.utc,
        )
        if persisted_enforcement_manifest is not None:
            if not isinstance(persisted_enforcement_manifest, Mapping):
                raise ValueError(
                    "knowledge_index_source_access_manifest_invalid"
                )
            verified_manifest = enforcement.validate_delegated_manifest(
                persisted_enforcement_manifest,
                request,
                now=dispatch_time,
                minimum_remaining_ms=required_dispatch_window_ms,
            )
            worker_envelope = {
                **current_envelope,
                "source_access_enforcement_manifest": asdict(
                    verified_manifest
                ),
            }
            if not exact_dispatch_replay:
                self._claim_execution_dispatch(
                    service=service,
                    authorized_record=authorized,
                    job_id=str(job_id),
                    authenticated_worker_id=str(authenticated_worker_id),
                    dispatch_phase=normalized_dispatch_phase,
                )
            return {"knowledge_index_job": worker_envelope}
        source_dispatch = enforcement.authorize(
            request,
            now=dispatch_time,
            allow_exact_consumption_recovery=True,
            minimum_remaining_ms=required_dispatch_window_ms,
        )
        worker_envelope = {
            **current_envelope,
            "source_access_enforcement_manifest": asdict(
                source_dispatch.manifest
            ),
        }
        persist_bound_execution_envelope(
            self._repository_provider(),
            job_id=job_id,
            expected_envelope=current_envelope,
            envelope=worker_envelope,
        )
        self._claim_execution_dispatch(
            service=service,
            authorized_record=authorized,
            job_id=str(job_id),
            authenticated_worker_id=str(authenticated_worker_id),
            dispatch_phase=normalized_dispatch_phase,
        )
        return {"knowledge_index_job": worker_envelope}

    def _assert_dispatch_runtime_window(
        self,
        envelope: Mapping[str, Any],
        *,
        exact_replay: bool = False,
    ) -> int:
        """Require full execution time or a bounded exact-replay window."""

        assignment = envelope.get("assignment")
        resources = envelope.get("resources")
        if not isinstance(assignment, Mapping) or not isinstance(
            resources,
            Mapping,
        ):
            raise ValueError(
                KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON
            )
        lease_expires_epoch_ms = assignment.get(
            "lease_expires_epoch_ms"
        )
        max_runtime_seconds = resources.get("max_runtime_seconds")
        if (
            isinstance(lease_expires_epoch_ms, bool)
            or not isinstance(lease_expires_epoch_ms, int)
            or isinstance(max_runtime_seconds, bool)
            or not isinstance(max_runtime_seconds, int)
            or max_runtime_seconds < 1
        ):
            raise ValueError(
                KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON
            )
        try:
            now_epoch_ms = int(float(self._clock()) * 1000)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON
            ) from exc
        required_window_seconds = (
            KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS
            if exact_replay
            else (
                max_runtime_seconds
                + KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS
            )
        )
        required_window_ms = required_window_seconds * 1000
        if lease_expires_epoch_ms - now_epoch_ms < required_window_ms:
            raise ValueError(
                KNOWLEDGE_INDEX_DISPATCH_WINDOW_INSUFFICIENT_REASON
            )
        return required_window_ms

    @staticmethod
    def _claim_execution_dispatch(
        *,
        service: Any,
        authorized_record: Any,
        job_id: str,
        authenticated_worker_id: str,
        dispatch_phase: str,
    ) -> None:
        if dispatch_phase != "execute":
            return
        claim = getattr(service, "claim_dispatch", None)
        if not callable(claim):
            raise RuntimeError(
                "knowledge_index_dispatch_claim_service_unavailable"
            )
        claim(
            job_id=job_id,
            authenticated_worker_id=authenticated_worker_id,
            expected_lock_version=int(authorized_record.lock_version),
        )


__all__ = [
    "KnowledgeIndexBoundDispatchGate",
    "build_source_access_request",
    "persist_bound_execution_envelope",
    "verify_destination_binding",
]
