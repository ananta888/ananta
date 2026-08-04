"""Worker-local admission and durable claim for governed index dispatches."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.common.errors import TransientError
from ananta_contracts.knowledge_index_dispatch import (
    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_HTTP_STATUS,
    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON,
    SOURCE_ACCESS_MANIFEST_FIELD,
    KnowledgeIndexDispatch,
    parse_knowledge_index_dispatch,
)
from ananta_contracts.knowledge_index_execution import (
    MAX_KNOWLEDGE_INDEX_WORKER_RESULT_BYTES,
    parse_execution_job,
    parse_execution_result,
)

BOUND_JOB_SCHEMA = "ananta.knowledge_index_execution_job.v2"
DISPATCH_RECEIPT_SCHEMA = "ananta.knowledge_index_worker_dispatch_receipt.v1"


class KnowledgeIndexWorkerDispatchResultPendingError(TransientError):
    """Stable wire error for an exact replay whose result is still pending."""

    def __init__(self) -> None:
        super().__init__(
            KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON,
            details={
                "reason_code": (
                    KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON
                ),
            },
            status_code=(
                KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_HTTP_STATUS
            ),
            retryable=True,
        )


class KnowledgeIndexWorkerDispatchReceiptLedgerPort(Protocol):
    """Worker-owned atomic replay and expiry boundary."""

    def claim(
        self,
        *,
        worker_id: str,
        job_id: str,
        assignment_id: str,
        lease_id: str,
        marker_digest: str,
        manifest_binding_digest: str,
        lease_expires_epoch_ms: int,
        grant_expires_at_epoch_ms: int,
    ) -> Mapping[str, Any]: ...

    def complete(
        self,
        *,
        worker_id: str,
        job_id: str,
        assignment_id: str,
        lease_id: str,
        marker_digest: str,
        manifest_binding_digest: str,
        result_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class PreparedKnowledgeIndexDispatch:
    base_job: Mapping[str, Any]
    executable_job: Mapping[str, Any]
    marker: KnowledgeIndexDispatch


@dataclass(frozen=True, slots=True)
class ClaimedKnowledgeIndexDispatch:
    executable_job: Mapping[str, Any]
    receipt: Mapping[str, Any]
    replayed_result: Mapping[str, Any] | None = None


class KnowledgeIndexWorkerDispatchAdmission:
    """Bind a Hub marker, then claim exactly one local v2 execution."""

    def __init__(
        self,
        *,
        receipt_ledger: KnowledgeIndexWorkerDispatchReceiptLedgerPort,
        worker_id: str,
    ) -> None:
        self._receipt_ledger = receipt_ledger
        self._worker_id = str(worker_id or "").strip()
        if not self._worker_id:
            raise ValueError("knowledge_index_authenticated_worker_missing")

    def prepare(
        self,
        *,
        task: Mapping[str, Any],
        job: Mapping[str, Any],
        request_data: Any,
        expected_phase: str,
    ) -> PreparedKnowledgeIndexDispatch:
        """Validate request metadata and attach only the signed manifest."""

        if str(job.get("schema") or "") != BOUND_JOB_SCHEMA:
            raise ValueError("knowledge_index_execution_binding_missing")
        job_id = str(job.get("job_id") or "").strip()
        if (
            str(task.get("id") or "").strip() != job_id
            or str(task.get("task_kind") or "").strip().lower() != "codecompass_index_build"
        ):
            raise ValueError("knowledge_index_dispatch_task_mismatch")
        raw_marker = (
            request_data.get("knowledge_index_dispatch")
            if isinstance(request_data, Mapping)
            else getattr(
                request_data,
                "knowledge_index_dispatch",
                None,
            )
        )
        marker = parse_knowledge_index_dispatch(
            raw_marker,
            expected_phase=expected_phase,
            expected_job_id=job_id,
        )
        base_job = copy.deepcopy(dict(job))
        existing_manifest = base_job.get(SOURCE_ACCESS_MANIFEST_FIELD)
        if marker.phase == "propose" and existing_manifest is not None:
            raise ValueError("knowledge_index_worker_base_manifest_unexpected")
        executable_job = copy.deepcopy(base_job)
        if marker.source_access_manifest is not None:
            marker_manifest = dict(marker.source_access_manifest)
            if existing_manifest is not None:
                if not isinstance(existing_manifest, Mapping) or dict(existing_manifest) != marker_manifest:
                    raise ValueError("knowledge_index_worker_manifest_mismatch")
            else:
                executable_job[SOURCE_ACCESS_MANIFEST_FIELD] = copy.deepcopy(marker_manifest)
        return PreparedKnowledgeIndexDispatch(
            base_job=base_job,
            executable_job=executable_job,
            marker=marker,
        )

    def claim_execute(
        self,
        *,
        task_id: str,
        prepared: PreparedKnowledgeIndexDispatch,
    ) -> ClaimedKnowledgeIndexDispatch:
        """Persist one replay fence, then expose the manifest transiently."""

        if prepared.marker.phase != "execute":
            raise ValueError("knowledge_index_worker_dispatch_phase_invalid")
        if str(task_id or "").strip() != prepared.marker.job_id:
            raise ValueError("knowledge_index_dispatch_task_mismatch")
        executable_job = dict(prepared.executable_job)
        binding = self._execution_binding(
            prepared=prepared,
            task_id=task_id,
        )
        try:
            receipt = dict(
                self._receipt_ledger.claim(
                    **binding,
                )
            )
        except ValueError as exc:
            if exc.args != (
                KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON,
            ):
                raise
            raise KnowledgeIndexWorkerDispatchResultPendingError() from exc
        expected_receipt = {
            "schema": DISPATCH_RECEIPT_SCHEMA,
            "job_id": prepared.marker.job_id,
            "phase": "execute",
            "worker_id": self._worker_id,
            "assignment_id": binding["assignment_id"],
            "lease_id": binding["lease_id"],
            "marker_digest": prepared.marker.marker_digest,
            "manifest_binding_digest": binding[
                "manifest_binding_digest"
            ],
        }
        replayed_result = self._validate_receipt(
            receipt,
            expected_receipt=expected_receipt,
            executable_job=executable_job,
        )
        return ClaimedKnowledgeIndexDispatch(
            executable_job=copy.deepcopy(executable_job),
            receipt=copy.deepcopy(receipt),
            replayed_result=copy.deepcopy(replayed_result),
        )

    def complete_execute_result(
        self,
        *,
        claimed: ClaimedKnowledgeIndexDispatch,
        result_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Publish a terminal result before the Worker returns it."""

        validated_result = self._validate_result_binding(
            claimed.executable_job,
            result_payload,
        )
        original_receipt = dict(claimed.receipt)
        completed_receipt = dict(
            self._receipt_ledger.complete(
                worker_id=str(
                    original_receipt.get("worker_id") or ""
                ),
                job_id=str(original_receipt.get("job_id") or ""),
                assignment_id=str(
                    original_receipt.get("assignment_id") or ""
                ),
                lease_id=str(original_receipt.get("lease_id") or ""),
                marker_digest=str(
                    original_receipt.get("marker_digest") or ""
                ),
                manifest_binding_digest=str(
                    original_receipt.get("manifest_binding_digest") or ""
                ),
                result_payload=validated_result,
            )
        )
        expected_receipt = {
            key: original_receipt[key]
            for key in (
                "schema",
                "job_id",
                "phase",
                "worker_id",
                "assignment_id",
                "lease_id",
                "marker_digest",
                "manifest_binding_digest",
            )
        }
        replayed_result = self._validate_receipt(
            completed_receipt,
            expected_receipt=expected_receipt,
            executable_job=claimed.executable_job,
        )
        if replayed_result is None:
            raise ValueError(
                "knowledge_index_worker_dispatch_completion_invalid"
            )
        if replayed_result != validated_result:
            raise ValueError(
                "knowledge_index_worker_dispatch_result_conflict"
            )
        return copy.deepcopy(replayed_result)

    def _execution_binding(
        self,
        *,
        prepared: PreparedKnowledgeIndexDispatch,
        task_id: str,
    ) -> dict[str, Any]:
        if prepared.marker.phase != "execute":
            raise ValueError("knowledge_index_worker_dispatch_phase_invalid")
        if str(task_id or "").strip() != prepared.marker.job_id:
            raise ValueError("knowledge_index_dispatch_task_mismatch")
        executable_job = dict(prepared.executable_job)
        assignment = executable_job.get("assignment")
        manifest = executable_job.get(SOURCE_ACCESS_MANIFEST_FIELD)
        if not isinstance(assignment, Mapping) or not isinstance(
            manifest,
            Mapping,
        ):
            raise ValueError(
                "knowledge_index_worker_dispatch_binding_invalid"
            )
        binding = {
            "worker_id": str(assignment.get("worker_id") or "").strip(),
            "job_id": prepared.marker.job_id,
            "assignment_id": str(
                assignment.get("assignment_id") or ""
            ).strip(),
            "lease_id": str(assignment.get("lease_id") or "").strip(),
            "marker_digest": prepared.marker.marker_digest,
            "manifest_binding_digest": str(
                manifest.get("binding_digest") or ""
            ).strip(),
            "lease_expires_epoch_ms": assignment.get(
                "lease_expires_epoch_ms"
            ),
            "grant_expires_at_epoch_ms": manifest.get(
                "grant_expires_at_epoch_ms"
            ),
        }
        if (
            binding["worker_id"] != self._worker_id
            or not binding["assignment_id"]
            or not binding["lease_id"]
            or not binding["manifest_binding_digest"]
            or isinstance(binding["lease_expires_epoch_ms"], bool)
            or not isinstance(binding["lease_expires_epoch_ms"], int)
            or isinstance(binding["grant_expires_at_epoch_ms"], bool)
            or not isinstance(binding["grant_expires_at_epoch_ms"], int)
        ):
            raise ValueError(
                "knowledge_index_worker_dispatch_binding_invalid"
            )
        return binding

    @classmethod
    def _validate_receipt(
        cls,
        receipt: Mapping[str, Any],
        *,
        expected_receipt: Mapping[str, Any],
        executable_job: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        claimed_at = receipt.get("claimed_at_epoch_ms")
        expected_fields = {
            *expected_receipt,
            "claimed_at_epoch_ms",
            "state",
            "result_digest",
            "result_payload",
            "completed_at_epoch_ms",
        }
        if (
            {key: receipt.get(key) for key in expected_receipt}
            != dict(expected_receipt)
            or isinstance(claimed_at, bool)
            or not isinstance(claimed_at, int)
            or claimed_at < 0
            or set(receipt) != expected_fields
        ):
            raise ValueError(
                "knowledge_index_worker_dispatch_claim_invalid"
            )
        state = str(receipt.get("state") or "")
        if state == "claimed":
            if (
                receipt.get("result_digest") is None
                and receipt.get("result_payload") is None
                and receipt.get("completed_at_epoch_ms") is None
            ):
                return None
            raise ValueError(
                "knowledge_index_worker_dispatch_claim_invalid"
            )
        completed_at = receipt.get("completed_at_epoch_ms")
        if (
            state != "completed"
            or isinstance(completed_at, bool)
            or not isinstance(completed_at, int)
            or completed_at < claimed_at
        ):
            raise ValueError(
                "knowledge_index_worker_dispatch_claim_invalid"
            )
        replayed_result, digest = cls._canonical_result(
            receipt.get("result_payload")
        )
        if digest != str(receipt.get("result_digest") or ""):
            raise ValueError(
                "knowledge_index_worker_dispatch_claim_invalid"
            )
        return cls._validate_result_binding(
            executable_job,
            replayed_result,
        )

    @staticmethod
    def _canonical_result(
        payload: Any,
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(payload, Mapping):
            raise ValueError(
                "knowledge_index_worker_dispatch_claim_invalid"
            )
        try:
            encoded = json.dumps(
                dict(payload),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError(
                "knowledge_index_worker_dispatch_claim_invalid"
            ) from exc
        if len(encoded) > MAX_KNOWLEDGE_INDEX_WORKER_RESULT_BYTES:
            raise ValueError(
                "knowledge_index_worker_dispatch_claim_invalid"
            )
        normalized = json.loads(encoded.decode("ascii"))
        if not isinstance(normalized, dict):
            raise ValueError(
                "knowledge_index_worker_dispatch_claim_invalid"
            )
        return normalized, hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _validate_result_binding(
        executable_job: Mapping[str, Any],
        result_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            job = parse_execution_job(
                {
                    key: value
                    for key, value in executable_job.items()
                    if key != SOURCE_ACCESS_MANIFEST_FIELD
                }
            )
            result = parse_execution_result(result_payload)
        except ValueError as exc:
            raise ValueError(
                "knowledge_index_worker_dispatch_result_invalid"
            ) from exc
        authority = job.authority_binding
        assignment = job.assignment
        expected = {
            "job_id": job.job_id,
            "idempotency_fingerprint": job.idempotency_fingerprint,
            "assignment_id": assignment.assignment_id,
            "worker_id": assignment.worker_id,
            "lease_id": assignment.lease_id,
            "lease_generation": assignment.lease_generation,
            "source_revision_id": authority.source_revision_id,
            "source_revision_digest": authority.source_revision_digest,
            "admission_digest": authority.admission_digest,
            "policy_snapshot_id": authority.policy_snapshot_id,
            "policy_snapshot_digest": authority.policy_snapshot_digest,
            "destination_id": authority.destination_id,
            "destination_digest": authority.destination_digest,
            "source_access_grant_id": authority.source_access_grant_id,
            "source_access_grant_digest": (
                authority.source_access_grant_digest
            ),
            "authority_binding_digest": authority.binding_digest,
            "file_manifest_digest": job.file_manifest.manifest_digest,
        }
        wire = result.to_wire()
        if any(wire.get(key) != value for key, value in expected.items()):
            raise ValueError(
                "knowledge_index_worker_dispatch_result_binding_invalid"
            )
        return wire


__all__ = [
    "BOUND_JOB_SCHEMA",
    "DISPATCH_RECEIPT_SCHEMA",
    "ClaimedKnowledgeIndexDispatch",
    "KnowledgeIndexWorkerDispatchAdmission",
    "KnowledgeIndexWorkerDispatchResultPendingError",
    "KnowledgeIndexWorkerDispatchReceiptLedgerPort",
    "PreparedKnowledgeIndexDispatch",
]
