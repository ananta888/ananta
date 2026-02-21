import json
import logging
import os
import re
import time
from collections import defaultdict
from functools import wraps
from typing import Any, Callable, List, Optional, Type

import portalocker
import portalocker.exceptions
from flask import current_app, g, request
from pydantic import BaseModel, ValidationError

from agent.common.errors import PermanentError, TransientError, api_response
from agent.common.errors import ValidationError as AnantaValidationError
from agent.common.http import get_default_client
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
    """Versucht die IP des Host-Gateways (WSL2/Docker) zu finden."""
    try:
        import subprocess

        # Unter Linux/Docker/WSL2 ist der Host oft das default gateway.
        ip_cmd = "/sbin/ip"
        if not os.path.exists(ip_cmd):
            ip_cmd = "/usr/sbin/ip"
        if not os.path.exists(ip_cmd):
            ip_cmd = "ip"
        output = subprocess.check_output(  # noqa: S603 - diagnostic read-only network query
            [ip_cmd, "route", "show", "default"], stderr=subprocess.DEVNULL
        )  # noqa: S607 - prefers absolute ip path, falls back when unavailable
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="ignore")

        for line in str(output).splitlines():
            line = line.strip()
            if not line:
                continue

            # Fallback: einige Aufrufer/Tests liefern nur die nackte Gateway-IP.
            if " " not in line and "." in line:
                return line

            parts = line.split()
            if "via" in parts:
                via_index = parts.index("via")
                if via_index + 1 < len(parts):
                    gateway = parts[via_index + 1].strip()
                    if gateway and "." in gateway:
                        return gateway
    except Exception:
        pass
    return None


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
        with portalocker.Lock(
            log_file, mode="r+", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX | portalocker.LOCK_NB
        ) as f:
            lines = f.readlines()
            for line in lines:
                if not line.strip():
                    continue
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
                with portalocker.Lock(
                    archive_file,
                    mode="a",
                    encoding="utf-8",
                    timeout=5,
                    flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
                ) as archive_locked:
                    for line in archived_entries:
                        archive_locked.write(line)

                f.seek(0)
                f.truncate()
                for line in remaining_entries:
                    f.write(line)
    except Exception as e:
        logging.error(f"Fehler bei der Archivierung des Terminal-Logs: {e}")


def _cleanup_old_backups():
    """Löscht alte Datenbank-Backups basierend auf backups_retention_days."""
    try:
        backup_dir = os.path.join(get_data_dir(), "backups")
        if not os.path.exists(backup_dir):
            return

        retention_days = settings.backups_retention_days
        cutoff = time.time() - (retention_days * 86400)

        removed_count = 0
        for filename in os.listdir(backup_dir):
            file_path = os.path.join(backup_dir, filename)
            if os.path.isfile(file_path):
                file_mtime = os.path.getmtime(file_path)
                if file_mtime < cutoff:
                    try:
                        os.remove(file_path)
                        removed_count += 1
                    except Exception as e:
                        logging.error(f"Fehler beim Löschen der Backup-Datei {file_path}: {e}")

        if removed_count > 0:
            logging.info(f"Cleanup: {removed_count} alte Backups aus {backup_dir} entfernt.")
    except Exception as e:
        logging.error(f"Fehler beim Cleanup der Backups: {e}")


