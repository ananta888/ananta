import json
import logging
import os
import time
from functools import wraps
from typing import Any, Callable, List, Optional, Type

import portalocker
from flask import current_app, g, request
from pydantic import BaseModel, ValidationError

from agent.common.errors import PermanentError, TransientError, api_response
from agent.common.errors import ValidationError as AnantaValidationError
from agent.common.http import get_default_client
from agent.common.utils import archive_utils, extraction_utils, json_utils, network_utils
from agent.config import settings
from agent.metrics import HTTP_REQUEST_DURATION
from agent.services.rate_limit_service import get_rate_limit_service


def get_data_dir() -> str:
    """Gibt das Datenverzeichnis zurück, bevorzugt aus der Flask-Config."""
    try:
        if current_app:
            return current_app.config.get("DATA_DIR", settings.data_dir)
    except RuntimeError:
        pass
    return settings.data_dir


def get_host_gateway_ip() -> Optional[str]:
    """Proxy für network_utils.get_host_gateway_ip (Abwärtskompatibilität)."""
    return network_utils.get_host_gateway_ip()


def validate_request(model: Type[BaseModel]) -> Callable:
    """Decorator zur Validierung des Request-Body mit Pydantic."""

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                data = request.get_json(silent=True) or {}
                # Validierung gegen das Modell
                validated = model(**data)
                # Speichere validierte Daten in Flask 'g'
                g.validated_data = validated
                return f(*args, **kwargs)
            except ValidationError as e:
                # Wir werfen unsere eigene Exception für den globalen Handler
                raise AnantaValidationError("Validierung fehlgeschlagen", details=e.errors())

        return wrapper

    return decorator


_last_terminal_archive_check = 0


def _archive_terminal_logs() -> None:
    """Proxy für archive_utils.archive_terminal_logs."""
    global _last_terminal_archive_check
    now = time.time()
    if now - _last_terminal_archive_check < 3600:
        return
    _last_terminal_archive_check = now
    archive_utils.archive_terminal_logs(get_data_dir())


def _cleanup_old_backups():
    """Proxy für archive_utils.cleanup_old_backups."""
    archive_utils.cleanup_old_backups(get_data_dir())


def _archive_old_tasks(tasks_path=None):
    """Proxy für archive_utils.archive_old_tasks."""
    if tasks_path is None:
        try:
            tasks_path = current_app.config.get("TASKS_PATH", "data/tasks.json")
        except RuntimeError:
            tasks_path = os.path.join(settings.data_dir, "tasks.json")
    # Preserve the historical JSON cleanup seam for tests and explicit file-based runs.
    if tasks_path and os.path.exists(tasks_path):
        archive_path = str(tasks_path).replace(".json", "_archive.json")
        if os.path.exists(archive_path):
            now = time.time()
            cutoff_archive = now - (settings.archived_tasks_retention_days * 86400)

            def cleanup_archive_func(archived_tasks):
                if not isinstance(archived_tasks, dict):
                    return archived_tasks
                return {
                    tid: task
                    for tid, task in archived_tasks.items()
                    if float((task or {}).get("archived_at", (task or {}).get("created_at", now)) or now) >= cutoff_archive
                }

            update_json(archive_path, cleanup_archive_func, default={})
    archive_utils.archive_old_tasks(tasks_path)


def _http_get(
    url: str,
    params: dict | None = None,
    timeout: int | None = None,
    return_response: bool = False,
    silent: bool = False,
) -> Any:
    if timeout is None:
        timeout = settings.http_timeout
    with HTTP_REQUEST_DURATION.labels(method="GET", target=url).time():
        client = get_default_client(timeout=timeout)
        return client.get(url, params=params, timeout=timeout, return_response=return_response, silent=silent)


def _http_post(
    url: str,
    data: dict | None = None,
    headers: dict | None = None,
    form: bool = False,
    timeout: int | None = None,
    return_response: bool = False,
    silent: bool = False,
    idempotency_key: Optional[str] = None,
) -> Any:
    if timeout is None:
        timeout = settings.http_timeout
    with HTTP_REQUEST_DURATION.labels(method="POST", target=url).time():
        client = get_default_client(timeout=timeout)
        return client.post(
            url,
            data=data,
            headers=headers,
            form=form,
            timeout=timeout,
            return_response=return_response,
            silent=silent,
            idempotency_key=idempotency_key,
        )


def rate_limit(limit: int, window: int, *, namespace: str = "http") -> Callable:
    """Shared decorator for endpoint throttling with Redis/in-memory fallback."""

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ident = request.remote_addr or "unknown"
            effective_namespace = f"{namespace}:{request.endpoint or f.__name__}"
            allowed = get_rate_limit_service().allow_request(
                namespace=effective_namespace,
                subject=str(ident),
                limit=limit,
                window_seconds=window,
            )
            if not allowed:
                logging.warning("Rate Limit ueberschritten fuer %s in %s", ident, effective_namespace)
                return api_response(
                    status="error", message=f"Limit von {limit} Anfragen pro {window}s überschritten.", code=429
                )
            return f(*args, **kwargs)

        return wrapper

    return decorator


