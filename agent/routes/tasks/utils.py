import json
import queue
import threading
from collections.abc import Mapping
from typing import Any

from flask import current_app
from requests.exceptions import RequestException

from agent.common.http import (
    HttpTransportCancelled,
    HttpTransportDeadlineExceeded,
    HttpTransportResponseLost,
    close_http_response,
)
from agent.config import settings
from agent.services.knowledge_index_task_ingress_policy import (
    has_bound_knowledge_index_job,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.task_runtime_service import (
    _subscribers_lock as _subscribers_lock,
)
from agent.services.task_runtime_service import (
    _task_subscribers as _task_subscribers,
)
from agent.services.task_runtime_service import (
    get_local_task_status,
    notify_task_update,
    update_local_task_status,
)
from agent.services.vector_index_worker_result_boundary import (
    MAX_VECTOR_INDEX_WORKER_RESULT_BYTES,
)
from agent.services.worker_forward_transport import (
    WorkerForwardAmbiguousTransportError,
    WorkerForwardDeadlineExceeded,
    WorkerForwardPermanentTransportError,
    WorkerTransportDeadline,
)
from agent.utils import _http_post
from ananta_contracts.knowledge_index_execution import (
    MAX_KNOWLEDGE_INDEX_WORKER_RESULT_BYTES,
)
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
    task = get_local_task_status(tid)
    if has_bound_knowledge_index_job(task):
        # Bound v2 state belongs to the Hub forwarding/binding CAS saga.
        # Autopilot may dispatch it, but must not project status or results
        # through the generic task writer.
        return None
    update_local_task_status(
        tid,
        status,
        event_type=event_type,
        event_actor=event_actor,
        event_details=event_details,
        **kwargs,
    )


def _forward_to_worker(
    worker_url: str,
    endpoint: str,
    data: dict,
    token: str = None,
    *,
    transport_deadline: WorkerTransportDeadline | None = None,
) -> Any:
    deadline_monotonic = None
    if transport_deadline is not None:
        if not isinstance(transport_deadline, WorkerTransportDeadline):
            raise WorkerForwardPermanentTransportError(
                "worker_forward_transport_deadline_invalid"
            )
        # A governed deadline is an upper bound, never a minimum.  Global
        # command/http settings therefore cannot extend this request.
        timeout = transport_deadline.requests_timeout()
        deadline_monotonic = transport_deadline.expires_at_monotonic
    else:
        try:
            timeout = max(
                1,
                int(getattr(settings, "http_timeout", 60) or 60),
            )
        except (TypeError, ValueError):
            timeout = 60
        try:
            agent_cfg = (
                current_app.config.get("AGENT_CONFIG", {}) or {}
            ) if current_app else {}
        except RuntimeError:
            agent_cfg = {}
        try:
            command_timeout = max(
                1,
                int(agent_cfg.get("command_timeout") or timeout or 60),
            )
        except (TypeError, ValueError):
            command_timeout = timeout
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
    vector_forward = isinstance(
        data.get("vector_index_dispatch"),
        Mapping,
    )
    knowledge_index_forward = bool(
        isinstance(data.get("knowledge_index_dispatch"), Mapping)
        and data["knowledge_index_dispatch"].get("task_kind")
        == "codecompass_index_build"
    )
    secure_forward = (
        recovery_forward
        or vector_forward
        or knowledge_index_forward
        or transport_deadline is not None
    )
    request_options: dict[str, Any] = {
        "data": data,
        "headers": headers,
        "timeout": timeout,
        "return_response": True,
        "silent": True,
        "allow_redirects": False,
    }
    if secure_forward:
        request_options["stream"] = True
    if deadline_monotonic is not None:
        request_options["deadline_monotonic"] = deadline_monotonic
    if knowledge_index_forward and transport_deadline is not None:
        request_options["raise_on_transport_error"] = True
    try:
        response = _http_post(url, **request_options)
    except HttpTransportDeadlineExceeded as exc:
        raise WorkerForwardDeadlineExceeded() from exc
    except HttpTransportResponseLost as exc:
        raise WorkerForwardAmbiguousTransportError() from exc
    except HttpTransportCancelled as exc:
        raise WorkerForwardPermanentTransportError(
            "worker_forward_transport_cancelled"
        ) from exc
    except TypeError:
        if secure_forward:
            raise WorkerForwardPermanentTransportError(
                "worker_forward_secure_transport_unsupported"
            )
        # Backward-compatible path for callsites/tests that monkeypatch _http_post
        # without newer keyword arguments.
        response = _http_post(url, data=data, headers=headers, timeout=timeout)
    if response is None:
        if (
            transport_deadline is not None
            and transport_deadline.remaining_seconds() <= 0
        ):
            raise WorkerForwardDeadlineExceeded()
        return None
    if isinstance(response, dict):
        if transport_deadline is not None:
            transport_deadline.require_remaining_seconds()
        return response
    code = int(getattr(response, "status_code", 500) or 500)
    if 300 <= code < 400:
        close_http_response(response)
        raise WorkerForwardPermanentTransportError(
            "worker_forward_redirect_forbidden"
        )
    if recovery_forward:
        body = _parse_bounded_recovery_worker_response(
            response,
            transport_deadline=transport_deadline,
        )
        if code < 400:
            return body
        return _worker_error_payload(
            body=body,
            code=code,
            worker_url=worker_url,
            endpoint=endpoint,
        )
    if vector_forward:
        body = _parse_bounded_vector_worker_response(
            response,
            transport_deadline=transport_deadline,
        )
        if code < 400:
            return body
        return _worker_error_payload(
            body=body,
            code=code,
            worker_url=worker_url,
            endpoint=endpoint,
        )
    if knowledge_index_forward or transport_deadline is not None:
        try:
            body = _parse_bounded_knowledge_index_worker_response(
                response,
                transport_deadline=transport_deadline,
            )
        except RequestException as exc:
            raise WorkerForwardAmbiguousTransportError() from exc
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
        finally:
            close_http_response(response)
    # Structured error payload for caller-side diagnostics/backoff.
    body: Any
    try:
        try:
            body = response.json()
        except Exception:
            body = {
                "raw": str(getattr(response, "text", "") or "")[:600]
            }
    finally:
        close_http_response(response)
    return _worker_error_payload(
        body=body,
        code=code,
        worker_url=worker_url,
        endpoint=endpoint,
    )


def _parse_bounded_recovery_worker_response(
    response: Any,
    *,
    transport_deadline: WorkerTransportDeadline | None = None,
) -> Any:
    """Stream and parse one Recovery response only after enforcing its cap."""

    return _parse_bounded_worker_response(
        response,
        maximum_bytes=MAX_RECOVERY_FORWARD_RESPONSE_BYTES,
        reason_prefix="recovery_worker_response",
        transport_deadline=transport_deadline,
    )


def _parse_bounded_vector_worker_response(
    response: Any,
    *,
    transport_deadline: WorkerTransportDeadline | None = None,
) -> Any:
    """Stream and parse a Vector result before Hub materialization."""

    return _parse_bounded_worker_response(
        response,
        maximum_bytes=MAX_VECTOR_INDEX_WORKER_RESULT_BYTES,
        reason_prefix="vector_index_worker_response",
        transport_deadline=transport_deadline,
    )


def _parse_bounded_knowledge_index_worker_response(
    response: Any,
    *,
    transport_deadline: WorkerTransportDeadline | None = None,
) -> Any:
    """Stream one CodeCompass result before Hub-side materialization."""

    return _parse_bounded_worker_response(
        response,
        maximum_bytes=MAX_KNOWLEDGE_INDEX_WORKER_RESULT_BYTES,
        reason_prefix="knowledge_index_worker_response",
        transport_deadline=transport_deadline,
    )


def _parse_bounded_worker_response(
    response: Any,
    *,
    maximum_bytes: int,
    reason_prefix: str,
    transport_deadline: WorkerTransportDeadline | None = None,
) -> Any:
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
            raise WorkerForwardPermanentTransportError(
                f"{reason_prefix}_too_large"
            )
        chunks: list[bytes] = []
        total_bytes = 0
        iter_content = getattr(response, "iter_content", None)
        if callable(iter_content):
            raw_chunks = iter_content(
                chunk_size=64 * 1024,
                decode_unicode=False,
            )
            content_chunks = _deadline_bounded_chunks(
                raw_chunks,
                transport_deadline=transport_deadline,
            )
        else:
            content_chunks = (
                getattr(response, "content", b""),
            )
        for chunk in content_chunks:
            if transport_deadline is not None:
                transport_deadline.require_remaining_seconds()
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                raise WorkerForwardPermanentTransportError(
                    f"{reason_prefix}_bytes_invalid"
                )
            total_bytes += len(chunk)
            if total_bytes > maximum_bytes:
                raise WorkerForwardPermanentTransportError(
                    f"{reason_prefix}_too_large"
                )
            chunks.append(chunk)
        try:
            parsed = json.loads(b"".join(chunks))
        except (
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            raise WorkerForwardPermanentTransportError(
                f"{reason_prefix}_json_invalid"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise WorkerForwardPermanentTransportError(
                f"{reason_prefix}_json_invalid"
            )
        if transport_deadline is not None:
            transport_deadline.require_remaining_seconds()
        return parsed
    finally:
        close_http_response(response)


def _deadline_bounded_chunks(
    content_chunks: Any,
    *,
    transport_deadline: WorkerTransportDeadline | None,
):
    """Yield response chunks while enforcing a wall-clock deadline.

    ``requests`` read timeouts measure socket inactivity rather than total
    elapsed time.  Reading on a daemon thread lets the Hub close the response
    and return at the authoritative deadline even when a peer slow-drips data.
    """

    if transport_deadline is None:
        yield from content_chunks
        return

    events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=2)
    stopped = threading.Event()

    def emit(kind: str, value: Any) -> None:
        while not stopped.is_set():
            try:
                events.put((kind, value), timeout=0.05)
                return
            except queue.Full:
                continue

    def produce() -> None:
        try:
            for chunk in content_chunks:
                if stopped.is_set():
                    return
                emit("chunk", chunk)
        except Exception as exc:
            emit("error", exc)
        finally:
            emit("done", None)

    reader = threading.Thread(target=produce, daemon=True)
    reader.start()
    try:
        while True:
            remaining = transport_deadline.require_remaining_seconds()
            try:
                kind, value = events.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if kind == "chunk":
                yield value
            elif kind == "error":
                raise value
            else:
                return
    finally:
        stopped.set()


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
