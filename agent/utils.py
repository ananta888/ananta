import time
import logging
import json
import os
import portalocker
from functools import wraps
from flask import jsonify, request, g
from collections import defaultdict
from typing import Any, Optional, Callable, Type, Dict, List
from pydantic import ValidationError, BaseModel
from agent.config import settings
from agent.common.errors import (
    AnantaError, TransientError, PermanentError, ValidationError as AnantaValidationError
)

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
from agent.metrics import HTTP_REQUEST_DURATION
from agent.common.http import get_default_client

# Konstanten (sollten idealerweise aus Settings kommen, hier als Fallback)
HTTP_TIMEOUT = settings.http_timeout

# In-Memory Storage für einfaches Rate-Limiting
_rate_limit_storage = defaultdict(list)

def _http_get(url: str, params: dict | None = None, timeout: int = HTTP_TIMEOUT) -> Any:
    with HTTP_REQUEST_DURATION.labels(method="GET", target=url).time():
        client = get_default_client(timeout=timeout)
        return client.get(url, params=params, timeout=timeout)

def _http_post(url: str, data: dict | None = None, headers: dict | None = None, form: bool = False, timeout: int = HTTP_TIMEOUT) -> Any:
    with HTTP_REQUEST_DURATION.labels(method="POST", target=url).time():
        client = get_default_client(timeout=timeout)
        return client.post(url, data=data, headers=headers, form=form, timeout=timeout)

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
                return jsonify({
                    "error": "rate_limit_exceeded",
                    "message": f"Limit von {limit} Anfragen pro {window}s überschritten."
                }), 429
            
            _rate_limit_storage[ident].append(now)
            return f(*args, **kwargs)
        return wrapper
    return decorator

def _extract_command(text: str) -> str:
    """Extrahiert den Shell-Befehl aus dem LLM-Output."""
    if "```bash" in text:
        return text.split("```bash")[1].split("```")[0].strip()
    if "```sh" in text:
        return text.split("```sh")[1].split("```")[0].strip()
    if "```" in text:
        return text.split("```")[1].split("```")[0].strip()
    return text.strip()

def _extract_reason(text: str) -> str:
    """Extrahiert die Begründung (alles vor dem Code-Block)."""
    if "```" in text:
        return text.split("```")[0].strip()
    return "Keine Begründung angegeben."

def read_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with portalocker.Lock(path, mode="r", encoding="utf-8", timeout=5, flags=portalocker.LOCK_SH) as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Fehler beim Lesen von {path}: {e}")
        return default

def write_json(path: str, data: Any) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with portalocker.Lock(path, mode="w", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX) as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"Fehler beim Schreiben von {path}: {e}")

def register_with_hub(hub_url: str, agent_name: str, port: int, token: str, role: str = "worker") -> bool:
    """Registriert den Agenten beim Hub."""
    payload = {
        "name": agent_name,
        "url": f"http://localhost:{port}",
        "role": role,
        "token": token
    }
    try:
        _http_post(f"{hub_url}/register", payload)
        logging.info(f"Erfolgreich am Hub ({hub_url}) registriert.")
        return True
    except Exception as e:
        logging.warning(f"Hub-Registrierung fehlgeschlagen: {e}")
        return False

def _get_approved_command(controller: str, cmd: str, prompt: str) -> Optional[str]:
    """Sendet Befehl zur Genehmigung. Gibt finalen Befehl oder None (SKIP) zurück."""
    approval = _http_post(
        f"{controller}/approve",
        {"cmd": cmd, "summary": prompt},
        form=True
    )
    
    if isinstance(approval, str):
        if approval.strip().upper() == "SKIP":
            return None
        # String-Antwort = überschriebener Befehl (außer Status-Meldungen)
        if approval.strip() not in ('{"status": "approved"}', "approved"):
            return approval.strip()
    elif isinstance(approval, dict):
        # Expliziter Override
        override = approval.get("cmd")
        if isinstance(override, str) and override.strip():
            return override.strip()
    
    return cmd  # Original-Befehl genehmigt

def log_to_db(agent_name: str, level: str, message: str) -> None:
    """(Platzhalter) Loggt eine Nachricht in die DB via Controller."""
    # In dieser Version deaktiviert oder via _http_post an den Hub
    pass

def _log_terminal_entry(agent_name: str, step: int, direction: str, **kwargs: Any) -> None:
    """Schreibt einen Eintrag ins Terminal-Log (JSONL)."""
    log_file = os.path.join(settings.data_dir, "terminal_log.jsonl")
    entry = {
        "timestamp": time.time(),
        "agent": agent_name,
        "step": step,
        "direction": direction,
        **kwargs
    }
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with portalocker.Lock(log_file, mode="a", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX) as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logging.error(f"Fehler beim Schreiben ins Terminal-Log: {e}")
