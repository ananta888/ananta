"""
AI-Agent: Terminal-Control-Modus
================================
Ein autonomer Agent, der ein Terminal via LLM-generierte Shell-Befehle steuert.

Ablauf:
1. Config vom Controller holen (GET /next-config)
2. Prompt an LLM senden → Befehlsvorschlag erhalten
3. Befehl vom Controller genehmigen lassen (POST /approve)
4. Befehl im Terminal ausführen
5. Ergebnis loggen (DB + Datei)

Hinweis: Es existiert nur noch dieser eine Betriebsmodus. Es gibt keinen separaten
Task‑Polling‑Modus mehr.
"""

# Versuch, paket-relative Importe zu nutzen; falls das Modul direkt als Skript ausgeführt wird,
# auf absoluten Import zurückfallen. Wenn health.py nicht existiert, stiller Fallback.
# python
# Robust import: relative wenn als Paket ausgeführt, sonst absolute als Fallback
try:
    from .health import health_bp
    from .shell import PersistentShell
except ImportError:
    # Wenn das Skript direkt ausgeführt wird (z. B. python agent/ai_agent.py),
    # schlagen relative Importe fehl. Versuche dann die absolute Form.
    from agent.health import health_bp
    from agent.shell import PersistentShell

import time
from flask import Flask, jsonify, request, g, Response
from functools import wraps
from pydantic import ValidationError
from collections import defaultdict
try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
except ImportError:
    # Minimaler Mock falls nicht installiert
    class MockMetric:
        def inc(self, *args, **kwargs): pass
        def labels(self, *args, **kwargs): return self
        def observe(self, *args, **kwargs): pass
        def time(self): return self
        def __enter__(self): return self
        def __exit__(self, *args): pass
    Counter = Histogram = lambda *a, **kw: MockMetric()
    generate_latest = lambda: b""
    CONTENT_TYPE_LATEST = "text/plain"

# Metriken
TASK_RECEIVED = Counter("task_received_total", "Total tasks received")
TASK_COMPLETED = Counter("task_completed_total", "Total tasks completed")
TASK_FAILED = Counter("task_failed_total", "Total tasks failed")
LLM_CALL_DURATION = Histogram("llm_call_duration_seconds", "Duration of LLM calls")
HTTP_REQUEST_DURATION = Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "target"])
RETRIES_TOTAL = Counter("retries_total", "Total number of retries")

# In-Memory Storage für einfaches Rate-Limiting
_rate_limit_storage = defaultdict(list)

def rate_limit(limit: int, window: int):
    """Einfacher Decorator für Rate-Limiting (In-Memory)."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
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
try:
    # optional CORS for direct Angular ↔ Agent communication
    from flask_cors import CORS
except Exception:  # pragma: no cover
    CORS = None  # type: ignore
import threading

import os
import json
import signal
import subprocess
from typing import Any, Optional

# Entfernt: Abhängigkeiten auf externes Settings-Modul/DB. Alles wird über ENV/Defaults konfiguriert.


# =============================================================================
# Konfiguration
# =============================================================================

# Versuche die neue zentrale Konfiguration zu laden
try:
    from src.config.settings import settings
    from src.common.logging import setup_logging, set_correlation_id, get_correlation_id
    from src.common.http import get_default_client
    from agent.models import (
        TaskStepProposeRequest, TaskStepExecuteRequest, AgentRegisterRequest
    )
    from src.common.errors import (
        AnantaError, TransientError, PermanentError, ValidationError as AnantaValidationError
    )
except ImportError:
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from src.config.settings import settings
    from src.common.logging import setup_logging, set_correlation_id, get_correlation_id
    from src.common.http import get_default_client
    from agent.models import (
        TaskStepProposeRequest, TaskStepExecuteRequest, AgentRegisterRequest
    )
    from src.common.errors import (
        AnantaError, TransientError, PermanentError, ValidationError as AnantaValidationError
    )
import uuid
import logging

# Initiales Logging-Setup
setup_logging(level=settings.log_level, json_format=settings.log_json)

DATA_DIR = os.path.abspath(os.path.join(os.getcwd(), "data"))
LOG_FILE = os.path.join(DATA_DIR, "terminal_log.jsonl")
SUMMARY_FILE = os.path.join(DATA_DIR, "summary.txt")
STOP_FLAG = os.path.join(DATA_DIR, "stop.flag")

# Timeouts aus Settings
COMMAND_TIMEOUT = settings.command_timeout
HTTP_TIMEOUT = settings.http_timeout

# Graceful Shutdown
_shutdown_requested = False
_rr_index = 0  # Round-Robin Index für Task-Verteilung

# Rolle des Agents (worker oder hub)
ROLE = settings.role.lower()

# Globale Shell-Instanz für langlebige Sessions
persistent_shell = PersistentShell()


def _handle_shutdown(signum, frame):
    """Signal-Handler für SIGTERM/SIGINT."""
    global _shutdown_requested
    _shutdown_requested = True
    print(f"Shutdown-Signal empfangen ({signum}), beende nach aktuellem Schritt...")
    try:
        persistent_shell.close()
    except Exception:
        pass


signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)


# =============================================================================
# Main Prompt
# =============================================================================

MAIN_PROMPT = """You are an autonomous AI agent that directly controls a terminal.
All interactions happen through shell commands you output.

