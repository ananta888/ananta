"""Autopilot worker forwarding: target resolution, deadlines and retries.

``AutopilotWorkerForwarder`` owns one concern of the autonomous loop: sending a
task-scoped request to a worker (or to the Hub for hub-owned step endpoints)
with the configured retry/backoff policy. Loop state such as worker health,
provider backpressure and HTTP error bookkeeping stays with
``AutonomousLoopManager``; the forwarder reaches it only through the narrow
callables the loop passes in.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.auth import resolve_configured_agent_token
from agent.common.api_envelope import unwrap_api_envelope
from agent.config import settings
from agent.routes.tasks.orchestration_policy import compute_retry_delay_seconds
from agent.services.worker_forward_transport import (
    WorkerForwardDeadlineExceeded,
    WorkerTransportDeadline,
    invoke_worker_forwarder,
)

_TERMINAL_RETRY_STATUSES = frozenset(
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


@dataclass(frozen=True, slots=True)
class _ForwardTarget:
    """Bind an internal forwarding destination to its credential."""

    url: str
    token: str = field(repr=False)
    records_worker_health: bool = True


class _ForwardAttemptError(RuntimeError):
    """Internal forwarding failure with an explicit retry decision."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)


def _structured_forward_retryable(
    response: dict[str, Any],
    *,
    http_status: int,
) -> bool:
    if http_status in {401, 403}:
        return False
    candidates: list[Any] = [response]
    details = response.get("details")
    if isinstance(details, dict):
        candidates.append(details)
        nested_details = details.get("details")
        if isinstance(nested_details, dict):
            candidates.append(nested_details)
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(
            candidate.get("retryable"),
            bool,
        ):
            return bool(candidate["retryable"])
    return not 400 <= http_status < 500


def _forward_exception_retryable(exc: Exception) -> bool:
    explicit = getattr(exc, "retryable", None)
    if isinstance(explicit, bool):
        return explicit
    error_text = str(exc or "").strip().lower()
    permanent_errors = {
        "401 unauthorized",
        "403 forbidden",
        "invalid or missing registration token",
        "worker_forward_deadline_transport_unsupported",
        "worker_forward_redirect_forbidden",
        "worker_forward_secure_transport_unsupported",
        "worker_forward_transport_deadline_exceeded",
    }
    return not (
        error_text in permanent_errors
        or error_text.endswith("_response_too_large")
        or error_text.endswith("_response_json_invalid")
        or error_text.endswith("_response_bytes_invalid")
    )


def _normalize_forwarded_step_envelope(response: Any) -> dict[str, Any]:
    cursor = response
    for _depth in range(6):
        if not isinstance(cursor, dict) or "data" not in cursor:
            break
        nested = cursor.get("data")
        if not isinstance(nested, dict):
            return {}
        cursor = nested
    normalized = unwrap_api_envelope(response)
    return normalized if isinstance(normalized, dict) else {}


