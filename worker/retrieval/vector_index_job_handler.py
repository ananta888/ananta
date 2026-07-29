"""Worker execution boundary for Hub-owned vector-index tasks."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Mapping
from typing import Any, Callable, Protocol

from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocationError,
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_index_dispatch_admission import (
    VectorIndexDispatchAdmissionPort,
    build_vector_index_dispatch_admission_client,
)
from worker.retrieval.vector_index_replay_guard import (
    VectorIndexReplayGuard,
    build_vector_index_replay_guard,
    canonicalize_vector_index_worker_audience,
)

JOB_SCHEMA = "ananta.vector_index_task.v1"
RESULT_SCHEMA = "ananta.vector_index_task_result.v1"
OPERATIONS = frozenset({"index", "refresh", "rebuild", "delete", "migrate"})
_POLICY_DECISION = "worker_delegation_allowed"
_POLICY_SOURCE_LAYERS = frozenset(
    {
        "global_json_default",
        "profile_override",
        "workspace_override",
    }
)
_JOB_FIELDS = frozenset(
    {
        "schema",
        "job_id",
        "operation",
        "scope",
        "scope_fingerprint",
        "idempotency_key",
        "request_fingerprint",
        "resolved_config",
        "policy_decision",
        "policy_source_layers",
        "payload",
        "created_by",
        "created_at",
        "dispatch",
        "hub_attestation",
    }
)
_DISPATCH_FIELDS = frozenset(
    {
        "schema",
        "attempt_id",
        "sequence",
        "audience",
        "phase",
        "issued_at",
        "expires_at",
    }
)
_DISPATCH_SCHEMA = "ananta.vector_index_task_dispatch.v1"
_DISPATCH_PHASES = frozenset({"propose", "execute"})
_RESOLVED_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "provider",
        "config_hash",
        "config",
        "source_layers",
    }
)
_IDEMPOTENCY_KEY = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"
)
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class VectorIndexExecutionPort(Protocol):
    def execute(
        self,
        *,
        operation: str,
        scope: Mapping[str, str],
        resolved_config: Mapping[str, Any],
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]: ...


class VectorIndexTaskAttestationVerifier(Protocol):
    def verify(self, envelope: Mapping[str, Any]) -> None: ...


class _UnconfiguredVectorIndexExecution:
    def execute(self, **kwargs: Any) -> Mapping[str, Any]:
        del kwargs
        return {
            "status": "failed",
            "reason_code": "vector_index_execution_adapter_unavailable",
            "diagnostics": {},
            "result": None,
        }


class VectorIndexWorkerTaskHandler:
    """Execute exactly one immutable Hub envelope; never orchestrate workers."""

    def __init__(
        self,
        execution: VectorIndexExecutionPort,
        *,
        task_verifier: VectorIndexTaskAttestationVerifier,
        replay_guard: VectorIndexReplayGuard,
        dispatch_admission: VectorIndexDispatchAdmissionPort,
        worker_audience: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._execution = execution
        self._task_verifier = task_verifier
        self._replay_guard = replay_guard
        self._dispatch_admission = dispatch_admission
        self._worker_audience = self._normalize_audience(worker_audience)
        self._clock = clock

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        envelope = self._resolve(None, kwargs)
        dispatch = self._validate(envelope, phase="propose")
        self._consume_dispatch(envelope, dispatch)
        return {
            "proposal_id": f"{envelope['job_id']}-proposal",
            "strategy_id": "deterministic_handler",
            "command": None,
            "tool_calls": [
                {
                    "name": "vector_index_operation",
                    "arguments": {
                        "job_id": envelope["job_id"],
                        "operation": envelope["operation"],
                    },
                }
            ],
            "expected_artifacts": [
                {
                    "kind": "vector_index_result",
                    "required": True,
                    "schema": RESULT_SCHEMA,
                }
            ],
            "safety_flags": {
                "worker_only": True,
                "search_forbidden": True,
                "worker_orchestration_forbidden": True,
            },
        }

    def execute(
        self,
        envelope: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        job = self._resolve(envelope, kwargs)
        dispatch = self._validate(job, phase="execute")
        self._consume_dispatch(job, dispatch)
        self._dispatch_admission.admit(
            job_id=str(job["job_id"]),
            attempt_id=str(dispatch["attempt_id"]),
            sequence=int(dispatch["sequence"]),
            phase="execute",
            worker_audience=self._worker_audience,
        )
        try:
            raw = dict(
                self._execution.execute(
                    operation=str(job["operation"]),
                    scope=dict(job["scope"]),
                    resolved_config=dict(job["resolved_config"]),
                    payload=dict(job["payload"]),
                    idempotency_key=str(job["idempotency_key"]),
                )
                or {}
            )
        except Exception as exc:
            return self._result(
                job,
                status="failed",
                reason_code=f"vector_index_worker_failed:{type(exc).__name__}",
                diagnostics={},
                result=None,
                error="vector index worker execution failed",
            )
        status = str(raw.get("status") or "").strip().lower()
        if status not in {"completed", "failed"}:
            return self._result(
                job,
                status="failed",
                reason_code="vector_index_worker_result_status_invalid",
                diagnostics={},
                result=None,
                error="execution port returned a non-terminal status",
            )
        diagnostics = raw.get("diagnostics")
        result = raw.get("result")
        if diagnostics is not None and not isinstance(diagnostics, Mapping):
            diagnostics = {"reason": "worker_diagnostics_invalid"}
            status = "failed"
        if result is not None and not isinstance(result, Mapping):
            result = None
            status = "failed"
        return self._result(
            job,
            status=status,
            reason_code=str(raw.get("reason_code") or "") or None,
            diagnostics=dict(diagnostics or {}),
            result=dict(result) if isinstance(result, Mapping) else None,
            error=(
                "vector index worker execution failed"
                if status == "failed" and raw.get("error")
                else None
            ),
        )

    @staticmethod
    def _resolve(
        envelope: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        del envelope
        request_data = kwargs.get("request_data")
        if isinstance(request_data, Mapping):
            dispatched = request_data.get("vector_index_dispatch")
        else:
            dispatched = getattr(
                request_data,
                "vector_index_dispatch",
                None,
            )
        if isinstance(dispatched, Mapping):
            resolved = dict(dispatched)
            outer_id = str(
                kwargs.get("tid")
                or kwargs.get("task_id")
                or ""
            ).strip()
            task = kwargs.get("task")
            task_id = (
                str(task.get("id") or "").strip()
                if isinstance(task, Mapping)
                else ""
            )
            if (
                not outer_id
                or outer_id != str(resolved.get("job_id") or "")
                or (task_id and task_id != outer_id)
            ):
                raise ValueError("vector_index_task_outer_id_mismatch")
            return resolved
        task = kwargs.get("task")
        if isinstance(task, Mapping):
            context = task.get("worker_execution_context")
            if isinstance(context, Mapping):
                value = context.get("vector_index_task")
                if isinstance(value, Mapping):
                    resolved = dict(value)
                    task_id = str(task.get("id") or "").strip()
                    if not task_id or task_id != str(
                        resolved.get("job_id") or ""
                    ):
                        raise ValueError("vector_index_task_outer_id_mismatch")
                    return resolved
        raise ValueError("vector_index_task_envelope_missing")

    def _validate(
        self,
        job: Mapping[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]:
        if job.get("schema") != JOB_SCHEMA:
            raise ValueError("vector_index_task_schema_invalid")
        self._task_verifier.verify(job)
        if set(job) != _JOB_FIELDS:
            raise ValueError("vector_index_task_fields_invalid")
        if str(job.get("operation") or "") not in OPERATIONS:
            raise ValueError("vector_index_task_operation_invalid")
        if not isinstance(job.get("scope"), Mapping):
            raise ValueError("vector_index_task_scope_invalid")
        try:
            expected_scope_fingerprint = (
                VectorIndexArtifactLocator.scope_fingerprint(
                    dict(job["scope"])
                )
            )
        except VectorIndexArtifactLocationError as exc:
            raise ValueError("vector_index_task_scope_invalid") from exc
        supplied_scope_fingerprint = str(
            job.get("scope_fingerprint") or ""
        ).strip().lower()
        if supplied_scope_fingerprint != expected_scope_fingerprint:
            raise ValueError(
                "vector_index_task_scope_fingerprint_mismatch"
            )
        idempotency_key = str(job.get("idempotency_key") or "")
        if _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
            raise ValueError("vector_index_task_idempotency_key_invalid")
        expected_job_id = "vector-index-" + hashlib.sha256(
            json.dumps(
                {
                    "scope_fingerprint": expected_scope_fingerprint,
                    "idempotency_key": idempotency_key,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()[:32]
        if job.get("job_id") != expected_job_id:
            raise ValueError("vector_index_task_job_id_invalid")
        if _SHA256.fullmatch(
            str(job.get("request_fingerprint") or "")
        ) is None:
            raise ValueError("vector_index_task_request_fingerprint_invalid")
        if job.get("policy_decision") != _POLICY_DECISION:
            raise ValueError("vector_index_task_policy_decision_invalid")
        policy_source_layers = job.get("policy_source_layers")
        if (
            not isinstance(policy_source_layers, list)
            or not policy_source_layers
            or policy_source_layers[0] != "global_json_default"
            or len(policy_source_layers)
            != len(set(policy_source_layers))
            or any(
                layer not in _POLICY_SOURCE_LAYERS
                for layer in policy_source_layers
            )
        ):
            raise ValueError("vector_index_task_policy_layers_invalid")
        if not isinstance(job.get("resolved_config"), Mapping):
            raise ValueError("vector_index_task_resolved_config_invalid")
        resolved_config = dict(job.get("resolved_config") or {})
        if set(resolved_config) != _RESOLVED_CONFIG_FIELDS:
            raise ValueError("vector_index_task_resolved_config_fields_invalid")
        if resolved_config.get("schema") != "ananta.vector_store_resolved_config.v1":
            raise ValueError("vector_index_task_resolved_config_schema_invalid")
        if resolved_config.get("provider") not in {"json", "qdrant"}:
            raise ValueError("vector_index_task_resolved_config_provider_invalid")
        if _SHA256.fullmatch(
            str(resolved_config.get("config_hash") or "")
        ) is None:
            raise ValueError("vector_index_task_resolved_config_hash_invalid")
        if not isinstance(resolved_config.get("config"), Mapping):
            raise ValueError("vector_index_task_resolved_config_invalid")
        if resolved_config.get("source_layers") != policy_source_layers:
            raise ValueError("vector_index_task_policy_layers_mismatch")
        if not isinstance(job.get("payload"), Mapping):
            raise ValueError("vector_index_task_payload_invalid")
        if not str(job.get("created_by") or "").strip():
            raise ValueError("vector_index_task_created_by_invalid")
        try:
            created_at = float(job.get("created_at"))
        except (TypeError, ValueError) as exc:
            raise ValueError("vector_index_task_created_at_invalid") from exc
        if not math.isfinite(created_at) or created_at < 0:
            raise ValueError("vector_index_task_created_at_invalid")
        return self._validate_dispatch(job.get("dispatch"), phase=phase)

    def _validate_dispatch(
        self,
        dispatch: object,
        *,
        phase: str,
    ) -> dict[str, Any]:
        if not isinstance(dispatch, Mapping):
            raise ValueError("vector_index_task_dispatch_invalid")
        normalized_dispatch = dict(dispatch)
        if (
            set(normalized_dispatch) != _DISPATCH_FIELDS
            or normalized_dispatch.get("schema") != _DISPATCH_SCHEMA
        ):
            raise ValueError("vector_index_task_dispatch_invalid")
        attempt_id = str(
            normalized_dispatch.get("attempt_id") or ""
        ).strip()
        if _ATTEMPT_ID.fullmatch(attempt_id) is None:
            raise ValueError("vector_index_task_attempt_id_invalid")
        sequence = normalized_dispatch.get("sequence")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or not 1 <= sequence <= 2**63 - 1
        ):
            raise ValueError("vector_index_task_dispatch_sequence_invalid")
        audience = self._normalize_audience(
            normalized_dispatch.get("audience")
        )
        if audience != self._worker_audience:
            raise ValueError("vector_index_task_audience_mismatch")
        if normalized_dispatch.get("phase") != phase or phase not in _DISPATCH_PHASES:
            raise ValueError("vector_index_task_dispatch_phase_mismatch")
        try:
            issued_at = float(normalized_dispatch.get("issued_at"))
            expires_at = float(normalized_dispatch.get("expires_at"))
            now = float(self._clock())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "vector_index_task_dispatch_time_invalid"
            ) from exc
        if (
            not all(math.isfinite(value) for value in (issued_at, expires_at, now))
            or issued_at < 0
            or expires_at <= issued_at
            or issued_at > now + 30.0
            or expires_at <= now
            or expires_at - issued_at > 3_600.0
        ):
            raise ValueError("vector_index_task_dispatch_expired")
        normalized_dispatch["attempt_id"] = attempt_id
        normalized_dispatch["audience"] = audience
        return normalized_dispatch

    def _consume_dispatch(
        self,
        job: Mapping[str, Any],
        dispatch: Mapping[str, Any],
    ) -> None:
        self._replay_guard.consume(
            job_id=str(job["job_id"]),
            attempt_id=str(dispatch["attempt_id"]),
            sequence=int(dispatch["sequence"]),
            phase=str(dispatch["phase"]),
            audience=str(dispatch["audience"]),
            expires_at=float(dispatch["expires_at"]),
        )

    @staticmethod
    def _normalize_audience(value: Any) -> str:
        return canonicalize_vector_index_worker_audience(value)

    @staticmethod
    def _result(
        job: Mapping[str, Any],
        *,
        status: str,
        reason_code: str | None,
        diagnostics: Mapping[str, Any],
        result: Mapping[str, Any] | None,
        error: str | None,
    ) -> dict[str, Any]:
        return {
            "schema": RESULT_SCHEMA,
            "job_id": str(job.get("job_id") or ""),
            "attempt_id": str(
                dict(job.get("dispatch") or {}).get("attempt_id") or ""
            ),
            "idempotency_key": str(job.get("idempotency_key") or ""),
            "operation": str(job.get("operation") or ""),
            "status": status,
            "reason_code": reason_code,
            "diagnostics": dict(diagnostics),
            "result": dict(result) if isinstance(result, Mapping) else None,
            "error": error,
        }


def build_vector_index_task_handler(
    execution: VectorIndexExecutionPort | None = None,
    *,
    task_verifier: VectorIndexTaskAttestationVerifier,
    replay_guard: VectorIndexReplayGuard | None = None,
    dispatch_admission: VectorIndexDispatchAdmissionPort | None = None,
    worker_audience: str | None = None,
) -> VectorIndexWorkerTaskHandler:
    if execution is None:
        from worker.retrieval.vector_index_execution import (
            ConfiguredVectorIndexExecution,
        )

        execution = ConfiguredVectorIndexExecution()
    if replay_guard is None:
        replay_guard = build_vector_index_replay_guard()
    if dispatch_admission is None:
        dispatch_admission = (
            build_vector_index_dispatch_admission_client()
        )
    audience = str(
        worker_audience
        or os.environ.get("ANANTA_VECTOR_INDEX_TASK_AUDIENCE")
        or os.environ.get("AGENT_URL")
        or f"http://localhost:{os.environ.get('PORT', '5000')}"
    ).strip()
    return VectorIndexWorkerTaskHandler(
        execution,
        task_verifier=task_verifier,
        replay_guard=replay_guard,
        dispatch_admission=dispatch_admission,
        worker_audience=audience,
    )


__all__ = [
    "JOB_SCHEMA",
    "OPERATIONS",
    "RESULT_SCHEMA",
    "VectorIndexExecutionPort",
    "VectorIndexTaskAttestationVerifier",
    "VectorIndexWorkerTaskHandler",
    "build_vector_index_task_handler",
]