Rules:
- Output ONLY a single shell command per step
- Assume a POSIX-like shell (bash/sh)
- Use non-interactive commands only
- Be careful with destructive operations (rm, mv, etc.)
- Explain your reasoning briefly before the command

Format your response as:
REASON: <brief explanation>
COMMAND: <shell command>
"""


# Initialer HTTP-Client
http_client = get_default_client(timeout=settings.http_timeout, retries=settings.retry_count)


# =============================================================================
# HTTP-Client Wrapper
# =============================================================================

def _http_get(url: str, params: dict | None = None, timeout: int = HTTP_TIMEOUT) -> Any:
    """HTTP GET via robustem Client."""
    target = url.split("/")[2] if "//" in url else "local"
    with HTTP_REQUEST_DURATION.labels(method="GET", target=target).time():
        return http_client.get(url, params=params, timeout=timeout)


def _http_post(
    url: str,
    data: dict | None = None,
    headers: dict | None = None,
    form: bool = False,
    timeout: int = HTTP_TIMEOUT
) -> Any:
    """HTTP POST via robustem Client."""
    target = url.split("/")[2] if "//" in url else "local"
    with HTTP_REQUEST_DURATION.labels(method="POST", target=target).time():
        return http_client.post(url, data=data, headers=headers, form=form, timeout=timeout)


def log_to_db(agent_name: str, level: str, message: str) -> None:
    """DB-Logging entfernt – Fallback: Logging-System."""
    try:
        lvl = getattr(logging, level.upper(), logging.INFO)
        logging.log(lvl, f"{agent_name}: {message}")
    except Exception:
        pass


def _log_terminal_entry(agent_name: str, step: int, direction: str, **kwargs) -> None:
    """Schreibt Terminal-Ein-/Ausgabe als JSONL-Datei (kein DB-Log mehr)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "ts": time.time(),
        "agent": agent_name,
        "step": step,
        "direction": direction,
        **kwargs,
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


# Polling-bezogene Logik (_add_log, In-Memory-Puffer) wurde entfernt.


def _extract_command(text: str) -> str:
    """Extrahiert den Befehl aus der LLM-Antwort (nach 'COMMAND:')."""
    if not text:
        return ""
    
    # Suche nach "COMMAND:" Pattern
    for line in text.split("\n"):
        line_stripped = line.strip()
        if line_stripped.upper().startswith("COMMAND:"):
            return line_stripped[8:].strip()
    
    # Fallback: Letzte nicht-leere Zeile
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    return lines[-1] if lines else ""


def _extract_reason(text: str) -> str:
    """Extrahiert die REASON-Zeile aus der LLM-Antwort."""
    if not text:
        return ""
    for line in text.split("\n"):
        ls = line.strip()
        if ls.upper().startswith("REASON:"):
            return ls[7:].strip()
    return ""