class AutopilotWorkerForwarder:
    """Forward one autopilot request with retry, backoff and deadline policy."""

    def __init__(
        self,
        *,
        app_config_provider: Callable[[], Mapping[str, Any]],
        repository_registry_provider: Callable[[], Any],
        resilience_config_provider: Callable[[], dict[str, Any]],
        forward_to_worker: Callable[..., Any],
        record_worker_success: Callable[[str], None],
        record_worker_failure: Callable[..., None],
        record_provider_backpressure: Callable[[str, str], None],
        record_forward_http_error: Callable[..., None],
        append_trace_event: Callable[..., None],
    ) -> None:
        self._app_config_provider = app_config_provider
        self._repository_registry_provider = repository_registry_provider
        self._resilience_config_provider = resilience_config_provider
        self._forward_to_worker = forward_to_worker
        self._record_worker_success = record_worker_success
        self._record_worker_failure = record_worker_failure
        self._record_provider_backpressure = record_provider_backpressure
        self._record_forward_http_error = record_forward_http_error
        self._append_trace_event = append_trace_event

    def resolve_target(
        self,
        worker_url: str,
        endpoint: str,
        payload: dict,
        token: str | None,
    ) -> _ForwardTarget:
        resolved_token = token
        hub_url = str(getattr(settings, "hub_url", "") or "").strip().rstrip("/")
        is_step_endpoint = endpoint.startswith("/tasks/") and "/step/" in endpoint
        recovery_fenced = bool(
            str((payload or {}).get("dispatch_lease_token") or "").strip()
        )
        if (
            settings.role == "hub"
            and is_step_endpoint
            and hub_url
            and not recovery_fenced
        ):
            # Task-scoped step endpoints are hub-owned (task state + routing context).
            # Hub may delegate internals further, but the API contract lives here.
            hub_token = resolve_configured_agent_token(self._app_config_provider())
            if not hub_token:
                raise RuntimeError("hub_service_token_unavailable")
            return _ForwardTarget(
                url=hub_url,
                token=hub_token,
                records_worker_health=False,
            )

        with contextlib.suppress(Exception):
            agent = self._repository_registry_provider().agent_repo.get_by_url(worker_url)
            current_token = str(getattr(agent, "token", "") or "").strip()
            if current_token:
                resolved_token = current_token
        if not resolved_token:
            raise RuntimeError("worker_service_token_unavailable")
        return _ForwardTarget(url=worker_url.rstrip("/"), token=resolved_token)

    def trusted_deadline(
        self,
        *,
        endpoint: str,
        payload: dict[str, Any],
    ) -> WorkerTransportDeadline | None:
        if not str(endpoint or "").rstrip("/").endswith(
            "/step/execute"
        ):
            return None
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id or not endpoint.startswith(f"/tasks/{task_id}/"):
            return None
        registry = self._repository_registry_provider()
        task_repository = getattr(registry, "task_repo", None)
        if task_repository is None:
            return None
        task = task_repository.get_by_id(task_id)
        if task is None:
            return None
        if isinstance(task, Mapping):
            raw_task = dict(task)
        else:
            model_dump = getattr(task, "model_dump", None)
            if callable(model_dump):
                raw_task = model_dump()
            else:
                # Repository ports may expose a focused object projection.
                # Missing index fields intentionally resolve to no governed
                # deadline below instead of breaking unrelated task retries.
                raw_task = {
                    field: getattr(task, field, None)
                    for field in (
                        "id",
                        "task_kind",
                        "worker_execution_context",
                    )
                }
        from agent.services.knowledge_index_forward_timeout import (
            resolve_knowledge_index_forward_deadline,
        )

        try:
            return resolve_knowledge_index_forward_deadline(
                raw_task,
                dispatch_phase="execute",
            )
        except ValueError as exc:
            raise _ForwardAttemptError(
                str(exc or "knowledge_index_resource_budget_invalid"),
                retryable=False,
            ) from exc

    def forward_with_retry(self, worker_url: str, endpoint: str, payload: dict, token: str | None = None) -> dict:
        cfg = self._resilience_config_provider()
        last_exc: Exception | None = None
        target = self.resolve_target(
            worker_url,
            endpoint,
            payload,
            token,
        )
        transport_deadline = self.trusted_deadline(
            endpoint=endpoint,
            payload=payload,
        )

        for attempt in range(1, cfg["retry_attempts"] + 1):
            try:
                res = invoke_worker_forwarder(
                    self._forward_to_worker,
                    target.url,
                    endpoint,
                    payload,
                    token=target.token,
                    transport_deadline=transport_deadline,
                )
                if res is None:
                    raise RuntimeError(f"worker_empty_response:{target.url}:{endpoint}")
                if isinstance(res, dict) and str(res.get("status") or "").strip().lower() == "error":
                    http_status = int(res.get("http_status") or 0)
                    key = f"{target.url}|{endpoint}|{http_status or 'unknown'}"
                    self._record_forward_http_error(
                        key,
                        http_status=http_status,
                        message=str(res.get("message") or ""),
                        task_id=str((payload or {}).get("task_id") or ""),
                    )
                    raise _ForwardAttemptError(
                        f"worker_http_error:{target.url}:{endpoint}:status={http_status}:"
                        f"{str(res.get('message') or '')}",
                        retryable=_structured_forward_retryable(
                            res,
                            http_status=http_status,
                        ),
                    )
                normalized = _normalize_forwarded_step_envelope(res)
                if not normalized:
                    raise RuntimeError(f"worker_empty_payload:{target.url}:{endpoint}")
                if target.records_worker_health:
                    self._record_worker_success(worker_url)
                return normalized
            except Exception as e:
                last_exc = e
                err_text = str(e or "")
                err_lc = err_text.lower()
                if "ollama" in err_lc and "/api/generate" in err_lc and "timeout" in err_lc:
                    self._record_provider_backpressure("ollama", "ollama_generate_timeout")
                if target.records_worker_health:
                    self._record_worker_failure(
                        worker_url,
                        f"forward_failed:{endpoint}",
                        task_id=(payload or {}).get("task_id"),
                        endpoint=endpoint,
                    )
                if attempt < cfg["retry_attempts"]:
                    if not _forward_exception_retryable(e):
                        break
                    if cfg.get("retry_backoff_strategy") == "constant":
                        delay = float(min(cfg["retry_backoff_seconds"], cfg["retry_max_backoff_seconds"]))
                    else:
                        delay = compute_retry_delay_seconds(
                            attempt,
                            cfg["retry_backoff_seconds"],
                            max_backoff_seconds=cfg["retry_max_backoff_seconds"],
                            jitter_factor=cfg["retry_jitter_factor"],
                        )
                    if transport_deadline is not None:
                        remaining = (
                            transport_deadline.remaining_seconds()
                        )
                        if remaining <= delay:
                            last_exc = WorkerForwardDeadlineExceeded()
                            break
                    if payload.get("task_id"):
                        self._append_trace_event(
                            payload["task_id"],
                            "autopilot_retry_scheduled",
                            worker_url=worker_url,
                            endpoint=endpoint,
                            retry_attempt=attempt,
                            retry_delay_seconds=delay,
                            retry_backoff_strategy=cfg.get("retry_backoff_strategy"),
                        )
                    time.sleep(delay)
                    _tid = str((payload or {}).get("task_id") or "").strip()
                    if _tid:
                        with contextlib.suppress(Exception):
                            _t = self._repository_registry_provider().task_repo.get_by_id(_tid)
                            if _t and str(getattr(_t, "status", "") or "") in _TERMINAL_RETRY_STATUSES:
                                last_exc = RuntimeError(f"task_terminal_during_retry:{_tid}:{_t.status}")
                                break
        raise RuntimeError(f"worker_forward_failed:{target.url}:{endpoint}:{last_exc}")