def _archive_old_tasks(tasks_path=None):
    """Archiviert alte Tasks basierend auf dem Alter (Datenbank oder JSON) und löscht sehr alte Archive."""
    from agent.db_models import ArchivedTaskDB
    from agent.repository import archived_task_repo, task_repo

    # 1. Aktive Tasks archivieren
    retention_days = settings.tasks_retention_days
    now = time.time()
    cutoff_active = now - (retention_days * 86400)

    # 2. Archivierte Tasks bereinigen (Cleanup)
    cleanup_days = settings.archived_tasks_retention_days
    cutoff_archive = now - (cleanup_days * 86400)

    # Wenn kein Pfad angegeben ist, versuchen wir es zuerst mit der Datenbank
    if tasks_path is None:
        try:
            # Cleanup alter Archiv-Einträge
            archived_task_repo.delete_old(cutoff_archive)

            # Neue Archivierung
            old_tasks = task_repo.get_old_tasks(cutoff_active)
            if old_tasks:
                logging.info(f"Archiviere {len(old_tasks)} Tasks aus der Datenbank.")
                for t in old_tasks:
                    try:
                        # In ArchivedTaskDB verschieben
                        archived = ArchivedTaskDB(**t.model_dump())
                        archived_task_repo.save(archived)
                        # Aus aktiver Tabelle löschen
                        task_repo.delete(t.id)
                    except Exception as e:
                        logging.error(f"Fehler beim Archivieren von Task {t.id}: {e}")
            return
        except Exception as e:
            logging.warning(f"DB-Archivierung fehlgeschlagen, versuche JSON: {e}")
            try:
                tasks_path = current_app.config.get("TASKS_PATH", "data/tasks.json")
            except RuntimeError:
                tasks_path = os.path.join(settings.data_dir, "tasks.json")

    # JSON-Fallback Logik
    if not os.path.exists(tasks_path):
        return

    archive_path = tasks_path.replace(".json", "_archive.json")

    # JSON Cleanup
    def cleanup_archive_func(archived_tasks):
        if not isinstance(archived_tasks, dict):
            return archived_tasks
        remaining = {}
        removed_count = 0
        for tid, task in archived_tasks.items():
            archived_at = task.get("archived_at", task.get("created_at", now))
            if archived_at >= cutoff_archive:
                remaining[tid] = task
            else:
                removed_count += 1
        if removed_count > 0:
            logging.info(f"Cleanup: {removed_count} sehr alte archivierte Tasks aus JSON entfernt.")
        return remaining

    if os.path.exists(archive_path):
        update_json(archive_path, cleanup_archive_func, default={})

    # JSON Archivierung
    def update_func(tasks):
        if not isinstance(tasks, dict):
            return tasks
        to_archive = {}
        remaining = {}
        for tid, task in tasks.items():
            created_at = task.get("created_at", now)
            if created_at < cutoff_active:
                to_archive[tid] = task
            else:
                remaining[tid] = task

        if to_archive:
            logging.info(f"Archiviere {len(to_archive)} Tasks in {archive_path}")

            def update_archive(archive_data):
                if not isinstance(archive_data, dict):
                    archive_data = {}
                for tid, tdata in to_archive.items():
                    if "archived_at" not in tdata:
                        tdata["archived_at"] = now
                    archive_data[tid] = tdata
                return archive_data

            update_json(archive_path, update_archive, default={})
            return remaining
        return tasks

    update_json(tasks_path, update_func, default={})


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
    """Extrahiert den Shell-Befehl aus dem LLM-Output (JSON oder Markdown)."""
    text = text.strip()

    # Vorbereitung für unvollständiges JSON (versuche schließende Klammern zu ergänzen)
    def fix_json(s: str) -> str:
        s = s.strip()
        if s.startswith("{") and not s.endswith("}"):
            # Zähle öffnende und schließende Klammern
            open_braces = s.count("{")
            close_braces = s.count("}")
            if open_braces > close_braces:
                s += "}" * (open_braces - close_braces)
        return s

    # 1. Versuche JSON-Extraktion
    try:
        json_str = ""
        if "```json" in text:
            json_str = text.split("```json")[1].split("```")[0].strip()
        elif text.startswith("{"):
            # Versuche das Ende des JSON-Objekts zu finden, falls Text danach folgt
            last_brace = text.rfind("}")
            if last_brace != -1:
                json_str = text[: last_brace + 1]
            else:
                json_str = text

        if json_str:
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                # Zweiter Versuch mit Fix
                data = json.loads(fix_json(json_str))

            if isinstance(data, dict) and "command" in data:
                return str(data["command"]).strip()
    except Exception:
        pass

    # 2. Fallback auf Markdown Code-Blöcke
    # Suche gezielt nach bash/sh/shell Blöcken
    for lang in ["bash", "sh", "shell", "powershell", "ps1", "cmd"]:
        pattern = rf"```(?:{lang})\n(.*?)\n```"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()

    # Fallback auf generische Code-Blöcke
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            # Falls der Block mit einem Wort (Sprache) beginnt, überspringe die erste Zeile
            content = parts[1].strip()
            if content and "\n" in content and not content.startswith((" ", "\t")):
                lines = content.split("\n")
                if len(lines) > 1 and len(lines[0].split()) == 1:
                    return "\n".join(lines[1:]).strip()
            return content

    return text.strip()