def _call_llm(provider: str, model: str, prompt: str, urls: dict, api_key: str | None, timeout: int = HTTP_TIMEOUT, history: list | None = None) -> str:
    """Ruft den konfigurierten LLM-Provider auf und gibt den rohen Text zurück."""
    
    with LLM_CALL_DURATION.time():
        # Historie in den Prompt einbauen (für Ollama/LMStudio)
        full_prompt = prompt
        if history and provider != "openai":
            history_str = "\n\nHistorie bisheriger Aktionen:\n"
            for h in history:
                history_str += f"- Prompt: {h.get('prompt')}\n"
                history_str += f"  Reasoning: {h.get('reason')}\n"
                history_str += f"  Befehl: {h.get('command')}\n"
                if "output" in h:
                    out = h.get('output', '')
                    if len(out) > 500: out = out[:500] + "..."
                    history_str += f"  Ergebnis: {out}\n"
            full_prompt = history_str + "\nAktueller Auftrag:\n" + prompt

        if provider == "ollama":
            resp = _http_post(urls["ollama"], {"model": model, "prompt": full_prompt}, timeout=timeout)
            if isinstance(resp, dict):
                return resp.get("response", "")
            return resp if isinstance(resp, str) else ""
        
        elif provider == "lmstudio":
            resp = _http_post(urls["lmstudio"], {"model": model, "prompt": full_prompt}, timeout=timeout)
            if isinstance(resp, dict):
                return resp.get("response", "")
            return resp if isinstance(resp, str) else ""
        
        elif provider == "openai":
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
            messages = []
            if history:
                for h in history:
                    messages.append({"role": "user", "content": h.get("prompt") or ""})
                    assistant_msg = f"REASON: {h.get('reason')}\nCOMMAND: {h.get('command')}"
                    messages.append({"role": "assistant", "content": assistant_msg})
                    if "output" in h:
                        messages.append({"role": "system", "content": f"Befehlsausgabe: {h.get('output')}"})
            
            messages.append({"role": "user", "content": prompt})
            
            resp = _http_post(
                urls["openai"],
                {
                    "model": model or "gpt-4o-mini",
                    "messages": messages,
                },
                headers=headers,
                timeout=timeout
            )
            if isinstance(resp, dict):
                try:
                    content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                    return content
                except (IndexError, AttributeError):
                    return ""
            return resp if isinstance(resp, str) else ""
        
        else:
            logging.error(f"Unbekannter Provider: {provider}")
            return ""


def _execute_command(cmd: str, timeout: int = COMMAND_TIMEOUT) -> tuple[str, int | None]:
    """Führt einen Shell-Befehl aus und gibt (output, returncode) zurück."""
    if not cmd or not cmd.strip():
        return "Leerer Befehl übersprungen", None
    
    return persistent_shell.execute(cmd, timeout=timeout)


def _get_approved_command(controller: str, cmd: str, prompt: str) -> str | None:
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


def _register_at_hub(port: int):
    """Registriert diesen Worker beim Hub."""
    if ROLE != "worker":
        return
    
    hub_url = settings.controller_url
    # Versuche eine sinnvolle Agent-URL zu bestimmen. 
    # In Docker ist 'localhost' oft falsch, aber hier nehmen wir es als Default.
    agent_url = f"http://localhost:{port}"
    payload = {
        "name": settings.agent_name,
        "url": agent_url,
        "role": "worker",
        "token": settings.agent_token
    }
    
    def run_register():
        # Warte kurz, bis der eigene Server hochgefahren ist
        time.sleep(2)
        try:
            logging.info(f"Registriere bei Hub: {hub_url} ...")
            res = _http_post(f"{hub_url}/register", data=payload, timeout=10)
            if res:
                logging.info("Registrierung am Hub erfolgreich.")
            else:
                raise Exception("Hub unreachable or returned error")
        except Exception as e:
            logging.warning(f"Registrierung am Hub fehlgeschlagen (Hub ggf. noch offline): {e}")

    threading.Thread(target=run_register, daemon=True).start()


