import time
import logging
import json
import os
import threading
import portalocker
import portalocker.exceptions
from functools import wraps
from flask import jsonify, request, g, current_app
from collections import defaultdict
from typing import Any, Optional, Callable, Type, Dict, List
from pydantic import ValidationError, BaseModel
from agent.config import settings
from agent.common.errors import (
    AnantaError, TransientError, PermanentError, ValidationError as AnantaValidationError
)

def get_data_dir() -> str:
    """Gibt das Datenverzeichnis zurück, bevorzugt aus der Flask-Config."""
    try:
        if current_app:
            return current_app.config.get("DATA_DIR", settings.data_dir)
    except RuntimeError:
        pass
    return settings.data_dir

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
_last_terminal_archive_check = 0

def _archive_terminal_logs() -> None:
    """Archiviert alte Einträge aus dem Terminal-Log."""
    global _last_terminal_archive_check
    now = time.time()
    if now - _last_terminal_archive_check < 3600:
        return
    _last_terminal_archive_check = now
    
    data_dir = get_data_dir()
    log_file = os.path.join(data_dir, "terminal_log.jsonl")
    if not os.path.exists(log_file):
        return
        
    archive_file = log_file.replace(".jsonl", "_archive.jsonl")
    retention_days = settings.tasks_retention_days
    cutoff = now - (retention_days * 86400)
    
    try:
        remaining_entries = []
        archived_entries = []
        
        # Wir müssen die Datei sperren während wir lesen und schreiben
        with portalocker.Lock(log_file, mode="r+", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX) as f:
            lines = f.readlines()
            for line in lines:
                if not line.strip(): continue
                try:
                    entry = json.loads(line)
                    if entry.get("timestamp", now) < cutoff:
                        archived_entries.append(line)
                    else:
                        remaining_entries.append(line)
                except Exception:
                    remaining_entries.append(line)
            
            if archived_entries:
                logging.info(f"Archiviere {len(archived_entries)} Terminal-Log Einträge.")
                with open(archive_file, "a", encoding="utf-8") as af:
                    # Hier könnten wir auch locken, aber da wir nur anhängen ist es unkritisch 
                    # wenn wir davon ausgehen dass nur dieser Prozess archiviert.
                    # Aber Sicherer ist mit Lock.
                    with portalocker.Lock(archive_file, mode="a", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX) as afl:
                        for line in archived_entries:
                            afl.write(line)
                
                f.seek(0)
                f.truncate()
                for line in remaining_entries:
                    f.write(line)
    except Exception as e:
        logging.error(f"Fehler bei der Archivierung des Terminal-Logs: {e}")

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
    """Extrahiert den Shell-Befehl aus dem LLM-Output (JSON oder Markdown)."""
    text = text.strip()
    
    # 1. Versuche JSON-Extraktion
    try:
        if "```json" in text:
            json_str = text.split("```json")[1].split("```")[0].strip()
            data = json.loads(json_str)
        else:
            data = json.loads(text)
        
        if isinstance(data, dict) and "command" in data:
            return str(data["command"]).strip()
    except Exception:
        pass

    # 2. Fallback auf Markdown Code-Blöcke
    if "```bash" in text:
        return text.split("```bash")[1].split("```")[0].strip()
    if "```sh" in text:
        return text.split("```sh")[1].split("```")[0].strip()
    if "```" in text:
        # Nehme den ersten Codeblock, falls vorhanden
        parts = text.split("```")
        if len(parts) >= 3:
            return parts[1].strip()
    
    return text.strip()

def _extract_reason(text: str) -> str:
    """Extrahiert die Begründung (JSON 'reason' oder Text vor dem Code-Block)."""
    text = text.strip()
    
    # 1. Versuche JSON-Extraktion
    try:
        if "```json" in text:
            json_str = text.split("```json")[1].split("```")[0].strip()
            data = json.loads(json_str)
        else:
            data = json.loads(text)
            
        if isinstance(data, dict) and "reason" in data:
            return str(data["reason"]).strip()
    except Exception:
        pass

    # 2. Fallback: Alles vor dem ersten Code-Block
    if "```" in text:
        reason = text.split("```")[0].strip()
        return reason if reason else "Befehl extrahiert."
    
    return "Keine Begründung angegeben."

def read_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default
    
    retries = 3
    for i in range(retries):
        try:
            with portalocker.Lock(path, mode="r", encoding="utf-8", timeout=2, flags=portalocker.LOCK_SH) as f:
                return json.load(f)
        except (portalocker.exceptions.LockException, portalocker.exceptions.AlreadyLocked):
            if i < retries - 1:
                logging.warning(f"Datei {path} gesperrt, Retry {i+1}/{retries}...")
                time.sleep(0.5)
                continue
            logging.error(f"Timeout beim Sperren von {path} nach {retries} Versuchen.")
            raise TransientError(f"Datei {path} ist dauerhaft gesperrt.")
        except Exception as e:
            logging.error(f"Fehler beim Lesen von {path}: {e}")
            return default

