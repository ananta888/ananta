import json
import logging
import os
import time
from collections import defaultdict
from functools import wraps
from typing import Any, Callable, List, Optional, Type

from flask import current_app, g, request
from pydantic import BaseModel, ValidationError

from agent.common.errors import PermanentError, TransientError, api_response
from agent.common.errors import ValidationError as AnantaValidationError
from agent.common.gateways.http_hub_gateway import HttpHubGateway
from agent.common.http import get_default_client
from agent.common.utils import archive_utils, extraction_utils, json_utils, network_utils
from agent.config import settings
from agent.metrics import HTTP_REQUEST_DURATION


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


# In-Memory Storage für einfaches Rate-Limiting
_rate_limit_storage = defaultdict(list)
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


def rate_limit(limit: int, window: int) -> Callable:
    """Einfacher Decorator für Rate-Limiting (In-Memory)."""

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            now = time.time()
            ident = request.remote_addr or "unknown"

            # Bereinige alte Einträge außerhalb des Zeitfensters
            _rate_limit_storage[ident] = [ts for ts in _rate_limit_storage[ident] if now - ts < window]

            if len(_rate_limit_storage[ident]) >= limit:
                logging.warning(f"Rate Limit überschritten für {ident}")
                return api_response(
                    status="error", message=f"Limit von {limit} Anfragen pro {window}s überschritten.", code=429
                )

            _rate_limit_storage[ident].append(now)
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
    """Proxy für HttpHubGateway.register."""
    return HttpHubGateway(hub_url).register(agent_name, port, token, role, silent)


def _get_approved_command(hub_url: str, cmd: str, prompt: str) -> Optional[str]:
    """Proxy für HttpHubGateway.approve_command."""
    return HttpHubGateway(hub_url).approve_command(cmd, prompt)


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