def validate_request(model):
    """Decorator zur Validierung des Request-Body mit Pydantic."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
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


def create_app(agent: str = "default") -> Flask:
    """Erzeugt die Flask-App für den Agenten (API-Server)."""
    app = Flask(__name__)

    @app.before_request
    def ensure_correlation_id_and_check_shutdown():
        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        set_correlation_id(cid)
        
        # Lehne Anfragen ab, wenn Shutdown läuft (außer Health)
        if _shutdown_requested and request.endpoint not in ('health', 'get_logs', 'task_logs'):
            return jsonify({"status": "shutdown_in_progress"}), 503

    @app.errorhandler(Exception)
    def handle_exception(e):
        """Zentrale Fehlerbehandlung für alle Endpunkte."""
        cid = get_correlation_id()
        
        # Logge den Fehler strukturiert
        if isinstance(e, AnantaError):
            logging.warning(f"{e.__class__.__name__} [CID: {cid}]: {e}")
        else:
            logging.exception(f"Unbehandelte Exception [CID: {cid}]: {e}")

        # Fehlerklassifizierung für HTTP-Antwort
        if isinstance(e, AnantaValidationError):
            return jsonify({
                "error": "validation_failed",
                "details": e.details,
                "cid": cid
            }), 422
        
        if isinstance(e, PermanentError):
            return jsonify({
                "error": "permanent_error",
                "message": str(e),
                "cid": cid
            }), 400
            
        if isinstance(e, TransientError):
            return jsonify({
                "error": "transient_error",
                "message": str(e),
                "cid": cid
            }), 503

        # Fallback für Standard-Flask/Python Exceptions
        code = 500
        if hasattr(e, "code"):
            code = getattr(e, "code")
            
        return jsonify({
            "error": "internal_server_error",
            "message": str(e) if code != 500 else "Ein interner Fehler ist aufgetreten.",
            "cid": cid
        }), code

    # CORS erlauben (für direktes Angular-Frontend)
    if CORS is not None:
        try:
            CORS(app, resources={r"*": {"origins": "*"}})
        except Exception:
            pass

    # Settings aus zentraler Konfiguration
    agent_name = settings.agent_name if settings.agent_name != "default" else agent
    app.config.update({
        "AGENT_NAME": agent_name,
        "AGENT_TOKEN": settings.agent_token,
        "PROVIDER_URLS": {
            "ollama": settings.ollama_url,
            "lmstudio": settings.lmstudio_url,
            "openai": settings.openai_url,
        },
        "OPENAI_API_KEY": settings.openai_api_key,
        "CONFIG_PATH": os.path.join(DATA_DIR, "config.json"),
        "TEMPLATES_PATH": os.path.join(DATA_DIR, "templates.json"),
        "TASKS_PATH": os.path.join(DATA_DIR, "tasks.json"),
        "AGENTS_PATH": os.path.join(DATA_DIR, "agents.json"),
    })

    # Health-Endpunkt
    if 'health_bp' in globals() and health_bp:
        app.register_blueprint(health_bp)
    else:
        @app.get("/health")
        def health():  # type: ignore[unused-ignore]
            return jsonify({"status": "ok"})

    @app.get("/metrics")
    def metrics():
        return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

    # Lokale Agent-Konfiguration (in Datei persistiert, wenn vorhanden)
    default_cfg = {
        "provider": "ollama",
        "model": "llama3",
        "max_summary_length": 500,
        "main_prompt": MAIN_PROMPT,
    }
    cfg_path = app.config["CONFIG_PATH"]
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                saved = json.load(f)
                default_cfg.update({k: v for k, v in saved.items() if k in default_cfg})
        except Exception:
            pass
    app.config["AGENT_CONFIG"] = default_cfg

    # =========================================================================
    # Hub-Endpunkte (Auto-Discovery)
    # =========================================================================
    if ROLE == "hub":
        @app.post("/register")
        @rate_limit(limit=20, window=60)
        @validate_request(AgentRegisterRequest)
        def register_agent():
            data = g.validated_data.dict()
            name = data.get("name")
            if not name:
                return jsonify({"error": "name_required"}), 400
            
            agents = _read_json(app.config["AGENTS_PATH"], {})
            agents[name] = {
                "url": data.get("url"),
                "role": data.get("role", "worker"),
                "token": data.get("token"),
                "last_seen": time.time(),
                "status": "online"
            }
            _write_json(app.config["AGENTS_PATH"], agents)
            logging.info(f"Agent registriert: {name} ({data.get('url')})")
            return jsonify({"status": "registered"})

        @app.get("/agents")
        def list_agents():
            agents = _read_json(app.config["AGENTS_PATH"], {})
            return jsonify(agents)

        def _check_agents_health():
            # Kurze Pause damit alles hochfahren kann
            time.sleep(5)
            while not _shutdown_requested:
                agents = _read_json(app.config["AGENTS_PATH"], {})
                if not agents:
                    time.sleep(10)
                    continue
                
                changed = False
                for name, info in agents.items():
                    url = info.get("url")
                    if not url:
                        continue
                    try:
                        res = _http_get(f"{url}/health", timeout=5)
                        status = "online" if res is not None else "offline"
                    except Exception:
                        status = "offline"
                    
                    if info.get("status") != status:
                        info["status"] = status
                        if status == "online":
                            info["last_seen"] = time.time()
                        changed = True
                
                if changed:
                    _write_json(app.config["AGENTS_PATH"], agents)
                
                time.sleep(30)

        threading.Thread(target=_check_agents_health, daemon=True).start()

    # Hilfsfunktionen
    def _read_json(path: str, default):
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _write_json(path: str, data) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _auth_ok() -> bool:
        token = app.config.get("AGENT_TOKEN")
        if not token:
            return True
        hdr = request.headers.get("Authorization", "")
        return hdr == f"Bearer {token}"

    def _persist_config() -> None:
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(app.config["AGENT_CONFIG"], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # Idempotenz-Helper
    TASK_STATUS_FILE = os.path.join(DATA_DIR, "task_status.json")

    def _get_local_task_status(tid: str) -> Optional[dict]:
        statuses = _read_json(TASK_STATUS_FILE, {})
        return statuses.get(str(tid))

    def _update_local_task_status(tid: str, status: str, **kwargs):
        statuses = _read_json(TASK_STATUS_FILE, {})
        entry = statuses.get(str(tid), {})
        entry.update({"status": status, "updated_at": time.time(), **kwargs})
        statuses[str(tid)] = entry
        _write_json(TASK_STATUS_FILE, statuses)
        logging.info(f"Task {tid} status updated to {status}")

    # API: Konfiguration
    @app.get("/config")
    def get_config():
        return jsonify({
            "agent_name": app.config["AGENT_NAME"],
            **app.config["AGENT_CONFIG"],
        })

    @app.post("/config")
    def set_config():
        if not _auth_ok():
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        allowed = {"provider", "model", "max_summary_length", "main_prompt"}
        app.config["AGENT_CONFIG"].update({k: v for k, v in data.items() if k in allowed})
        _persist_config()
        return jsonify(app.config["AGENT_CONFIG"])

    # API: Propose (LLM-Aufruf, keine Ausführung)
    @app.post("/step/propose")
    @rate_limit(limit=10, window=60)
    @validate_request(TaskStepProposeRequest)
    def propose_step():
        data = g.validated_data.dict()
        cfg = app.config["AGENT_CONFIG"].copy()
        provider = data.get("provider") or cfg.get("provider", "ollama")
        model = data.get("model") or cfg.get("model", "")
        prompt = data.get("prompt") or cfg.get("main_prompt", MAIN_PROMPT)
        task_id = data.get("task_id")
        history = data.get("history") or []

        if task_id:
            _update_local_task_status(task_id, "PROPOSING", provider=provider, model=model)

        urls = app.config["PROVIDER_URLS"]
        api_key = app.config.get("OPENAI_API_KEY")
        
        raw = _call_llm(provider, model, prompt, urls, api_key, history=history)
        if not raw:
            if task_id:
                _update_local_task_status(task_id, "FAILED", error="Empty LLM response or timeout")
            return jsonify({"error": "LLM call failed or timed out"}), 502

        cmd = _extract_command(raw)
        reason = _extract_reason(raw)
        
        if task_id:
            _update_local_task_status(task_id, "PROPOSED", command=cmd, reason=reason)

        return jsonify({
            "reason": reason,
            "command": cmd,
            "raw": raw,
        })

    # API: Execute (führt Befehl aus)
    @app.post("/step/execute")
    @rate_limit(limit=15, window=60)
    @validate_request(TaskStepExecuteRequest)
    def execute_step():
        if not _auth_ok():
            return jsonify({"error": "unauthorized"}), 401
        data = g.validated_data.dict()
        cmd = (data.get("command") or "").strip()
        timeout = int(data.get("timeout") or COMMAND_TIMEOUT)
        task_id = data.get("task_id")
        
        if not cmd:
            return jsonify({"error": "command required"}), 400

        # Idempotenz-Check
        if task_id:
            local_status = _get_local_task_status(task_id)
            if local_status and local_status.get("status") in ("DONE", "IN_PROGRESS"):
                logging.warning(f"Task {task_id} is already {local_status.get('status')}. Skipping execution.")
                return jsonify({
                    "error": f"task already {local_status.get('status')}",
                    "task_id": task_id,
                    "status": local_status.get("status")
                }), 409
            
            _update_local_task_status(task_id, "IN_PROGRESS", command=cmd)

        started = time.time()
        output, rc = _execute_command(cmd, timeout=timeout)
        finished = time.time()

        if rc == 0:
            TASK_COMPLETED.inc()
        else:
            TASK_FAILED.inc()

        if task_id:
            final_status = "DONE" if rc == 0 else "FAILED"
            _update_local_task_status(task_id, final_status, returncode=rc, output=output)

        # JSONL-Log schreiben
        try:
            entry = {
                "ts": finished,
                "agent": app.config["AGENT_NAME"],
                "task_id": task_id,
                "direction": "exec",
                "command": cmd,
                "returncode": rc,
                "stdout": output or "",
                "started_at": started,
                "finished_at": finished,
            }
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return jsonify({
            "exit_code": rc,
            "stdout": output or "",
            "stderr": "" if rc == 0 else "",
            "started_at": started,
            "finished_at": finished,
        })

    # API: Logs lesen (optional nach task_id filtern)
    @app.get("/logs")
    def get_logs():
        limit = int(request.args.get("limit", 200))
        task_filter = request.args.get("task_id")
        if not os.path.exists(LOG_FILE):
            return jsonify([])
        out: list[dict] = []
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if task_filter and str(obj.get("task_id")) != str(task_filter):
                        continue
                    out.append(obj)
        except Exception:
            out = []
        return jsonify(out[-limit:])

    # Optionale Hub-spezifische Endpunkte (Tasks/Templates)
    if ROLE == "hub":
        @app.get("/templates")
        def list_templates():
            data = _read_json(app.config["TEMPLATES_PATH"], [])
            return jsonify(data)

        @app.post("/templates")
        def create_template():
            if not _auth_ok():
                return jsonify({"error": "unauthorized"}), 401
            body = request.get_json(silent=True) or {}
            items = _read_json(app.config["TEMPLATES_PATH"], [])
            import uuid
            tpl = {
                "id": body.get("id") or str(uuid.uuid4()),
                "name": body.get("name") or "template",
                "description": body.get("description") or "",
                "prompt_template": body.get("prompt_template") or "",
                "provider": body.get("provider") or app.config["AGENT_CONFIG"].get("provider"),
                "model": body.get("model") or app.config["AGENT_CONFIG"].get("model"),
                "defaults": body.get("defaults") or {},
            }
            items.append(tpl)
            _write_json(app.config["TEMPLATES_PATH"], items)
            return jsonify(tpl), 201

        @app.put("/templates/<tpl_id>")
        def update_template(tpl_id: str):
            if not _auth_ok():
                return jsonify({"error": "unauthorized"}), 401
            body = request.get_json(silent=True) or {}
            items = _read_json(app.config["TEMPLATES_PATH"], [])
            for t in items:
                if t.get("id") == tpl_id:
                    t.update({k: v for k, v in body.items() if k in {"name","description","prompt_template","provider","model","defaults"}})
                    _write_json(app.config["TEMPLATES_PATH"], items)
                    return jsonify(t)
            return jsonify({"error": "not_found"}), 404

        @app.delete("/templates/<tpl_id>")
        def delete_template(tpl_id: str):
            if not _auth_ok():
                return jsonify({"error": "unauthorized"}), 401
            items = _read_json(app.config["TEMPLATES_PATH"], [])
            new_items = [t for t in items if t.get("id") != tpl_id]
            _write_json(app.config["TEMPLATES_PATH"], new_items)
            return jsonify({"status": "deleted"})

        @app.get("/tasks")
        def list_tasks():
            tasks = _read_json(app.config["TASKS_PATH"], {})
            return jsonify(list(tasks.values()))

        @app.post("/tasks")
        def create_task():
            if not _auth_ok():
                return jsonify({"error": "unauthorized"}), 401
            TASK_RECEIVED.inc()
            body = request.get_json(silent=True) or {}
            tasks = _read_json(app.config["TASKS_PATH"], {})
            import uuid, time as _t
            tid = body.get("id") or ("T-" + str(uuid.uuid4())[:8])
            task = {
                "id": tid,
                "title": body.get("title") or "Task",
                "description": body.get("description") or "",
                "template_id": body.get("template_id"),
                "status": body.get("status") or "backlog",
                "tags": body.get("tags") or [],
                "created_at": _t.time(),
                "updated_at": _t.time(),
                "assignment": None,
                "last_proposed_command": None,
                "history": [],
            }
            tasks[tid] = task
            _write_json(app.config["TASKS_PATH"], tasks)
            return jsonify(task), 201

        @app.get("/tasks/<tid>")
        def get_task(tid: str):
            tasks = _read_json(app.config["TASKS_PATH"], {})
            t = tasks.get(tid)
            if not t:
                return jsonify({"error": "not_found"}), 404
            return jsonify(t)

        @app.patch("/tasks/<tid>")
        def patch_task(tid: str):
            if not _auth_ok():
                return jsonify({"error": "unauthorized"}), 401
            body = request.get_json(silent=True) or {}
            tasks = _read_json(app.config["TASKS_PATH"], {})
            t = tasks.get(tid)
            if not t:
                return jsonify({"error": "not_found"}), 404
            allowed = {"title","description","status","tags"}
            for k, v in body.items():
                if k in allowed:
                    t[k] = v
            t["updated_at"] = time.time()
            tasks[tid] = t
            _write_json(app.config["TASKS_PATH"], tasks)
            return jsonify(t)

        @app.post("/tasks/<tid>/assign")
        def assign_task(tid: str):
            if not _auth_ok():
                return jsonify({"error": "unauthorized"}), 401
            body = request.get_json(silent=True) or {}
            tasks = _read_json(app.config["TASKS_PATH"], {})
            t = tasks.get(tid)
            if not t:
                return jsonify({"error": "not_found"}), 404
            
            agent_url = body.get("agent_url")
            token = body.get("token")
            
            if not agent_url and settings.feature_load_balancing_enabled:
                # Automatisches Load Balancing (Round-Robin über online Worker)
                agents = _read_json(app.config["AGENTS_PATH"], {})
                online_workers = [
                    (name, info) for name, info in agents.items() 
                    if info.get("status") == "online" and info.get("role") == "worker"
                ]
                if not online_workers:
                    return jsonify({"error": "no_online_workers_available"}), 503
                
                global _rr_index
                # Sortiere nach Namen für deterministische Reihenfolge
                online_workers.sort(key=lambda x: x[0])
                selected_name, selected_info = online_workers[_rr_index % len(online_workers)]
                _rr_index += 1
                
                agent_url = selected_info.get("url")
                token = selected_info.get("token")
                logging.info(f"Task {tid} automatisch an Agent '{selected_name}' zugewiesen via Round-Robin.")

            t["assignment"] = {
                "agent_url": agent_url,
                "token": token,
            }
            t["updated_at"] = time.time()
            tasks[tid] = t
            _write_json(app.config["TASKS_PATH"], tasks)
            return jsonify(t)

        @app.post("/tasks/<tid>/propose")
        def task_propose(tid: str):
            tasks = _read_json(app.config["TASKS_PATH"], {})
            t = tasks.get(tid)
            if not t:
                return jsonify({"error": "not_found"}), 404
            body = request.get_json(silent=True) or {}
            assignment = (t or {}).get("assignment") or {}
            agent_url = assignment.get("agent_url")
            
            prompt = body.get("prompt") or (t.get("description") or app.config["AGENT_CONFIG"].get("main_prompt"))
            history = t.get("history", []) if settings.feature_history_enabled else []

            # Wenn ein Agent zugewiesen ist, dort /step/propose aufrufen, sonst lokal
            if agent_url:
                data = _http_post(f"{agent_url}/step/propose", data={
                    "prompt": prompt,
                    "provider": body.get("provider"),
                    "model": body.get("model"),
                    "history": history,
                    "task_id": tid,
                }, timeout=HTTP_TIMEOUT)
                if data is None:
                    return jsonify({"error": "agent_propose_failed"}), 502
            else:
                with app.test_request_context(json={
                    "prompt": prompt,
                    "provider": body.get("provider"),
                    "model": body.get("model"),
                    "history": history,
                    "task_id": tid,
                }):
                    data = propose_step().json  # type: ignore[attr-defined]
            
            # History-Eintrag erstellen oder letzten ergänzen
            if settings.feature_history_enabled:
                if "history" not in t: t["history"] = []
                t["history"].append({
                    "step": len(t["history"]) + 1,
                    "prompt": prompt,
                    "reason": (data or {}).get("reason"),
                    "command": (data or {}).get("command"),
                    "raw_response": (data or {}).get("raw"),
                    "ts_propose": time.time()
                })
            
            # Command in Task speichern
            t["last_proposed_command"] = (data or {}).get("command")
            t["updated_at"] = time.time()
            tasks[tid] = t
            _write_json(app.config["TASKS_PATH"], tasks)
            return jsonify(data)

        @app.post("/tasks/<tid>/execute")
        def task_execute(tid: str):
            if not _auth_ok():
                return jsonify({"error": "unauthorized"}), 401
            tasks = _read_json(app.config["TASKS_PATH"], {})
            t = tasks.get(tid)
            if not t:
                return jsonify({"error": "not_found"}), 404
            assignment = (t or {}).get("assignment") or {}
            body = request.get_json(silent=True) or {}
            cmd = body.get("command") or t.get("last_proposed_command")
            if not cmd:
                return jsonify({"error": "no_command"}), 400
            # Ausführung an zugewiesenen Agent oder lokal
            agent_url = assignment.get("agent_url")
            token = assignment.get("token")
            headers = {"Authorization": f"Bearer {token}"} if token else None
            payload = {"command": cmd, "timeout": body.get("timeout"), "task_id": tid}
            if agent_url:
                data = _http_post(f"{agent_url}/step/execute", data=payload, headers=headers, timeout=HTTP_TIMEOUT)
                if data is None:
                    return jsonify({"error": "agent_execute_failed"}), 502
            else:
                with app.test_request_context(json=payload, headers=headers or {}):
                    data = execute_step().json  # type: ignore[attr-defined]
            
            # Ergebnis in History speichern
            if settings.feature_history_enabled and "history" in t and t["history"]:
                last = t["history"][-1]
                if last.get("command") == cmd and "output" not in last:
                    last["output"] = (data or {}).get("stdout")
                    last["exit_code"] = (data or {}).get("exit_code")
                    last["ts_execute"] = time.time()

            # Task-Status ggf. setzen
            t["status"] = body.get("status") or t.get("status") or "in-progress"
            t["updated_at"] = time.time()
            tasks[tid] = t
            _write_json(app.config["TASKS_PATH"], tasks)
            return jsonify(data)

        @app.get("/tasks/<tid>/logs")
        def task_logs(tid: str):
            # Lokal aggregierte Logs nach task_id filtern
            if not os.path.exists(LOG_FILE):
                return jsonify([])
            out = []
            try:
                with open(LOG_FILE, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        if str(obj.get("task_id")) == str(tid):
                            out.append(obj)
            except Exception:
                out = []
            return jsonify(out[-500:])

        @app.get("/tasks/<tid>/stream")
        def stream_task_logs(tid: str):
            """SSE-Stream für Task-Logs."""
            def generate():
                if os.path.exists(LOG_FILE):
                    try:
                        with open(LOG_FILE, "r", encoding="utf-8") as f:
                            # Springe zum Ende der Datei für Live-Streaming
                            f.seek(0, os.SEEK_END)
                            while not _shutdown_requested:
                                line = f.readline()
                                if not line:
                                    time.sleep(0.5)
                                    continue
                                line_content = line.strip()
                                if not line_content: continue
                                try:
                                    obj = json.loads(line_content)
                                    if str(obj.get("task_id")) == str(tid):
                                        yield f"data: {line_content}\n\n"
                                except Exception:
                                    pass
                    except Exception as e:
                        yield f"data: {json.dumps({'error': str(e)})}\n\n"
                else:
                    yield f"data: {json.dumps({'message': 'Log-Datei noch nicht erstellt', 'task_id': tid})}\n\n"
            
            return Response(generate(), mimetype="text/event-stream")

    return app


def _run_health_app_in_background(port: int = 5000, agent_name: str = "default") -> None:
    """Deprecated: Der Agent läuft als API-Server; Health ist Teil davon."""
    app = create_app(agent_name)
    th = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False), daemon=True)
    th.start()


# =============================================================================
# Haupt-Agent-Loop
# =============================================================================

def run_agent(
    controller: str = "http://controller:8081",
    ollama: str = "http://localhost:11434/api/generate",
    lmstudio: str = "http://localhost:1234/v1/completions",
    openai: str = "https://api.openai.com/v1/chat/completions",
    openai_api_key: str | None = None,
    steps: int | None = None,
    step_delay: int = 0,
) -> None:
    """Deprecated: Der autonome Polling-Loop wurde durch API-Endpunkte ersetzt."""
    app = create_app()
    port = settings.port
    _register_at_hub(port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    # Starte den API-Server direkt
    app = create_app()
    port = settings.port
    _register_at_hub(port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)