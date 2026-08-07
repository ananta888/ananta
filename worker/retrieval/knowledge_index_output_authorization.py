"""Worker-local authorization for Hub retrieval of index outputs."""

from __future__ import annotations

import hmac
import re
import time
from collections.abc import Mapping
from typing import Any, Callable, Protocol

from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
)
from ananta_contracts.knowledge_index_dispatch import (
    build_knowledge_index_dispatch,
    parse_knowledge_index_dispatch,
)
from ananta_contracts.knowledge_index_execution import parse_execution_job

_BOUND_JOB_SCHEMA = "ananta.knowledge_index_execution_job.v2"
_JOB_ID = re.compile(r"^knowledge-index-[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OUTPUT_ROLES = frozenset(
    {
        "manifest",
        "index",
        "details",
        "relations",
        "graph_index",
        "graph_visual_metrics",
        DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
    }
)
_TERMINAL_TASK_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "verification_failed",
        "skipped",
        "aborted",
        "timeout",
        "archived",
    }
)


class KnowledgeIndexWorkerOutputAuthorizationError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 401) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class KnowledgeIndexWorkerDispatchReceiptReaderPort(Protocol):
    def get_receipt(
        self,
        *,
        worker_id: str,
        job_id: str,
    ) -> Mapping[str, Any] | None: ...