def write_json(path: str, data: Any, chmod: Optional[int] = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    retries = 3
    for i in range(retries):
        try:
            # Falls chmod gesetzt ist und die Datei neu erstellt wird, 
            # setzen wir restriktive Berechtigungen von Anfang an.
            if chmod is not None and not os.path.exists(path):
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT, chmod)
                    os.close(fd)
                except Exception:
                    pass

            with portalocker.Lock(path, mode="w", encoding="utf-8", timeout=2, flags=portalocker.LOCK_EX) as f:
                json.dump(data, f, indent=2)
                if chmod is not None:
                    try:
                        os.chmod(path, chmod)
                    except Exception:
                        pass
                return
        except (portalocker.exceptions.LockException, portalocker.exceptions.AlreadyLocked):
            if i < retries - 1:
                logging.warning(f"Datei {path} für Schreibzugriff gesperrt, Retry {i+1}/{retries}...")
                time.sleep(0.5)
                continue
            logging.error(f"Timeout beim Sperren (Schreiben) von {path} nach {retries} Versuchen.")
            raise TransientError(f"Datei {path} konnte nicht geschrieben werden (Sperre).")
        except Exception as e:
            logging.error(f"Fehler beim Schreiben von {path}: {e}")
            raise PermanentError(f"Kritischer Fehler beim Schreiben von {path}: {e}")

def update_json(path: str, update_func: Callable[[Any], Any], default: Any = None) -> Any:
    """Führt einen atomaren Read-Modify-Write Zyklus auf einer JSON-Datei aus."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    retries = 10  # Mehr Retries für hohe Nebenläufigkeit
    for i in range(retries):
        try:
            # 'a+' verhindert das Leeren beim Öffnen, erlaubt aber Lesen und Schreiben.
            with portalocker.Lock(path, mode="a+", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX) as f:
                f.seek(0)
                content = f.read()
                data = default
                if content.strip():
                    try:
                        data = json.loads(content)
                    except (json.JSONDecodeError, ValueError) as e:
                        logging.warning(f"Konnte JSON aus {path} nicht lesen ({e}), verwende Default.")
                
                updated_data = update_func(data)
                
                f.seek(0)
                f.truncate()
                json.dump(updated_data, f, indent=2)
                # Flush ist wichtig bei portalocker
                f.flush()
                return updated_data
        except (portalocker.exceptions.LockException, portalocker.exceptions.AlreadyLocked):
            if i < retries - 1:
                logging.warning(f"Datei {path} für atomares Update gesperrt, Retry {i+1}/{retries}...")
                time.sleep(0.5)
                continue
            logging.error(f"Timeout beim Sperren (Update) von {path} nach {retries} Versuchen.")
            raise TransientError(f"Datei {path} konnte nicht atomar aktualisiert werden.")
        except Exception as e:
            logging.error(f"Fehler beim atomaren Update von {path}: {e}")
            raise PermanentError(f"Kritischer Fehler beim Update von {path}: {e}")

def register_with_hub(hub_url: str, agent_name: str, port: int, token: str, role: str = "worker") -> bool:
    """Registriert den Agenten beim Hub."""
    # Bestimme die URL des Agenten: Priorität hat settings.agent_url, Fallback auf localhost
    agent_url = settings.agent_url or f"http://localhost:{port}"
    
    payload = {
        "name": agent_name,
        "url": agent_url,
        "role": role,
        "token": token
    }
    try:
        response = _http_post(f"{hub_url}/register", payload)
        logging.info(f"Erfolgreich am Hub ({hub_url}) registriert.")
        
        # Token-Persistierung falls vom Hub zurückgegeben
        if isinstance(response, dict) and "agent_token" in response:
            new_token = response["agent_token"]
            if new_token and new_token != token:
                token_path = os.path.join(settings.data_dir, "token.json")
                write_json(token_path, {"agent_token": new_token}, chmod=0o600)
                logging.info(f"Neuer Agent Token vom Hub empfangen und persistiert: {token_path}")
        
        return True
    except Exception as e:
        logging.warning(f"Hub-Registrierung fehlgeschlagen: {e}")
        return False

def _get_approved_command(hub_url: str, cmd: str, prompt: str) -> Optional[str]:
    """Sendet Befehl zur Genehmigung. Gibt finalen Befehl oder None (SKIP) zurück."""
    approval = _http_post(
        f"{hub_url}/approve",
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
    """(Platzhalter) Loggt eine Nachricht in die DB via Hub."""
    # In dieser Version deaktiviert oder via _http_post an den Hub
    pass

def _log_terminal_entry(agent_name: str, step: int, direction: str, **kwargs: Any) -> None:
    """Schreibt einen Eintrag ins Terminal-Log (JSONL)."""
    data_dir = get_data_dir()
    log_file = os.path.join(data_dir, "terminal_log.jsonl")
    entry = {
        "timestamp": time.time(),
        "agent": agent_name,
        "step": step,
        "direction": direction,
        **kwargs
    }
    try:
        os.makedirs(data_dir, exist_ok=True)
        with portalocker.Lock(log_file, mode="a", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX) as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logging.error(f"Fehler beim Schreiben ins Terminal-Log: {e}")
