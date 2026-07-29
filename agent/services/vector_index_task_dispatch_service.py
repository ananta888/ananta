"""Dispatch admission and Worker-result handling for vector-index tasks."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

from agent.services.vector_index_task_contracts import (
    VECTOR_INDEX_RESULT_SCHEMA,
    canonical_json,
    clone_json,
)
from agent.services.vector_index_task_lifecycle_support import (
    ACTIVE_STATUSES,
    ATTEMPT_ID_PATTERN,
    DISPATCH_ADMISSION_SCHEMA,
    TERMINAL_STATUSES,
    VectorIndexTaskLifecycleSupport,
)
from agent.services.vector_index_task_query_service import (
    VectorIndexTaskQueryService,
)
from agent.services.vector_index_worker_result_boundary import (
    VECTOR_INDEX_RESULT_FIELDS,
    VECTOR_INDEX_RESULT_REASON_CODE,
)


class VectorIndexTaskDispatchService:
    """Issue capabilities, admit Workers, and commit verified results."""

    def __init__(
        self,
        support: VectorIndexTaskLifecycleSupport,
        queries: VectorIndexTaskQueryService,
    ) -> None:
        self._support = support
        self._queries = queries

    def issue_dispatch_attempt(
        self,
        *,
        job_id: str,
        worker_audience: str,
        phase: str,
        actor: str = "hub-dispatch",
    ) -> dict[str, Any]:
        """Persist and return a short-lived, Worker-bound dispatch envelope."""

        normalized_phase = str(phase or "").strip().lower()
        if normalized_phase not in {"propose", "execute"}:
            raise ValueError("vector_index_task_dispatch_phase_invalid")
        audience = self._support._normalize_dispatch_audience(worker_audience)
        with self._support._lock:
            raw = self._support._raw(self._support._repository().get_by_id(str(job_id)))
            envelope = self._support._envelope(raw)
            if not envelope:
                raise ValueError("vector_index_task_not_found")
            status = str(raw.get("status") or "").strip().lower()
            if status in TERMINAL_STATUSES:
                raise RuntimeError("vector_index_task_dispatch_terminal_forbidden")
            if status not in ACTIVE_STATUSES:
                raise RuntimeError("vector_index_task_dispatch_state_invalid")
            current_dispatch = dict(envelope.get("dispatch") or {})
            verification = raw.get("verification_status") or {}
            if not isinstance(verification, Mapping):
                raise ValueError("vector_index_result_verification_invalid")
            if "vector_index_dispatch_admission" in verification:
                raise RuntimeError("vector_index_task_dispatch_inflight")
            current_attempt_id = str(current_dispatch.get("attempt_id") or "")
            request_fingerprint = str(envelope.get("request_fingerprint") or "")
            issued_at = float(self._support._clock())
            refreshed = clone_json(dict(envelope))
            refreshed["dispatch"] = self._support._new_dispatch(
                previous=envelope.get("dispatch"),
                audience=audience,
                phase=normalized_phase,
                issued_at=issued_at,
            )
            refreshed = self._support._signer().attest(refreshed)

            def dispatch_issue_predicate(task: Any) -> bool:
                if not self._support._authoritative_task_matches(
                    task,
                    job_id=str(job_id),
                    request_fingerprint=request_fingerprint,
                    attempt_id=current_attempt_id,
                    worker_audience=audience,
                ):
                    return False
                candidate_raw = self._support._raw(task)
                candidate_envelope = self._support._envelope(candidate_raw)
                candidate_dispatch = dict(candidate_envelope.get("dispatch") or {})
                candidate_verification = candidate_raw.get("verification_status") or {}
                return (
                    canonical_json(candidate_dispatch) == canonical_json(current_dispatch)
                    and isinstance(candidate_verification, Mapping)
                    and "vector_index_dispatch_admission" not in candidate_verification
                )

            committed = self._support._compare_and_set_status(
                str(job_id),
                status or "todo",
                expected_statuses={status},
                authoritative_predicate=dispatch_issue_predicate,
                force=True,
                worker_execution_context={"vector_index_task": refreshed},
                event_type="vector_index_task_dispatch_issued",
                event_actor=str(actor or "hub-dispatch"),
                event_details={
                    "attempt_id_hash": hashlib.sha256(
                        str(refreshed["dispatch"]["attempt_id"]).encode("utf-8")
                    ).hexdigest(),
                    "dispatch_sequence": refreshed["dispatch"]["sequence"],
                    "dispatch_phase": normalized_phase,
                    "dispatch_audience_hash": hashlib.sha256(audience.encode("utf-8")).hexdigest(),
                },
            )
            if not committed:
                raise RuntimeError("vector_index_task_dispatch_conflict")
            persisted_raw = self._support._raw(self._support._repository().get_by_id(str(job_id)))
            persisted_envelope = self._support._envelope(persisted_raw)
            if str(dict(persisted_envelope.get("dispatch") or {}).get("attempt_id") or "") != str(
                refreshed["dispatch"]["attempt_id"]
            ):
                raise RuntimeError("vector_index_task_dispatch_commit_invalid")
            self._support._audit_task(
                "dispatch_issued",
                persisted_envelope,
                actor,
            )
            return clone_json(persisted_envelope)

    def admit_dispatch_attempt(
        self,
        *,
        job_id: str,
        attempt_id: str,
        sequence: int,
        phase: str,
        worker_audience: str,
        actor: str = "worker-dispatch-admission",
    ) -> dict[str, Any]:
        """Atomically redeem the current Worker-bound execute capability."""

        normalized_phase = str(phase or "").strip().lower()
        if normalized_phase != "execute":
            raise ValueError("vector_index_dispatch_admission_phase_invalid")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("vector_index_dispatch_admission_sequence_invalid")
        normalized_attempt_id = str(attempt_id or "").strip()
        if ATTEMPT_ID_PATTERN.fullmatch(normalized_attempt_id) is None:
            raise ValueError("vector_index_task_attempt_id_invalid")
        audience = self._support._normalize_dispatch_audience(worker_audience)
        with self._support._lock:
            raw = self._support._raw(self._support._repository().get_by_id(str(job_id)))
            envelope = self._support._envelope(raw)
            if not envelope:
                raise ValueError("vector_index_task_not_found")
            status = str(raw.get("status") or "").strip().lower()
            if status in TERMINAL_STATUSES:
                raise RuntimeError("vector_index_dispatch_admission_terminal")
            if status not in ACTIVE_STATUSES:
                raise RuntimeError("vector_index_dispatch_admission_state_invalid")
            dispatch = dict(envelope.get("dispatch") or {})
            if (
                str(dispatch.get("attempt_id") or "") != normalized_attempt_id
                or dispatch.get("sequence") != sequence
                or dispatch.get("phase") != normalized_phase
                or self._support._normalize_dispatch_audience(dispatch.get("audience")) != audience
            ):
                raise RuntimeError("vector_index_dispatch_admission_mismatch")
            now = float(self._support._clock())
            expires_at = self._support._finite_timestamp(
                dispatch.get("expires_at"),
                fallback=-1.0,
            )
            if not math.isfinite(now) or expires_at <= now:
                raise RuntimeError("vector_index_dispatch_admission_expired")
            verification = raw.get("verification_status") or {}
            if not isinstance(verification, Mapping):
                raise ValueError("vector_index_result_verification_invalid")
            if "vector_index_dispatch_admission" in verification:
                raise RuntimeError("vector_index_dispatch_admission_replay")
            admission = {
                "schema": DISPATCH_ADMISSION_SCHEMA,
                "attempt_id": normalized_attempt_id,
                "sequence": sequence,
                "phase": normalized_phase,
                "audience": audience,
                "admitted_at": now,
            }
            next_verification = clone_json(dict(verification))
            next_verification["vector_index_dispatch_admission"] = admission

            def admission_predicate(task: Any) -> bool:
                if not self._support._authoritative_task_matches(
                    task,
                    job_id=str(job_id),
                    request_fingerprint=str(envelope.get("request_fingerprint") or ""),
                    attempt_id=normalized_attempt_id,
                    worker_audience=audience,
                ):
                    return False
                candidate_raw = self._support._raw(task)
                candidate_envelope = self._support._envelope(candidate_raw)
                candidate_dispatch = dict(candidate_envelope.get("dispatch") or {})
                candidate_verification = candidate_raw.get("verification_status") or {}
                return (
                    candidate_dispatch.get("sequence") == sequence
                    and candidate_dispatch.get("phase") == normalized_phase
                    and isinstance(candidate_verification, Mapping)
                    and "vector_index_dispatch_admission" not in candidate_verification
                )

            committed = self._support._compare_and_set_status(
                str(job_id),
                status,
                expected_statuses={status},
                authoritative_predicate=admission_predicate,
                force=True,
                verification_status=next_verification,
                event_type=("vector_index_task_dispatch_admitted"),
                event_actor=str(actor or "worker-gateway"),
                event_details={
                    "attempt_id_hash": hashlib.sha256(normalized_attempt_id.encode("utf-8")).hexdigest(),
                    "dispatch_sequence": sequence,
                    "dispatch_audience_hash": hashlib.sha256(audience.encode("utf-8")).hexdigest(),
                },
            )
            if not committed:
                raise RuntimeError("vector_index_dispatch_admission_conflict")
            self._support._audit_task(
                "dispatch_admitted",
                envelope,
                actor,
            )
            return clone_json(admission)

    def validate_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw = self._support._raw(self._support._repository().get_by_id(str(job_id)))
        envelope = self._support._envelope(raw)
        if not envelope:
            raise ValueError("vector_index_task_not_found")
        if str(raw.get("status") or "").strip().lower() == "cancelled":
            raise RuntimeError("vector_index_result_after_cancel")
        if not isinstance(result, Mapping):
            raise ValueError("vector_index_result_mapping_invalid")
        payload = self._support._result_boundary.normalize_result(result)
        if set(payload) != VECTOR_INDEX_RESULT_FIELDS:
            raise ValueError("vector_index_result_fields_invalid")
        if payload.get("schema") != VECTOR_INDEX_RESULT_SCHEMA:
            raise ValueError("vector_index_result_schema_invalid")
        if str(payload.get("job_id") or "") != str(job_id):
            raise ValueError("vector_index_result_job_mismatch")
        expected_attempt_id = str(dict(envelope.get("dispatch") or {}).get("attempt_id") or "")
        if not expected_attempt_id or payload.get("attempt_id") != expected_attempt_id:
            raise ValueError("vector_index_result_attempt_mismatch")
        if payload.get("idempotency_key") != envelope.get("idempotency_key"):
            raise ValueError("vector_index_result_idempotency_mismatch")
        if payload.get("operation") != envelope.get("operation"):
            raise ValueError("vector_index_result_operation_mismatch")
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"completed", "failed"}:
            raise ValueError("vector_index_result_status_invalid")
        for field in ("diagnostics", "result"):
            value = payload.get(field)
            if value is not None and not isinstance(value, Mapping):
                raise ValueError(f"vector_index_result_{field}_invalid")
        reason_code = payload.get("reason_code")
        if reason_code is not None and not isinstance(
            reason_code,
            str,
        ):
            raise ValueError("vector_index_result_reason_invalid")
        if isinstance(reason_code, str):
            reason_code = reason_code.strip()
            if not reason_code:
                reason_code = None
            elif VECTOR_INDEX_RESULT_REASON_CODE.fullmatch(reason_code) is None:
                raise ValueError("vector_index_result_reason_invalid")
        if status == "failed" and reason_code is None:
            reason_code = "vector_index_worker_failed"
        payload["reason_code"] = reason_code
        error = payload.get("error")
        if error is not None and not isinstance(error, str):
            raise ValueError("vector_index_result_error_invalid")
        if isinstance(error, str):
            error = error.strip()
            if status != "failed" and error:
                raise ValueError("vector_index_result_error_status_invalid")
            payload["error"] = "vector index worker execution failed" if error else None
        return clone_json({**payload, "status": status})

    def accept_worker_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        status_values: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._support._lock:
            payload = self.validate_worker_result(
                job_id=job_id,
                result=result,
            )
            raw = self._support._raw(self._support._repository().get_by_id(str(job_id)))
            envelope = self._support._envelope(raw)
            current_status = str(raw.get("status") or "").strip().lower()
            current_verification = raw.get("verification_status") or {}
            if not isinstance(current_verification, Mapping):
                raise ValueError("vector_index_result_verification_invalid")
            existing_result = current_verification.get("vector_index_task_result")
            if current_status in TERMINAL_STATUSES:
                if isinstance(existing_result, Mapping) and canonical_json(existing_result) == canonical_json(payload):
                    return self._queries.get_task(job_id) or {}
                if current_status == "cancelled":
                    raise RuntimeError("vector_index_result_after_cancel")
                raise RuntimeError("vector_index_result_terminal_conflict")
            if current_status not in ACTIVE_STATUSES:
                raise RuntimeError("vector_index_result_state_invalid")
            if status_values is not None and not isinstance(
                status_values,
                Mapping,
            ):
                raise ValueError("vector_index_result_status_values_invalid")
            additional = self._support._result_boundary.normalize_status_values(status_values or {})
            allowed_status_fields = {
                "last_output",
                "last_exit_code",
                "last_proposal",
                "verification_status",
            }
            if set(additional) - allowed_status_fields:
                raise ValueError("vector_index_result_status_values_forbidden")
            verification = self._support._result_boundary.normalize_verification(current_verification)
            forwarded_verification = additional.pop(
                "verification_status",
                None,
            )
            if forwarded_verification is not None:
                if not isinstance(forwarded_verification, Mapping):
                    raise ValueError("vector_index_result_verification_invalid")
                verification.update(self._support._result_boundary.normalize_verification(forwarded_verification))
            dispatch = dict(envelope.get("dispatch") or {})
            admission = current_verification.get("vector_index_dispatch_admission")
            if (
                not isinstance(admission, Mapping)
                or admission.get("schema") != DISPATCH_ADMISSION_SCHEMA
                or str(admission.get("attempt_id") or "") != str(dispatch.get("attempt_id") or "")
                or admission.get("sequence") != dispatch.get("sequence")
                or admission.get("phase") != "execute"
                or admission.get("audience") != dispatch.get("audience")
            ):
                raise RuntimeError("vector_index_result_dispatch_not_admitted")
            verification["vector_index_task_result"] = payload
            verification = self._support._result_boundary.normalize_verification(verification)
            attempt_id = str(dispatch.get("attempt_id") or "")
            request_fingerprint = str(envelope.get("request_fingerprint") or "")

            def result_predicate(task: Any) -> bool:
                if not self._support._authoritative_task_matches(
                    task,
                    job_id=str(job_id),
                    request_fingerprint=request_fingerprint,
                    attempt_id=attempt_id,
                ):
                    return False
                candidate_raw = self._support._raw(task)
                candidate_verification = candidate_raw.get("verification_status") or {}
                candidate_admission = (
                    candidate_verification.get("vector_index_dispatch_admission")
                    if isinstance(candidate_verification, Mapping)
                    else None
                )
                return (
                    isinstance(candidate_verification, Mapping)
                    and isinstance(candidate_admission, Mapping)
                    and candidate_admission.get("attempt_id") == attempt_id
                    and "vector_index_task_result" not in candidate_verification
                )

            committed = self._support._compare_and_set_status(
                str(job_id),
                str(payload["status"]),
                expected_statuses={current_status},
                authoritative_predicate=result_predicate,
                status_reason_code=(str(payload.get("reason_code") or "") or None),
                verification_status=verification,
                event_type=(f"vector_index_task_{payload['status']}"),
                event_actor="vector-index-worker-gateway",
                event_details={
                    "scope_fingerprint": envelope.get("scope_fingerprint"),
                    "result_schema": VECTOR_INDEX_RESULT_SCHEMA,
                },
                **clone_json(additional),
            )
            if not committed:
                latest_raw = self._support._raw(self._support._repository().get_by_id(str(job_id)))
                latest_verification = latest_raw.get("verification_status") or {}
                latest_result = (
                    latest_verification.get("vector_index_task_result")
                    if isinstance(latest_verification, Mapping)
                    else None
                )
                if isinstance(latest_result, Mapping) and canonical_json(latest_result) == canonical_json(payload):
                    return self._queries.get_task(job_id) or {}
                if str(latest_raw.get("status") or "").strip().lower() == "cancelled":
                    raise RuntimeError("vector_index_result_after_cancel")
                raise RuntimeError("vector_index_result_terminal_conflict")
            self._support._audit_task(
                str(payload["status"]),
                envelope,
                "worker-gateway",
            )
            return self._queries.get_task(job_id) or {}


__all__ = ["VectorIndexTaskDispatchService"]