class KnowledgeIndexWorkerOutputCapabilityAuthorizer:
    """Validate one output request against Worker-local delegated state."""

    def __init__(
        self,
        *,
        task_repository: Any,
        receipt_ledger: KnowledgeIndexWorkerDispatchReceiptReaderPort,
        manifest_verifier: Any,
        worker_id: str,
        worker_url: str,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        execution_job_parser: Callable[[Mapping[str, Any]], Any] = (parse_execution_job),
    ) -> None:
        self._tasks = task_repository
        self._receipt_ledger = receipt_ledger
        self._manifest_verifier = manifest_verifier
        self._worker_id = str(worker_id or "").strip()
        self._worker_url = str(worker_url or "").strip().rstrip("/")
        self._clock_ms = clock_ms
        self._parse_execution_job = execution_job_parser
        if not self._worker_id or not self._worker_url:
            raise KnowledgeIndexWorkerOutputAuthorizationError("knowledge_index_output_worker_identity_invalid")

    def authorize(
        self,
        *,
        artifact_id: str,
        artifact_sha256: str,
        artifact_size_bytes: int,
        artifact_media_type: str,
        artifact_metadata: Mapping[str, Any],
        manifest: Mapping[str, Any],
        job_id: str,
        knowledge_index_id: str,
        run_id: str,
        output_role: str,
    ) -> None:
        if not self._manifest_verifier.verify_manifest(manifest):
            self._deny("knowledge_index_output_capability_signature_invalid")
        now_ms = int(self._clock_ms())
        if int(manifest.get("grant_expires_at_epoch_ms") or 0) <= now_ms:
            self._deny(
                "knowledge_index_output_capability_expired",
                status_code=403,
            )

        normalized_job_id = str(job_id or "").strip()
        if not _JOB_ID.fullmatch(normalized_job_id):
            self._deny("knowledge_index_output_job_invalid")
        try:
            expected_marker_digest = parse_knowledge_index_dispatch(
                build_knowledge_index_dispatch(
                    job_id=normalized_job_id,
                    phase="execute",
                    source_access_manifest=manifest,
                ),
                expected_phase="execute",
                expected_job_id=normalized_job_id,
            ).marker_digest
        except ValueError:
            self._deny(
                "knowledge_index_output_capability_marker_invalid",
                status_code=403,
            )
        task = self._tasks.get_by_id(normalized_job_id)
        if task is None:
            self._deny(
                "knowledge_index_output_task_not_found",
                status_code=403,
            )
        task_status = str(self._value(task, "status") or "").strip().lower()
        if task_status in _TERMINAL_TASK_STATUSES:
            self._deny(
                "knowledge_index_output_task_terminal",
                status_code=403,
            )
        assigned_url = str(self._value(task, "assigned_agent_url") or "").strip().rstrip("/")
        context = self._mapping(self._value(task, "worker_execution_context"))
        snapshot_binding = self._mapping(context.get("knowledge_index_worker_binding"))
        if snapshot_binding:
            if (
                set(snapshot_binding) != {"schema", "worker_id", "worker_url"}
                or snapshot_binding.get("schema") != "ananta.knowledge_index_worker_binding.v1"
                or not hmac.compare_digest(
                    str(snapshot_binding.get("worker_id") or ""),
                    self._worker_id,
                )
            ):
                self._deny(
                    "knowledge_index_output_worker_binding_invalid",
                    status_code=403,
                )
            assigned_url = str(snapshot_binding.get("worker_url") or "").strip().rstrip("/")
        if not assigned_url or not hmac.compare_digest(assigned_url, self._worker_url):
            self._deny(
                "knowledge_index_output_worker_url_mismatch",
                status_code=403,
            )

        envelope = self._mapping(context.get("knowledge_index_job"))
        if str(envelope.get("schema") or "") != _BOUND_JOB_SCHEMA or not hmac.compare_digest(
            str(envelope.get("job_id") or ""), normalized_job_id
        ):
            self._deny("knowledge_index_output_execution_binding_invalid")
        try:
            parsed = self._parse_execution_job(
                {key: value for key, value in envelope.items() if key != "source_access_enforcement_manifest"}
            )
        except Exception as exc:
            raise KnowledgeIndexWorkerOutputAuthorizationError(
                "knowledge_index_output_execution_binding_invalid",
                status_code=403,
            ) from exc
        assignment = parsed.assignment
        receipt = self._mapping(
            self._receipt_ledger.get_receipt(
                worker_id=self._worker_id,
                job_id=normalized_job_id,
            )
        )
        expected_receipt_fields = {
            "schema",
            "job_id",
            "phase",
            "worker_id",
            "assignment_id",
            "lease_id",
            "marker_digest",
            "manifest_binding_digest",
            "claimed_at_epoch_ms",
        }
        if (
            int(assignment.lease_expires_epoch_ms) <= now_ms
            or not hmac.compare_digest(str(assignment.worker_id), self._worker_id)
            or not hmac.compare_digest(
                str(assignment.assignment_id),
                str(manifest.get("assignment_id") or ""),
            )
            or not hmac.compare_digest(
                str(assignment.lease_id),
                str(manifest.get("lease_id") or ""),
            )
            or set(receipt) != expected_receipt_fields
            or receipt.get("schema") != "ananta.knowledge_index_worker_dispatch_receipt.v1"
            or receipt.get("phase") != "execute"
            or not hmac.compare_digest(
                str(receipt.get("job_id") or ""),
                normalized_job_id,
            )
            or not hmac.compare_digest(
                str(receipt.get("worker_id") or ""),
                self._worker_id,
            )
            or not hmac.compare_digest(
                str(receipt.get("assignment_id") or ""),
                str(assignment.assignment_id),
            )
            or not hmac.compare_digest(
                str(receipt.get("lease_id") or ""),
                str(assignment.lease_id),
            )
            or not hmac.compare_digest(
                str(receipt.get("manifest_binding_digest") or ""),
                str(manifest.get("binding_digest") or ""),
            )
            or _SHA256.fullmatch(str(receipt.get("marker_digest") or "")) is None
            or not hmac.compare_digest(
                str(receipt.get("marker_digest") or ""),
                expected_marker_digest,
            )
            or _SHA256.fullmatch(str(receipt.get("manifest_binding_digest") or "")) is None
            or isinstance(receipt.get("claimed_at_epoch_ms"), bool)
            or not isinstance(receipt.get("claimed_at_epoch_ms"), int)
            or not (0 <= int(receipt["claimed_at_epoch_ms"]) <= now_ms)
        ):
            self._deny(
                "knowledge_index_output_execution_binding_invalid",
                status_code=403,
            )

        metadata = dict(artifact_metadata or {})
        normalized_role = str(output_role or "").strip()
        supplied_digest = str(artifact_sha256 or "").strip().lower()
        try:
            supplied_size = int(artifact_size_bytes)
        except (TypeError, ValueError):
            self._deny("knowledge_index_output_reference_invalid")
        if (
            not str(artifact_id or "").strip()
            or not _SHA256.fullmatch(supplied_digest)
            or supplied_size < 0
            or normalized_role not in _OUTPUT_ROLES
            or not str(artifact_media_type or "").strip()
            or not str(knowledge_index_id or "").strip()
            or not str(run_id or "").strip()
        ):
            self._deny("knowledge_index_output_reference_invalid")
        expected_metadata = {
            "system_artifact_kind": "knowledge_index_worker_output",
            "knowledge_index_job_id": normalized_job_id,
            "knowledge_index_id": str(knowledge_index_id),
            "knowledge_index_run_id": str(run_id),
            "output_role": normalized_role,
        }
        if any(
            not hmac.compare_digest(str(metadata.get(field) or ""), expected)
            for field, expected in expected_metadata.items()
        ):
            self._deny(
                "knowledge_index_output_artifact_binding_mismatch",
                status_code=403,
            )

    @staticmethod
    def _value(value: Any, field: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(field)
        return getattr(value, field, None)

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _deny(reason_code: str, *, status_code: int = 401) -> None:
        raise KnowledgeIndexWorkerOutputAuthorizationError(
            reason_code,
            status_code=status_code,
        )


__all__ = [
    "KnowledgeIndexWorkerDispatchReceiptReaderPort",
    "KnowledgeIndexWorkerOutputAuthorizationError",
    "KnowledgeIndexWorkerOutputCapabilityAuthorizer",
]
