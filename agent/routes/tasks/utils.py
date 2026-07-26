import json
import time
from collections.abc import Mapping
from typing import Any

from flask import current_app

from agent.db_models import TaskDB
from agent.config import settings
from agent.services.repository_registry import get_repository_registry
from agent.services.task_runtime_service import (
    _subscribers_lock,
    _task_subscribers,
    append_task_history_event,
    get_local_task_status,
    notify_task_update,
    update_local_task_status,
)
from agent.services.task_status_service import normalize_task_status
from agent.utils import _http_post
from ananta_contracts.recovery_artifact_ingress import (
    MAX_RECOVERY_FORWARD_RESPONSE_BYTES,
)


def _repos():
    return get_repository_registry()

# Pub/Sub Mechanismus für Task-Updates (Liste von Tupeln: (tid, queue))
# In-Memory Cache für Tasks (Veraltet, durch Paginierung ersetzt)
_tasks_cache = None
_last_cache_update = 0
_last_archive_check = 0
task_repo = get_repository_registry().task_repo


def _get_tasks_cache():
    # Diese Funktion wird nur noch intern verwendet, falls nötig.
    # Für öffentliche APIs wird jetzt Paginierung direkt im Repository genutzt.
    tasks = task_repo.get_all()
    return {t.id: t.model_dump() for t in tasks}


def _notify_task_update(tid: str):
    notify_task_update(tid)


def _get_local_task_status(tid: str):
    return get_local_task_status(tid)


def _update_local_task_status(
    tid: str,
    status: str,
    event_type: str | None = None,
    event_actor: str = "system",
    event_details: dict | None = None,
    **kwargs,
):
    update_local_task_status(
        tid,
        status,
        event_type=event_type,
        event_actor=event_actor,
        event_details=event_details,
        **kwargs,
    )


def _forward_to_worker(worker_url: str, endpoint: str, data: dict, token: str = None) -> Any:
    timeout = int(getattr(settings, "http_timeout", 60) or 60)
    try:
        agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if current_app else {}
    except RuntimeError:
        agent_cfg = {}
    command_timeout = max(1, int(agent_cfg.get("command_timeout") or timeout or 60))
    endpoint_name = str(endpoint or "").strip().lower()
    if endpoint_name.endswith("/step/propose"):
        timeout = max(command_timeout + 120, 180)
    else:
        timeout = max(timeout, command_timeout)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    url = f"{worker_url.rstrip('/')}/{endpoint.lstrip('/')}"
    recovery_forward = bool(
        str(data.get("dispatch_lease_token") or "").strip()
    )
    request_options: dict[str, Any] = {
        "data": data,
        "headers": headers,
        "timeout": timeout,
        "return_response": True,
        "silent": True,
    }
    if recovery_forward:
        request_options["stream"] = True
    try:
        response = _http_post(url, **request_options)
    except TypeError:
        # Backward-compatible path for callsites/tests that monkeypatch _http_post
        # without newer keyword arguments.
        response = _http_post(url, data=data, headers=headers, timeout=timeout)
    if response is None:
        return None
    if isinstance(response, dict):
        return response
    code = int(getattr(response, "status_code", 500) or 500)
    if recovery_forward:
        body = _parse_bounded_recovery_worker_response(response)
        if code < 400:
            return body
        return _worker_error_payload(
            body=body,
            code=code,
            worker_url=worker_url,
            endpoint=endpoint,
        )
    # Preserve API envelope on success.
    if code < 400:
        try:
            return response.json()
        except Exception:
            return {"status": "ok", "data": {}}
    # Structured error payload for caller-side diagnostics/backoff.
    body: Any
    try:
        body = response.json()
    except Exception:
        body = {"raw": str(getattr(response, "text", "") or "")[:600]}
    return _worker_error_payload(
        body=body,
        code=code,
        worker_url=worker_url,
        endpoint=endpoint,
    )


def _parse_bounded_recovery_worker_response(response: Any) -> Any:
    """Stream and parse one Recovery response only after enforcing its cap."""

    maximum_bytes = MAX_RECOVERY_FORWARD_RESPONSE_BYTES
    headers = getattr(response, "headers", None)
    content_length = (
        headers.get("Content-Length")
        if isinstance(headers, Mapping)
        else None
    )
    try:
        declared_bytes = int(content_length)
    except (TypeError, ValueError):
        declared_bytes = None
    try:
        if (
            declared_bytes is not None
            and declared_bytes > maximum_bytes
        ):
            raise ValueError("recovery_worker_response_too_large")
        chunks: list[bytes] = []
        total_bytes = 0
        iter_content = getattr(response, "iter_content", None)
        if callable(iter_content):
            content_chunks = iter_content(
                chunk_size=64 * 1024,
                decode_unicode=False,
            )
        else:
            content_chunks = (
                getattr(response, "content", b""),
            )
        for chunk in content_chunks:
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                raise ValueError(
                    "recovery_worker_response_bytes_invalid"
                )
            total_bytes += len(chunk)
            if total_bytes > maximum_bytes:
                raise ValueError(
                    "recovery_worker_response_too_large"
                )
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks))
        except (
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "recovery_worker_response_json_invalid"
            ) from exc
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _worker_error_payload(
    *,
    body: Any,
    code: int,
    worker_url: str,
    endpoint: str,
) -> dict[str, Any]:
    message = None
    if isinstance(body, dict):
        message = body.get("message") or body.get("error")
        details = body.get("data") if isinstance(body.get("data"), dict) else body
    else:
        details = {"raw": str(body)}
    return {
        "status": "error",
        "message": str(message or f"http_{code}"),
        "http_status": code,
        "details": details,
        "worker_url": worker_url,
        "endpoint": endpoint,
    }