def _extract_command(text: str) -> str:
    """Proxy für extraction_utils.extract_command."""
    return extraction_utils.extract_command(text)


def _extract_reason(text: str) -> str:
    """Proxy für extraction_utils.extract_reason."""
    return extraction_utils.extract_reason(text)


def _extract_tool_calls(text: str) -> Optional[List[dict]]:
    """Proxy für extraction_utils.extract_tool_calls."""
    return extraction_utils.extract_tool_calls(text)


def read_json(path: str, default: Any = None) -> Any:
    """Proxy für json_utils.read_json."""
    return json_utils.read_json(path, default)


def write_json(path: str, data: Any, chmod: Optional[int] = None) -> None:
    """Proxy für json_utils.write_json."""
    return json_utils.write_json(path, data, chmod)


def update_json(path: str, update_func: Callable[[Any], Any], default: Any = None) -> Any:
    """Proxy für json_utils.update_json."""
    return json_utils.update_json(path, update_func, default)


def register_with_hub(
    hub_url: str, agent_name: str, port: int, token: str, role: str = "worker", silent: bool = False
) -> bool:
    """Backward-compatible proxy for hub registration."""
    agent_url = settings.agent_url or f"http://localhost:{port}"
    payload = {"name": agent_name, "url": agent_url, "role": role, "token": token}
    if role == "worker":
        # Keep registration compatible with hub contract requiring worker capabilities.
        payload["worker_roles"] = ["planner", "researcher", "coder", "reviewer", "tester"]
        payload["capabilities"] = ["planning", "analysis", "research", "coding", "implementation", "review", "testing", "verification"]
        payload["execution_limits"] = {"max_parallel_tasks": 2, "max_runtime_seconds": 1800, "max_workspace_mb": 2048}
    if settings.registration_token:
        payload["registration_token"] = settings.registration_token
    try:
        response = _http_post(f"{hub_url}/register", data=payload, silent=silent)
        if isinstance(response, dict) and "agent_token" in response:
            new_token = response["agent_token"]
            if new_token and new_token != token:
                settings.save_agent_token(new_token)
        if not silent:
            logging.info(f"Erfolgreich am Hub ({hub_url}) registriert.")
        return True
    except Exception as exc:
        if not silent:
            logging.warning(f"Hub-Registrierung fehlgeschlagen: {exc}")
        return False


def _get_approved_command(hub_url: str, cmd: str, prompt: str) -> Optional[str]:
    """Backward-compatible proxy for command approval."""
    try:
        approval = _http_post(f"{hub_url}/approve", data={"cmd": cmd, "summary": prompt}, form=True)
        if isinstance(approval, str):
            if approval.strip().upper() == "SKIP":
                return None
            if approval.strip() not in ('{"status": "approved"}', "approved"):
                return approval.strip()
        elif isinstance(approval, dict):
            override = approval.get("cmd")
            if isinstance(override, str) and override.strip():
                return override.strip()
        return cmd
    except Exception as exc:
        logging.error(f"Fehler bei der Genehmigungsanfrage am Hub: {exc}")
        return cmd


def log_to_db(agent_name: str, level: str, message: str) -> None:
    """(Platzhalter) Loggt eine Nachricht in die DB via Hub."""
    pass


def _log_terminal_entry(agent_name: str, step: int, direction: str, **kwargs: Any) -> None:
    """Schreibt einen Eintrag ins Terminal-Log (JSONL)."""
    data_dir = get_data_dir()
    log_file = os.path.join(data_dir, "terminal_log.jsonl")
    entry = {"timestamp": time.time(), "agent": agent_name, "step": step, "direction": direction, **kwargs}
    try:
        os.makedirs(data_dir, exist_ok=True)
        with portalocker.Lock(
            log_file, mode="a", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX | portalocker.LOCK_NB
        ) as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logging.error(f"Fehler beim Schreiben ins Terminal-Log: {e}")


def log_llm_entry(event: str, **kwargs: Any) -> None:
    """Schreibt einen Eintrag ins LLM-Log (JSONL)."""
    data_dir = get_data_dir()
    log_file = os.path.join(data_dir, "llm_log.jsonl")
    entry = {"timestamp": time.time(), "event": event, **kwargs}
    try:
        os.makedirs(data_dir, exist_ok=True)
        with portalocker.Lock(
            log_file, mode="a", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX | portalocker.LOCK_NB
        ) as f:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception as e:
        logging.error(f"Fehler beim Schreiben ins LLM-Log: {e}")
