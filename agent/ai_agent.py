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
from typing import Any

from src.config.settings import load_settings
# Entfernt: DB-Abhängigkeit. Der Agent arbeitet controller- und datenbankfrei.


# =============================================================================
# Konfiguration
# =============================================================================

DATA_DIR = os.path.abspath(os.path.join(os.getcwd(), "data"))
LOG_FILE = os.path.join(DATA_DIR, "terminal_log.jsonl")
SUMMARY_FILE = os.path.join(DATA_DIR, "summary.txt")
STOP_FLAG = os.path.join(DATA_DIR, "stop.flag")

# Timeouts
COMMAND_TIMEOUT = 60  # Sekunden für Shell-Befehlsausführung
HTTP_TIMEOUT = 30     # Sekunden für HTTP-Requests

# Graceful Shutdown
_shutdown_requested = False

# Keine In-Memory-Polling-/E2E-Logik mehr – nur Terminal-Control bleibt bestehen.
# Optional: Hub-Rolle zur Task-/Template-Orchestrierung (gleicher Code, andere Rolle)
ROLE = os.environ.get("ROLE", "worker").lower()


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


# =============================================================================
# HTTP-Client mit Retry
# =============================================================================

def _http_get(url: str, params: dict | None = None, timeout: int = HTTP_TIMEOUT) -> Any:
    """HTTP GET mit Timeout und Fehlerbehandlung."""
    try:
        settings = load_settings()
        r = requests.get(
            url,
            params=params,
            timeout=timeout or settings.http_timeout_get
        )
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return r.text
    except requests.exceptions.Timeout:
        print(f"HTTP GET Timeout: {url}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"HTTP GET Fehler: {url} - {e}")
        return None


def _http_post(
    url: str,
    data: dict | None = None,
    headers: dict | None = None,
    form: bool = False,
    timeout: int = HTTP_TIMEOUT
) -> Any:
    """HTTP POST mit Timeout und Fehlerbehandlung."""
    try:
        settings = load_settings()
        if form:
            r = requests.post(
                url,
                data=data or {},
                headers=headers,
                timeout=timeout or settings.http_timeout_post
            )
        else:
            r = requests.post(
                url,
                json=data or {},
                headers=headers,
                timeout=timeout or settings.http_timeout_post
            )
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return r.text
    except requests.exceptions.Timeout:
        print(f"HTTP POST Timeout: {url}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"HTTP POST Fehler: {url} - {e}")
        return None


def log_to_db(agent_name: str, level: str, message: str) -> None:
    """DB-Logging entfernt – Fallback: Konsole."""
    try:
        print(f"[{level}] {agent_name}: {message}")
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


def _call_llm(provider: str, model: str, prompt: str, urls: dict, api_key: str | None) -> str:
    """Ruft den konfigurierten LLM-Provider auf und gibt den Befehlsvorschlag zurück."""
    
    if provider == "ollama":
        resp = _http_post(urls["ollama"], {"model": model, "prompt": prompt})
        if isinstance(resp, dict):
            return _extract_command(resp.get("response", ""))
        return _extract_command(resp) if isinstance(resp, str) else ""
    
    elif provider == "lmstudio":
        resp = _http_post(urls["lmstudio"], {"model": model, "prompt": prompt})
        if isinstance(resp, dict):
            return _extract_command(resp.get("response", ""))
        return _extract_command(resp) if isinstance(resp, str) else ""
    
    elif provider == "openai":
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        resp = _http_post(
            urls["openai"],
            {
                "model": model or "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
            },
            headers=headers,
        )
        if isinstance(resp, dict):
            try:
                content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                return _extract_command(content)
            except (IndexError, AttributeError):
                return ""
        return _extract_command(resp) if isinstance(resp, str) else ""
    
    else:
        print(f"Unbekannter Provider: {provider}")
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

    # CORS erlauben (für direktes Angular-Frontend)
    if CORS is not None:
        try:
            CORS(app, resources={r"*": {"origins": "*"}})
        except Exception:
            pass

    # Settings laden (Defaults für Provider-URLs, Agent-Name etc.)
    settings = load_settings()
    agent_name = getattr(settings, "agent_name", None) or agent
    app.config.update({
        "AGENT_NAME": agent_name,
        "AGENT_TOKEN": os.environ.get("AGENT_TOKEN"),
        "PROVIDER_URLS": {
            "ollama": getattr(settings, "ollama_url", "http://localhost:11434/api/generate"),
            "lmstudio": getattr(settings, "lmstudio_url", "http://localhost:1234/v1/completions"),
            "openai": getattr(settings, "openai_url", "https://api.openai.com/v1/chat/completions"),
        },
        "OPENAI_API_KEY": getattr(settings, "openai_api_key", None),
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

        urls = app.config["PROVIDER_URLS"]
        api_key = app.config.get("OPENAI_API_KEY")
        raw = _call_llm(provider, model, prompt, urls, api_key)
        cmd = _extract_command(raw or "")
        reason = _extract_reason(raw or "")
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
        started = time.time()
        output, rc = _execute_command(cmd, timeout=timeout)
        finished = time.time()
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
    port = int(os.environ.get("PORT", getattr(load_settings(), "port", 5000) or 5000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    # Starte den API-Server direkt
    app = create_app()
    port = int(os.environ.get("PORT", getattr(load_settings(), "port", 5000) or 5000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)