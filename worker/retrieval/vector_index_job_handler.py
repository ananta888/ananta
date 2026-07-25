"""Worker execution boundary for Hub-owned vector-index tasks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

JOB_SCHEMA = "ananta.vector_index_task.v1"
RESULT_SCHEMA = "ananta.vector_index_task_result.v1"
OPERATIONS = frozenset({"index", "refresh", "rebuild", "delete", "migrate"})


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

    def __init__(self, execution: VectorIndexExecutionPort) -> None:
        self._execution = execution

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        envelope = self._resolve(None, kwargs)
        self._validate(envelope)
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
        self._validate(job)
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
        if isinstance(envelope, Mapping) and envelope.get("schema") == JOB_SCHEMA:
            return dict(envelope)
        task = kwargs.get("task")
        if isinstance(task, Mapping):
            context = task.get("worker_execution_context")
            if isinstance(context, Mapping):
                value = context.get("vector_index_task")
                if isinstance(value, Mapping):
                    return dict(value)
        raise ValueError("vector_index_task_envelope_missing")

    @staticmethod
    def _validate(job: Mapping[str, Any]) -> None:
        if job.get("schema") != JOB_SCHEMA:
            raise ValueError("vector_index_task_schema_invalid")
        if not str(job.get("job_id") or "").startswith("vector-index-"):
            raise ValueError("vector_index_task_job_id_invalid")
        if str(job.get("operation") or "") not in OPERATIONS:
            raise ValueError("vector_index_task_operation_invalid")
        if not isinstance(job.get("scope"), Mapping):
            raise ValueError("vector_index_task_scope_invalid")
        if not isinstance(job.get("resolved_config"), Mapping):
            raise ValueError("vector_index_task_resolved_config_invalid")
        if (
            dict(job.get("resolved_config") or {}).get("schema")
            != "ananta.vector_store_resolved_config.v1"
        ):
            raise ValueError("vector_index_task_resolved_config_schema_invalid")
        if not isinstance(job.get("payload"), Mapping):
            raise ValueError("vector_index_task_payload_invalid")
        idempotency_key = str(job.get("idempotency_key") or "")
        if len(idempotency_key) < 8:
            raise ValueError("vector_index_task_idempotency_key_invalid")

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
) -> VectorIndexWorkerTaskHandler:
    if execution is None:
        from worker.retrieval.vector_index_execution import (
            ConfiguredVectorIndexExecution,
        )

        execution = ConfiguredVectorIndexExecution()
    return VectorIndexWorkerTaskHandler(
        execution
    )


__all__ = [
    "JOB_SCHEMA",
    "OPERATIONS",
    "RESULT_SCHEMA",
    "VectorIndexExecutionPort",
    "VectorIndexWorkerTaskHandler",
    "build_vector_index_task_handler",
]
