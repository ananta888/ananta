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
except ImportError:
    # Wenn das Skript direkt ausgeführt wird (z. B. python agent/ai_agent.py),
    # schlagen relative Importe fehl. Versuche dann die absolute Form.
    from agent.health import health_bp

import time
import requests
from flask import Flask, jsonify, request
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
    from src.common.logging import setup_logging, set_correlation_id
    from src.common.http import get_default_client
except ImportError:
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from src.config.settings import settings
    from src.common.logging import setup_logging, set_correlation_id
    from src.common.http import get_default_client
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

# Rolle des Agents (worker oder hub)
ROLE = settings.role.lower()


def _handle_shutdown(signum, frame):
    """Signal-Handler für SIGTERM/SIGINT."""
    global _shutdown_requested
    _shutdown_requested = True
    print(f"Shutdown-Signal empfangen ({signum}), beende nach aktuellem Schritt...")


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
    return http_client.get(url, params=params, timeout=timeout)


def _http_post(
    url: str,
    data: dict | None = None,
    headers: dict | None = None,
    form: bool = False,
    timeout: int = HTTP_TIMEOUT
) -> Any:
    """HTTP POST via robustem Client."""
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


def _call_llm(provider: str, model: str, prompt: str, urls: dict, api_key: str | None, timeout: int = HTTP_TIMEOUT) -> str:
    """Ruft den konfigurierten LLM-Provider auf und gibt den rohen Text zurück."""
    
    if provider == "ollama":
        resp = _http_post(urls["ollama"], {"model": model, "prompt": prompt}, timeout=timeout)
        if isinstance(resp, dict):
            return resp.get("response", "")
        return resp if isinstance(resp, str) else ""
    
    elif provider == "lmstudio":
        resp = _http_post(urls["lmstudio"], {"model": model, "prompt": prompt}, timeout=timeout)
        if isinstance(resp, dict):
            return resp.get("response", "")
        return resp if isinstance(resp, str) else ""
    
    elif provider == "openai":
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        resp = _http_post(
            urls["openai"],
            {
                "model": model or "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
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
    
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return output, proc.returncode
    except subprocess.TimeoutExpired:
        return f"Timeout nach {timeout}s", -1
    except Exception as e:
        return f"Ausführungsfehler: {e}", -1


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


def create_app(agent: str = "default") -> Flask:
    """Erzeugt die Flask-App für den Agenten (API-Server)."""
    app = Flask(__name__)

    @app.before_request
    def ensure_correlation_id():
        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        set_correlation_id(cid)

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
    })

    # Health-Endpunkt
    if 'health_bp' in globals() and health_bp:
        app.register_blueprint(health_bp)
    else:
        @app.get("/health")
        def health():  # type: ignore[unused-ignore]
            return jsonify({"status": "ok"})

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
    def propose_step():
        data = request.get_json(silent=True) or {}
        cfg = app.config["AGENT_CONFIG"].copy()
        provider = data.get("provider") or cfg.get("provider", "ollama")
        model = data.get("model") or cfg.get("model", "")
        prompt = data.get("prompt") or cfg.get("main_prompt", MAIN_PROMPT)
        task_id = data.get("task_id")

        if task_id:
            _update_local_task_status(task_id, "PROPOSING", provider=provider, model=model)

        urls = app.config["PROVIDER_URLS"]
        api_key = app.config.get("OPENAI_API_KEY")
        
        raw = _call_llm(provider, model, prompt, urls, api_key)
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
    def execute_step():
        if not _auth_ok():
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(silent=True) or {}
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
            t["assignment"] = {
                "agent_url": body.get("agent_url"),
                "token": body.get("token"),
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
            # Wenn ein Agent zugewiesen ist, dort /step/propose aufrufen, sonst lokal
            if agent_url:
                try:
                    res = requests.post(f"{agent_url}/step/propose", json={
                        "prompt": body.get("prompt") or (t.get("description") or app.config["AGENT_CONFIG"].get("main_prompt")),
                        "provider": body.get("provider"),
                        "model": body.get("model"),
                    }, timeout=HTTP_TIMEOUT)
                    res.raise_for_status()
                    data = res.json()
                except Exception as e:
                    return jsonify({"error": f"agent_propose_failed: {e}"}), 502
            else:
                with app.test_request_context():
                    data = propose_step().json  # type: ignore[attr-defined]
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
                try:
                    res = requests.post(f"{agent_url}/step/execute", json=payload, headers=headers, timeout=HTTP_TIMEOUT)
                    res.raise_for_status()
                    data = res.json()
                except Exception as e:
                    return jsonify({"error": f"agent_execute_failed: {e}"}), 502
            else:
                with app.test_request_context(json=payload, headers=headers or {}):
                    data = execute_step().json  # type: ignore[attr-defined]
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
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    # Starte den API-Server direkt
    app = create_app()
    port = settings.port
    app.run(host="0.0.0.0", port=port, use_reloader=False)