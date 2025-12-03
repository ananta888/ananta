"""
AI-Agent: Terminal-Modus
========================
Ein autonomer Agent, der ein Terminal via LLM-generierte Shell-Befehle steuert.

Ablauf:
1. Config vom Controller holen (GET /next-config)
2. Prompt an LLM senden → Befehlsvorschlag erhalten
3. Befehl vom Controller genehmigen lassen (POST /approve)
4. Befehl im Terminal ausführen
5. Ergebnis loggen (DB + Datei)
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
from flask import Flask, jsonify, Response, request
from typing import Optional, List, Dict, Any

import os
import json
import signal
import subprocess
from typing import Any

from src.db import get_conn
from src.config.settings import load_settings


# =============================================================================
# Konfiguration
# =============================================================================

DATA_DIR = os.path.abspath(os.path.join(os.getcwd(), "data"))
LOG_FILE = os.path.join(DATA_DIR, "terminal_log.json")
SUMMARY_FILE = os.path.join(DATA_DIR, "summary.txt")
STOP_FLAG = os.path.join(DATA_DIR, "stop.flag")

# Timeouts
COMMAND_TIMEOUT = 60  # Sekunden für Shell-Befehlsausführung
HTTP_TIMEOUT = 30     # Sekunden für HTTP-Requests

# Graceful Shutdown
_shutdown_requested = False


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
    """Schreibt einen Log-Eintrag in die Datenbank (best-effort)."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO agent.logs (agent, level, message) VALUES (%s, %s, %s)",
                (agent_name, level, message),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f"DB-Log fehlgeschlagen: {e}")


