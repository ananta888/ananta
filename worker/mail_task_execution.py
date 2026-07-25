"""Worker-side execution of one Hub-delegated mail operation."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.mail_task_service import (
    MAIL_OPERATIONS,
    MAIL_TASK_RESULT_SCHEMA,
    MAIL_TASK_SCHEMA,
)

_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_FORBIDDEN_KEY_PARTS = (
    "password",
    "secret",
    "authorization",
    "credential",
    "body",
    "content",
    "attachment",
    "blob",
)
_SAFE_SUFFIXES = ("_ref", "_refs", "_hash", "_count", "_counts")


def _contains_sensitive_data(value: Any, *, key: str = "") -> bool:
    normalized = str(key or "").strip().lower()
    if normalized and any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
        if not normalized.endswith(_SAFE_SUFFIXES):
            return True
    if isinstance(value, Mapping):
        return any(
            _contains_sensitive_data(item, key=str(item_key))
            for item_key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_data(item) for item in value)
    return isinstance(value, (bytes, bytearray, memoryview))


@dataclass(frozen=True)
class MailTaskExecutionOutcome:
    status: str
    reason_code: str
    provider: str
    retryable: bool = False
    retry_after_ms: int | None = None
    result_refs: tuple[str, ...] = ()
    counters: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if self.status not in {"completed", "failed"}:
            raise ValueError("mail_worker_outcome_status_invalid")
        if self.provider not in {"imap", "jmap", ""}:
            raise ValueError("mail_worker_outcome_provider_invalid")
        if not self.reason_code.startswith("mail_"):
            raise ValueError("mail_worker_outcome_reason_invalid")
        if any(_REFERENCE.fullmatch(str(item or "")) is None for item in self.result_refs):
            raise ValueError("mail_worker_outcome_ref_invalid")


class MailTaskExecutionPort(Protocol):
    def execute(
        self,
        *,
        job_id: str,
        operation: str,
        account_ref: str,
        workspace_scope: Mapping[str, str],
        idempotency_key: str,
        operation_refs: Mapping[str, str],
        policy_refs: Mapping[str, str],
        deadline_at: float,
        lease_fencing_token: int,
    ) -> MailTaskExecutionOutcome: ...


class UnconfiguredMailTaskExecution:
    def execute(self, **_kwargs: Any) -> MailTaskExecutionOutcome:
        return MailTaskExecutionOutcome(
            status="failed",
            reason_code="mail_worker_execution_unconfigured",
            provider="",
            retryable=False,
        )


class MailWorkerTaskHandler:
    """Validates a reference-only envelope and calls exactly one execution port."""

    def __init__(
        self,
        execution: MailTaskExecutionPort,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._execution = execution
        self._clock = clock

    @staticmethod
    def _context(task: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        context = task.get("worker_execution_context")
        if not isinstance(context, Mapping):
            raise ValueError("mail_worker_context_required")
        envelope = context.get("mail_task")
        control = context.get("mail_task_control")
        if not isinstance(envelope, Mapping) or not isinstance(control, Mapping):
            raise ValueError("mail_worker_envelope_required")
        return dict(envelope), dict(control)

    @staticmethod
    def _validate(
        tid: str,
        envelope: Mapping[str, Any],
        control: Mapping[str, Any],
        *,
        require_lease: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        required = {
            "schema",
            "job_id",
            "operation",
            "account_ref",
            "workspace_scope",
            "idempotency_key",
            "request_fingerprint",
            "operation_refs",
            "policy_refs",
            "deadline_at",
            "max_attempts",
            "created_at",
        }
        if set(envelope) != required:
            raise ValueError("mail_worker_envelope_fields_invalid")
        if envelope.get("schema") != MAIL_TASK_SCHEMA:
            raise ValueError("mail_worker_envelope_schema_invalid")
        if str(envelope.get("job_id") or "") != str(tid):
            raise ValueError("mail_worker_job_mismatch")
        if str(envelope.get("operation") or "") not in MAIL_OPERATIONS:
            raise ValueError("mail_worker_operation_invalid")
        if _contains_sensitive_data(envelope):
            raise ValueError("mail_worker_sensitive_payload_forbidden")
        lease = control.get("lease")
        if not require_lease and lease is None:
            return dict(envelope), {}
        if not isinstance(lease, Mapping):
            raise ValueError("mail_worker_account_lease_required")
        lease_value = dict(lease)
        fencing = lease_value.get("fencing_token")
        if isinstance(fencing, bool) or not isinstance(fencing, int) or fencing < 1:
            raise ValueError("mail_worker_fencing_token_invalid")
        if str(lease_value.get("job_id") or "") != str(tid):
            raise ValueError("mail_worker_lease_job_mismatch")
        return dict(envelope), lease_value

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        tid = str(kwargs.get("tid") or "")
        task = kwargs.get("task")
        if not isinstance(task, Mapping):
            raise ValueError("mail_worker_task_required")
        envelope, control = self._context(task)
        self._validate(tid, envelope, control, require_lease=False)
        return {
            "status": "executable",
            "reason": "mail_worker_task_ready",
            "command": f"mail-task:{tid}",
            "tool_calls": [],
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        tid = str(kwargs.get("tid") or "")
        task = kwargs.get("task")
        if not isinstance(task, Mapping):
            raise ValueError("mail_worker_task_required")
        envelope, control = self._context(task)
        envelope, lease = self._validate(tid, envelope, control)
        deadline_at = float(envelope.get("deadline_at") or 0.0)
        fencing = int(lease["fencing_token"])
        if float(lease.get("expires_at") or 0.0) <= float(self._clock()):
            outcome = MailTaskExecutionOutcome(
                status="failed",
                reason_code="mail_worker_account_lease_expired",
                provider="",
            )
        elif deadline_at <= float(self._clock()):
            outcome = MailTaskExecutionOutcome(
                status="failed",
                reason_code="mail_worker_deadline_exceeded",
                provider="",
            )
        else:
            try:
                outcome = self._execution.execute(
                    job_id=tid,
                    operation=str(envelope["operation"]),
                    account_ref=str(envelope["account_ref"]),
                    workspace_scope=dict(envelope["workspace_scope"]),
                    idempotency_key=str(envelope["idempotency_key"]),
                    operation_refs=dict(envelope["operation_refs"]),
                    policy_refs=dict(envelope["policy_refs"]),
                    deadline_at=deadline_at,
                    lease_fencing_token=fencing,
                )
            except Exception:
                outcome = MailTaskExecutionOutcome(
                    status="failed",
                    reason_code="mail_provider_execution_failed",
                    provider="",
                    retryable=True,
                )
        result = {
            "schema": MAIL_TASK_RESULT_SCHEMA,
            "job_id": tid,
            "idempotency_key": str(envelope["idempotency_key"]),
            "operation": str(envelope["operation"]),
            "status": outcome.status,
            "reason_code": outcome.reason_code,
            "retryable": bool(outcome.retryable),
            "retry_after_ms": outcome.retry_after_ms,
            "provider": outcome.provider,
            "result_refs": list(outcome.result_refs),
            "counters": {
                str(key): int(value)
                for key, value in dict(outcome.counters or {}).items()
            },
            "lease_fencing_token": fencing,
        }
        if _contains_sensitive_data(result):
            raise ValueError("mail_worker_sensitive_result_forbidden")
        return result


def build_mail_task_handler(
    execution: MailTaskExecutionPort | None = None,
) -> MailWorkerTaskHandler:
    if execution is None:
        try:
            from worker.mail_task_composition import (
                build_production_mail_task_execution,
            )

            execution = build_production_mail_task_execution()
        except Exception:
            execution = UnconfiguredMailTaskExecution()
    return MailWorkerTaskHandler(execution)


__all__ = [
    "MailTaskExecutionOutcome",
    "MailTaskExecutionPort",
    "MailWorkerTaskHandler",
    "UnconfiguredMailTaskExecution",
    "build_mail_task_handler",
]