def _extract_reason(text: str) -> str:
    """Extrahiert die Begründung (JSON 'reason' oder Text vor dem Code-Block)."""
    text = text.strip()

    # 1. Versuche JSON-Extraktion (ähnlich wie oben)
    try:
        json_str = ""
        if "```json" in text:
            json_str = text.split("```json")[1].split("```")[0].strip()
        elif text.startswith("{"):
            last_brace = text.rfind("}")
            if last_brace != -1:
                json_str = text[: last_brace + 1]
            else:
                json_str = text

        if json_str:
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                # Versuche Klammern zu fixen
                if json_str.startswith("{") and not json_str.endswith("}"):
                    json_str += "}" * (json_str.count("{") - json_str.count("}"))
                data = json.loads(json_str)

            if isinstance(data, dict):
                for key in ["reason", "thought", "explanation", "begründung"]:
                    if key in data:
                        return str(data[key]).strip()
    except Exception:
        pass

    # 2. Fallback: Alles vor dem ersten Code-Block oder der Rest des Textes
    if "```" in text:
        reason = text.split("```")[0].strip()
        if reason:
            return reason

    # Wenn kein Code-Block da ist, aber wir kein valides JSON hatten,
    # ist der Text vielleicht einfach nur die Begründung ohne Befehl
    if len(text) > 0:
        return text[:200] + "..." if len(text) > 200 else text

    return "Keine Begründung angegeben."


def _extract_tool_calls(text: str) -> Optional[List[dict]]:
    """Extrahiert tool_calls aus dem LLM-Output."""
    text = text.strip()
    try:
        json_str = ""
        if "```json" in text:
            json_str = text.split("```json")[1].split("```")[0].strip()
        elif text.startswith("{") or text.startswith("["):
            json_str = text

        if json_str:
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                if json_str.startswith("{") and not json_str.endswith("}"):
                    json_str += "}" * (json_str.count("{") - json_str.count("}"))
                data = json.loads(json_str)

            if isinstance(data, dict) and "tool_calls" in data:
                return data["tool_calls"]
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return None


def read_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default

    retries = 3
    for i in range(retries):
        try:
            with portalocker.Lock(
                path, mode="r", encoding="utf-8", timeout=2, flags=portalocker.LOCK_SH | portalocker.LOCK_NB
            ) as f:
                return json.load(f)
        except (portalocker.exceptions.LockException, portalocker.exceptions.AlreadyLocked):
            if i < retries - 1:
                logging.warning(f"Datei {path} gesperrt, Retry {i + 1}/{retries}...")
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

            with portalocker.Lock(
                path, mode="w", encoding="utf-8", timeout=2, flags=portalocker.LOCK_EX | portalocker.LOCK_NB
            ) as f:
                json.dump(data, f, indent=2)
                if chmod is not None:
                    try:
                        os.chmod(path, chmod)
                    except Exception:
                        pass
                return
        except (portalocker.exceptions.LockException, portalocker.exceptions.AlreadyLocked):
            if i < retries - 1:
                logging.warning(f"Datei {path} für Schreibzugriff gesperrt, Retry {i + 1}/{retries}...")
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
            with portalocker.Lock(
                path, mode="a+", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX | portalocker.LOCK_NB
            ) as f:
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
                logging.warning(f"Datei {path} für atomares Update gesperrt, Retry {i + 1}/{retries}...")
                time.sleep(0.5)
                continue
            logging.error(f"Timeout beim Sperren (Update) von {path} nach {retries} Versuchen.")
            raise TransientError(f"Datei {path} konnte nicht atomar aktualisiert werden.")
        except Exception as e:
            logging.error(f"Fehler beim atomaren Update von {path}: {e}")
            raise PermanentError(f"Kritischer Fehler beim Update von {path}: {e}")


def register_with_hub(
    hub_url: str, agent_name: str, port: int, token: str, role: str = "worker", silent: bool = False
) -> bool:
    """Registriert den Agenten beim Hub."""
    # Bestimme die URL des Agenten: Priorität hat settings.agent_url, Fallback auf localhost
    agent_url = settings.agent_url or f"http://localhost:{port}"

    payload = {"name": agent_name, "url": agent_url, "role": role, "token": token}
    try:
        response = _http_post(f"{hub_url}/register", payload, silent=silent)
        logging.info(f"Erfolgreich am Hub ({hub_url}) registriert.")

        # Token-Persistierung falls vom Hub zurückgegeben
        if isinstance(response, dict) and "agent_token" in response:
            new_token = response["agent_token"]
            if new_token and new_token != token:
                settings.save_agent_token(new_token)

        return True
    except Exception as e:
        if not silent:
            logging.warning(f"Hub-Registrierung fehlgeschlagen: {e}")
        return False


def _get_approved_command(hub_url: str, cmd: str, prompt: str) -> Optional[str]:
    """Sendet Befehl zur Genehmigung. Gibt finalen Befehl oder None (SKIP) zurück."""
    approval = _http_post(f"{hub_url}/approve", {"cmd": cmd, "summary": prompt}, form=True)

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