def _log_terminal_entry(agent_name: str, step: int, direction: str, **kwargs) -> None:
    """Loggt einen Terminal-Ein-/Ausgabe-Eintrag in die DB."""
    entry = {"step": step, "direction": direction, **kwargs}
    try:
        log_to_db(agent_name, "TERMINAL", json.dumps(entry, ensure_ascii=False))
    except Exception:
        pass


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
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/agent/<name>/log")
    def agent_log_plain(name: str):
        # Return plain text logs for the given agent (used by E2E tests)
        logs = _MEM_LOGS.get(name, [])
        text = "\n".join(logs)
        return Response(text, mimetype='text/plain')

    @app.post("/stop")
    def stop():
        conn = get_conn()
        cur = conn.cursor()
        try:
            # Set the stop flag to "1" (create or update)
            cur.execute(
                """
                INSERT INTO agent.flags (name, value)
                VALUES ('stop', '1')
                ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value
                """
            )
            conn.commit()
            return jsonify({"status": "ok"})
        finally:
            cur.close()
            conn.close()

    @app.post("/restart")
    def restart():
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM agent.flags WHERE name='stop'")
            conn.commit()
            return jsonify({"status": "ok"})
        finally:
            cur.close()
            conn.close()

    @app.get("/logs")
    def logs():
        conn = get_conn()
        cur = conn.cursor()
        try:
            # Cast level to text to be robust against differing schema types
            cur.execute(
                "SELECT agent, (level)::text AS level, message FROM agent.logs WHERE agent=%s ORDER BY created_at ASC",
                (agent,),
            )
            rows = cur.fetchall()
            logs_list: List[Dict[str, Any]] = [
                {"agent": r[0], "level": r[1], "message": r[2]} for r in rows
            ]
            return jsonify({"agent": agent, "logs": logs_list})
        finally:
            cur.close()
            conn.close()

    @app.get("/tasks")
    def tasks():
        conn = get_conn()
        cur = conn.cursor()
        try:
            current_task: Optional[str] = None
            # Read latest controller config and extract current task for this agent
            cur.execute("SELECT data FROM controller.config ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row and row[0]:
                data = row[0]
                try:
                    current_task = (
                        data.get("agents", {})
                        .get(agent, {})
                        .get("current_task")
                    )
                except Exception:
                    current_task = None

            # Fetch tasks queue
            cur.execute("SELECT task, agent, template FROM controller.tasks ORDER BY id ASC")
            trows = cur.fetchall()
            tlist = [
                {"task": r[0], "agent": r[1], "template": r[2]} for r in trows
            ]
            return jsonify({"current_task": current_task, "tasks": tlist})
        finally:
            cur.close()
            conn.close()

    @app.get("/db/contents")
    def db_contents():
        """Return tables and rows from the agent schema for the Vue frontend.
        Query params:
          - table: optional, if provided only dump this table
          - limit: max rows per table (default 100)
          - offset: offset for rows (default 0)
          - include_empty: '1'|'true' to include empty tables
        """
        schema_default = "agent"
        schema = request.args.get("schema", schema_default) or schema_default
        # Enforce default schema for the agent service
        if schema not in ("agent",):
            return jsonify({"error": "invalid_schema", "allowed": ["agent"]}), 400
        table_filter = request.args.get("table")
        try:
            limit = max(0, min(1000, int(request.args.get("limit", "100"))))
        except Exception:
            limit = 100
        try:
            offset = max(0, int(request.args.get("offset", "0")))
        except Exception:
            offset = 0
        include_empty = str(request.args.get("include_empty", "0")).lower() in ("1", "true", "yes")

        try:
            conn = get_conn()
        except Exception as e:
            return jsonify({"error": "db_unavailable", "detail": str(e)}), 503

        cur = conn.cursor()
        try:
            # list tables in the schema
            params = [schema]
            tbl_sql = """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """
            cur.execute(tbl_sql, params)
            table_names = [r[0] for r in cur.fetchall()]
            if table_filter:
                table_names = [t for t in table_names if t == table_filter]

            from psycopg2 import sql as _sql  # imported lazily to avoid hard dep at import-time

            tables = []
            for tname in table_names:
                q = _sql.SQL("SELECT * FROM {}.{} LIMIT %s OFFSET %s").format(
                    _sql.Identifier(schema), _sql.Identifier(tname)
                )
                try:
                    cur.execute(q, (limit, offset))
                    rows = cur.fetchall()
                except Exception as e:
                    # Skip tables we cannot read for any reason
                    continue
                cols = [d.name if hasattr(d, 'name') else d[0] for d in cur.description or []]
                data_rows = [dict(zip(cols, row)) for row in rows]
                if data_rows or include_empty:
                    tables.append({
                        "name": tname,
                        "columns": cols,
                        "rows": data_rows,
                    })

            return jsonify({
                "schema": schema,
                "tables": tables,
                "limit": limit,
                "offset": offset,
            })
        finally:
            try:
                cur.close()
            finally:
                conn.close()

    return app


def main() -> None:
    """Continuously poll the controller for tasks and log them for E2E tests."""
    # Zentrale Settings laden (Defaults, env.json, ENV)
    settings = load_settings()
    controller_url = settings.controller_url
    agent_name = settings.agent_name
    print(f"Verbindung zum Controller unter: {controller_url} (Agent: {agent_name})")

    # Optionaler Startup-Delay, damit E2E-Tests den Task vor der Verarbeitung sehen
    startup_delay = settings.agent_startup_delay
    if startup_delay > 0:
        time.sleep(startup_delay)

    while True:
        try:
            # Pull next task for this agent (this also removes it from the queue server-side)
            resp = requests.get(f"{controller_url}/tasks/next", params={"agent": agent_name}, timeout=5)
            if resp.ok:
                data = resp.json()
                task = data.get("task")
                task_id = data.get("id")
                if task:
                    _add_log(agent_name, f"Received task: {task}")
                    # Persist into agent.logs (DB) for E2E verification
                    log_to_db(agent_name, "INFO", f"Received task: {task}")

                    # Simulate processing
                    _add_log(agent_name, f"Processed: {task}")
                    log_to_db(agent_name, "INFO", f"Processed: {task}")

                    # If enhanced mode is enabled on the controller, mark task as done
                    if task_id is not None:
                        try:
                            requests.post(
                                f"{controller_url}/tasks/{int(task_id)}/status",
                                json={
                                    "status": "done",
                                    "agent": agent_name,
                                    "message": f"Processed: {task}",
                                },
                                timeout=5,
                            )
                        except Exception as e:
                            print(f"Warn: failed to update task status: {e}")
            else:
                print(f"Warn: /tasks/next HTTP {resp.status_code}")
        except requests.exceptions.ConnectionError as e:
            print(f"Verbindungsfehler zum Controller: {e}")
        except Exception as e:
            print(f"Fehler bei der Kommunikation mit dem Controller: {e}")
        time.sleep(1)


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
    """
    Startet den Terminal-Control-Agenten.
    
    Args:
        controller: URL des Controllers für Config und Approval
        ollama: URL des Ollama-Endpunkts
        lmstudio: URL des LM Studio-Endpunkts
        openai: URL des OpenAI-Endpunkts
        openai_api_key: API-Key für OpenAI
        steps: Maximale Anzahl Schritte (None = unbegrenzt)
        step_delay: Pause zwischen Schritten in Sekunden
    """
    global _shutdown_requested
    
    # Vorbereitung
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("[")
    
    settings = load_settings()
    agent_name = settings.agent_name or "default"
    urls = {"ollama": ollama, "lmstudio": lmstudio, "openai": openai}
    
    print(f"Agent '{agent_name}' gestartet. Controller: {controller}")
    step = 0
    
    while not _shutdown_requested:
        # Schritt-Limit prüfen
        if steps is not None and step >= steps:
            print(f"Schritt-Limit ({steps}) erreicht.")
            break
        
        # Stop-Flag prüfen
        if os.path.exists(STOP_FLAG):
            print("Stop-Flag erkannt, beende...")
            break
        
        # 1. Config vom Controller holen
        cfg = _http_get(f"{controller}/next-config", params={"agent": agent_name}) or {}
        
        model = cfg.get("model", "") if isinstance(cfg, dict) else ""
        provider = cfg.get("provider", "ollama") if isinstance(cfg, dict) else "ollama"
        max_len = cfg.get("max_summary_length", 500) if isinstance(cfg, dict) else 500
        prompt = cfg.get("prompt") if isinstance(cfg, dict) else None
        prompt = prompt or MAIN_PROMPT
        
        # Prompt speichern
        try:
            with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
                f.write(prompt)
        except Exception:
            pass
        
        # 2. LLM nach Befehl fragen
        cmd = _call_llm(provider, model, prompt, urls, openai_api_key)
        
        if not cmd:
            print(f"Schritt {step}: Kein Befehl vom LLM erhalten, überspringe...")
            step += 1
            time.sleep(step_delay)
            continue
        
        # 3. Genehmigung einholen
        cmd_final = _get_approved_command(controller, cmd, prompt)
        
        if cmd_final is None:
            print(f"Schritt {step}: Befehl übersprungen (SKIP)")
            step += 1
            time.sleep(step_delay)
            continue
        
        # 4. Log: Input
        _log_terminal_entry(agent_name, step, "input", command=cmd_final)
        print(f"Schritt {step}: Führe aus: {cmd_final[:80]}...")
        
        # 5. Befehl ausführen
        output, rc = _execute_command(cmd_final)
        
        # 6. Log: Output (DB)
        _log_terminal_entry(
            agent_name, step, "output",
            returncode=rc,
            output=output[:max_len] if output else ""
        )
        
        # 7. Log: Datei
        entry = {
            "step": step,
            "command": cmd_final,
            "output": output[:max_len] if output else "",
            "returncode": rc
        }
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                if step > 0:
                    f.write(",")
                json.dump(entry, f, ensure_ascii=False)
        except Exception:
            pass
        
        step += 1
        if step_delay:
            time.sleep(step_delay)
    
    # JSON-Array schließen
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("]")
    except Exception:
        pass
    
    print(f"Agent beendet nach {step} Schritten.")


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    settings = load_settings()
    
    run_agent(
        controller=settings.controller_url,
        ollama=settings.ollama_url,
        lmstudio=settings.lmstudio_url,
        openai=settings.openai_url,
        openai_api_key=settings.openai_api_key,
        steps=int(os.environ.get("AGENT_STEPS", "0")) or None,
        step_delay=int(os.environ.get("AGENT_STEP_DELAY", "0")),
    )