"""Forwarding-hub cluster for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
forwarding_hub cluster of SPLIT-001 (sub-split 001f). The module owns
cross-container task forwarding: deciding whether to forward to a remote
worker, persisting forwarded proposal/execution results, and normalizing
forwarded artifacts.

Backwards compatibility is preserved at the service boundary via thin
delegating wrappers in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json SPLIT-001).
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Callable

from flask import current_app

from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import WorkerForwardingError
from agent.config import settings
from agent.llm_integration import normalize_llm_call_profile_entry
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.services.task_runtime_service import update_local_task_status
from urllib.parse import urlparse

if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse


def forward_task_request_if_remote(
    *,
    tid: str,
    task: dict,
    endpoint: str,
    payload: dict,
    forwarder: Callable,
    on_success: Callable[[dict, dict], None],
) -> "TaskScopedRouteResponse | None":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    # Hub owns cross-container routing. Worker containers must execute locally
    # and never re-forward step endpoints to avoid forwarding loops.
    if str(getattr(settings, "role", "") or "").strip().lower() != "hub":
        return None
    worker_url = task.get("assigned_agent_url")
    if not worker_url:
        return None
    my_url = settings.agent_url or f"http://localhost:{settings.port}"
    if worker_url.rstrip("/") == my_url.rstrip("/"):
        return None
    try:
        parsed_worker = urlparse(str(worker_url))
        parsed_self = urlparse(str(my_url))
        worker_host = str(parsed_worker.hostname or "").strip().lower()
        self_host = str(parsed_self.hostname or "").strip().lower()
        worker_port = int(parsed_worker.port or settings.port)
        self_port = int(parsed_self.port or settings.port)
        if worker_port == self_port and (
            worker_host in {"localhost", "127.0.0.1", "0.0.0.0"} or worker_host == self_host
        ):
            return None
    except Exception:
        pass
    assigned_token = task.get("assigned_agent_token")
    resolved_token = assigned_token
    try:
        agent = get_repository_registry().agent_repo.get_by_url(worker_url)
        current_token = str(getattr(agent, "token", "") or "").strip()
        if current_token:
            resolved_token = current_token
    except Exception:
        pass
    try:
        response = forwarder(worker_url, endpoint, payload, token=resolved_token)
        if response is None and resolved_token:
            response = forwarder(worker_url, endpoint, payload, token=None)
        if (
            resolved_token
            and isinstance(response, dict)
            and str(response.get("status") or "").strip().lower() == "error"
            and ("401" in str(response.get("message") or "").lower() or "unauthorized" in str(response.get("message") or "").lower())
        ):
            response = forwarder(worker_url, endpoint, payload, token=None)
        # Worker returned 404: task not in worker DB (split-DB dev setup).
        # Configurable via execution_fallback_policy.worker_404_hub_fallback_enabled.
        if (
            isinstance(response, dict)
            and str(response.get("status") or "").strip().lower() == "error"
            and int(response.get("http_status") or 0) == 404
        ):
            _fallback_policy = {}
            try:
                _fallback_policy = dict(current_app.config.get("AGENT_CONFIG", {}).get("execution_fallback_policy") or {})
            except Exception:
                pass
            if bool(_fallback_policy.get("worker_404_hub_fallback_enabled", True)):
                current_app.logger.warning(
                    "Worker %s returned 404 for %s — falling back to local hub execution",
                    worker_url,
                    endpoint,
                )
                return None
        response = unwrap_api_envelope(response)
        if not isinstance(response, dict) or not response:
            raise RuntimeError(f"worker_empty_payload:{worker_url}:{endpoint}")
        if isinstance(response, dict):
            on_success(response, task)
        return TaskScopedRouteResponse(data=response)
    except Exception as exc:
        err_text = str(exc or "")
        err_lc = err_text.lower()
        if assigned_token and ("401" in err_lc or "unauthorized" in err_lc):
            try:
                response = forwarder(worker_url, endpoint, payload, token=None)
                response = unwrap_api_envelope(response)
                if isinstance(response, dict):
                    on_success(response, task)
                return TaskScopedRouteResponse(data=response)
            except Exception:
                pass
        current_app.logger.error("Forwarding an %s fehlgeschlagen: %s", worker_url, exc)
        raise WorkerForwardingError(details={"details": str(exc), "worker_url": worker_url})


def persist_forwarded_proposal(
    response: dict,
    task: dict,
    request_payload: dict | None = None,
    *,
    allow_synthetic_llm_profile_fallback: Callable[[], bool],
) -> None:
    if not isinstance(response, dict):
        return
    request_payload = dict(request_payload or {})
    prompt_text = str(request_payload.get("prompt") or "").strip()
    forwarded_request = {
        "prompt_preview": prompt_text[:240],
        "prompt_hash_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() if prompt_text else None,
        "provider": str(request_payload.get("provider") or "").strip() or None,
        "providers": list(request_payload.get("providers") or []) if isinstance(request_payload.get("providers"), list) else None,
        "model": str(request_payload.get("model") or "").strip() or None,
        "temperature": request_payload.get("temperature"),
        "strategy_mode": str(request_payload.get("strategy_mode") or "").strip() or None,
        "request_task_id": str(request_payload.get("task_id") or "").strip() or None,
        "captured_at": time.time(),
    }
    has_proposal_payload = any(
        key in response
        for key in (
            "command",
            "tool_calls",
            "reason",
            "raw",
            "routing",
            "cli_result",
            "trace",
            "review",
            "pipeline",
            "research_artifact",
            "research_context",
            "worker_context",
        )
    )
    if not has_proposal_payload:
        return
    response_trace = response.get("trace") if isinstance(response.get("trace"), dict) else None
    if not response_trace:
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        wrapped = response.get("proposal") if isinstance(response.get("proposal"), dict) else {}
        wrapped_meta = wrapped.get("metadata") if isinstance(wrapped.get("metadata"), dict) else {}
        prompt_trace_id = (
            str(metadata.get("prompt_trace_id") or "").strip()
            or str(wrapped_meta.get("prompt_trace_id") or "").strip()
        )
        if prompt_trace_id:
            response_trace = {
                "trace_id": prompt_trace_id,
                "source": "model_invocation_service",
                "request_kind": "propose",
            }
        else:
            response_trace = {
                "source": "external_worker_uninspectable",
                "request_kind": "propose",
                "external_worker_uninspectable": True,
            }
    cli_result = response.get("cli_result") if isinstance(response.get("cli_result"), dict) else None
    if not isinstance(cli_result, dict):
        response_meta = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        meta_profile = [
            normalize_llm_call_profile_entry(entry)
            for entry in list(response_meta.get("llm_call_profile") or [])
            if isinstance(entry, dict)
        ]
        if meta_profile:
            cli_result = {
                "returncode": 0,
                "latency_ms": None,
                "output_source": str(response.get("backend") or "orchestrator").strip() or "orchestrator",
                "llm_call_profile": meta_profile,
            }
    if cli_result is None:
        snapshot = get_core_services().autopilot_decision_service.build_proposal_snapshot(response)
        snapshot_cli = snapshot.get("cli_result") if isinstance(snapshot.get("cli_result"), dict) else None
        if isinstance(snapshot_cli, dict):
            cli_result = dict(snapshot_cli)
    if not isinstance(cli_result, dict) and allow_synthetic_llm_profile_fallback():
        backend = str(response.get("backend") or "orchestrator").strip() or "orchestrator"
        model = str(response.get("model") or "").strip() or None
        provider = None
        ms = response.get("model_selection")
        if isinstance(ms, dict):
            provider = str(ms.get("runtime_provider") or "").strip() or None
            model = model or (str(ms.get("selected_model") or "").strip() or None)
        cli_result = {
            "returncode": 0,
            "latency_ms": None,
            "output_source": backend,
            "llm_call_profile": [
                {
                    "name": "propose_forwarded",
                    "backend": backend,
                    "provider": provider,
                    "model": model,
                    "success": True,
                    "latency_ms": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "source": "orchestrator_synthetic",
                    "estimated": True,
                    "error_type": None,
                    "error_message": None,
                    "started_at": None,
                    "ended_at": None,
                }
            ],
        }
    if not isinstance(cli_result, dict):
        cli_result = {
            "returncode": 0,
            "latency_ms": None,
            "output_source": str(response.get("backend") or "orchestrator").strip() or "orchestrator",
        }
    get_core_services().task_execution_service.persist_task_proposal_result(
        tid=task["id"],
        task=task,
        reason=str(response.get("reason") or ""),
        raw=str(response.get("raw") or ""),
        backend=(str(response.get("backend") or "").strip() or None),
        model=(str(response.get("model") or "").strip() or None),
        routing=response.get("routing") if isinstance(response.get("routing"), dict) else None,
        cli_result=cli_result,
        worker_context=response.get("worker_context") if isinstance(response.get("worker_context"), dict) else None,
        trace=response_trace,
        review=response.get("review") if isinstance(response.get("review"), dict) else None,
        pipeline=response.get("pipeline") if isinstance(response.get("pipeline"), dict) else None,
        command=(str(response.get("command") or "").strip() or None),
        tool_calls=response.get("tool_calls") if isinstance(response.get("tool_calls"), list) else None,
        comparisons=response.get("comparisons") if isinstance(response.get("comparisons"), dict) else None,
        research_artifact=response.get("research_artifact") if isinstance(response.get("research_artifact"), dict) else None,
        research_context=response.get("research_context") if isinstance(response.get("research_context"), dict) else None,
        forwarded_request=forwarded_request,
        history_event={
            "event_type": "proposal_result",
            "reason": str(response.get("reason") or ""),
            "backend": response.get("backend"),
            "routing_reason": ((response.get("routing") or {}).get("reason")) if isinstance(response.get("routing"), dict) else None,
            "forwarded_request": forwarded_request,
            "forwarded": True,
            "timestamp": time.time(),
        },
    )


def persist_forwarded_execution(*, tid: str, response: dict, task: dict, request_data) -> None:
    if "status" not in response:
        return
    history = task.get("history", [])
    proposal_meta = task.get("last_proposal", {}) or {}
    verification_status = dict(task.get("verification_status") or {})
    execution_scope = response.get("execution_scope") if isinstance(response.get("execution_scope"), dict) else None
    execution_provenance = (
        response.get("execution_provenance") if isinstance(response.get("execution_provenance"), dict) else None
    )
    artifacts = normalize_forwarded_artifacts(
        task_id=tid,
        artifacts=list(response.get("artifacts") or []) if isinstance(response.get("artifacts"), list) else None,
    )
    review = response.get("review") if isinstance(response.get("review"), dict) else None
    if execution_scope:
        verification_status["execution_scope"] = dict(execution_scope)
    if execution_provenance:
        verification_status["execution_provenance"] = dict(execution_provenance)
    if artifacts is not None:
        verification_status["execution_artifacts"] = artifacts
    if review:
        verification_status["execution_review"] = dict(review)
    history.append(
        {
            "event_type": "execution_result",
            "prompt": task.get("description"),
            "reason": "Forwarded to " + str(task.get("assigned_agent_url")),
            "command": request_data.command or task.get("last_proposal", {}).get("command"),
            "output": response.get("output"),
            "exit_code": response.get("exit_code"),
            "backend": proposal_meta.get("backend"),
            "routing_reason": ((proposal_meta.get("routing") or {}).get("reason")),
            "artifacts": artifacts,
            "execution_scope": execution_scope,
            "execution_provenance": execution_provenance,
            "review": review,
            "forwarded": True,
            "timestamp": time.time(),
        }
    )
    update_local_task_status(
        tid,
        response["status"],
        history=history,
        last_output=response.get("output"),
        last_exit_code=response.get("exit_code"),
        verification_status=verification_status,
    )


def normalize_forwarded_artifacts(*, task_id: str, artifacts: list[dict] | None) -> list[dict] | None:
    if artifacts is None:
        return None
    normalized: list[dict] = []
    for idx, item in enumerate(artifacts, start=1):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        artifact_id = str(row.get("artifact_id") or row.get("id") or "").strip()
        kind = str(row.get("kind") or "").strip()
        path = str(row.get("path") or row.get("name") or row.get("filename") or row.get("title") or "").strip()
        if not artifact_id:
            artifact_id = f"{task_id}-artifact-{idx:03d}"
        if not kind:
            kind = "task_output"
        row["artifact_id"] = artifact_id
        row.setdefault("id", artifact_id)
        row["kind"] = kind
        if path:
            row["path"] = path
        row.setdefault("task_id", task_id)
        normalized.append(row)
    return normalized
