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
import subprocess

from src.db import get_conn


def log_to_db(agent_name: str, level: str, message: str) -> None:
    """Best-effort DB logger that appends to agent.logs.
    Swallows exceptions to avoid impacting agent control flow.
    """
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
    except Exception as e:  # pragma: no cover - best effort
        print(f"Warn: failed to persist agent log: {e}")

# Ensure DB schemas/tables exist before agent starts handling requests
try:
    from src.db import init_db as _init_db
    import os as _os
    if _os.environ.get("SKIP_DB_INIT") != "1":
        _init_db()
except Exception as _e:
    print(f"Agent DB init skipped or failed at startup: {_e}")

# Simple in-memory log storage for E2E tests
_MEM_LOGS = {}

def _add_log(agent_name: str, message: str) -> None:
    logs = _MEM_LOGS.setdefault(agent_name, [])
    logs.append(message)

# Files/paths for terminal-control mode
DATA_DIR = os.path.abspath(os.path.join(os.getcwd(), "data"))
LOG_FILE = os.path.join(DATA_DIR, "terminal_log.json")
SUMMARY_FILE = os.path.join(DATA_DIR, "summary.txt")
STOP_FLAG = os.path.join(DATA_DIR, "stop.flag")

# Main prompt that explains terminal control mode
MAIN_PROMPT = (
    "You are an autonomous AI agent that directly controls a terminal. "
    "All interactions happen through shell commands you output. "
    "Only output a single shell command per step, suitable for execution in the current environment. "
    "Assume a POSIX-like shell in containers and PowerShell on Windows where applicable. "
    "Be careful: destructive operations must be explicit and justified. "
    "When you need to inspect files, use non-interactive commands."
)


def _http_get(url: str, params: dict | None = None, headers: dict | None = None):
    try:
        r = requests.get(url, params=params, headers=headers or None, timeout=10)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return r.text
    except Exception as e:
        print(f"http_get error: {e}")
        return None


def _http_post(url: str, data: dict | None = None, headers: dict | None = None, form: bool = False):
    try:
        if form:
            r = requests.post(url, data=(data or {}), headers=headers or None, timeout=15)
        else:
            r = requests.post(url, json=(data or {}), headers=headers or None, timeout=15)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return r.text
    except Exception as e:
        print(f"http_post error: {e}")
        return None


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
    import os
    # Controller-URL und Agent-Name aus Umgebungsvariablen lesen
    controller_url = os.environ.get("CONTROLLER_URL", "http://controller:8081")
    agent_name = os.environ.get("AGENT_NAME", "Architect")
    print(f"Verbindung zum Controller unter: {controller_url} (Agent: {agent_name})")

    # Optionaler Startup-Delay, damit E2E-Tests den Task vor der Verarbeitung sehen
    try:
        startup_delay = int(os.environ.get("AGENT_STARTUP_DELAY", "3"))
    except Exception:
        startup_delay = 3
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


def run_agent(
    controller: str = "http://controller:8081",
    ollama: str = "http://localhost:11434/api/generate",
    lmstudio: str = "http://localhost:1234/v1/completions",
    openai: str = "https://api.openai.com/v1/chat/completions",
    openai_api_key: str | None = None,
    steps: int | None = None,
    step_delay: int = 0,
) -> None:
    """Replicate a shell-based AI agent loop which controls a terminal.

    The agent builds a prompt, requests a command from the selected provider, asks
    the controller to approve it, executes the command, and logs the outcome as
    JSON lines array to data/terminal_log.json. A stop flag in data/stop.flag can
    be used to end the loop externally.
    """

    # Prepare files
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("[")
    step = 0

    # Identify this agent instance for per-agent controller config
    agent_name = os.environ.get("AGENT_NAME", "default")

    while steps is None or step < steps:
        # external stop
        if os.path.exists(STOP_FLAG):
            break

        # Pull a simple config snapshot (best-effort)
        cfg = _http_get(f"{controller}/next-config", params={"agent": agent_name}) or {}
        # Extract optional hints
        model = (cfg.get("model") if isinstance(cfg, dict) else None) or ""
        provider = (cfg.get("provider") if isinstance(cfg, dict) else None) or "ollama"
        max_len = (cfg.get("max_summary_length") if isinstance(cfg, dict) else None) or 300

        # Build prompt from config or previous log output
        prompt = (cfg.get("prompt") if isinstance(cfg, dict) else None) or MAIN_PROMPT
        try:
            with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
                f.write(prompt)
        except Exception:
            pass

        # Get a command proposal from the selected provider
        cmd = ""
        if provider == "ollama":
            resp = _http_post(ollama, {"model": model, "prompt": prompt})
            if isinstance(resp, dict):
                cmd = resp.get("response", "")
            elif isinstance(resp, str):
                cmd = resp
        elif provider == "lmstudio":
            resp = _http_post(lmstudio, {"model": model, "prompt": prompt})
            if isinstance(resp, dict):
                cmd = resp.get("response", "")
            elif isinstance(resp, str):
                cmd = resp
        elif provider == "openai":
            headers = {"Authorization": f"Bearer {openai_api_key}"} if openai_api_key else None
            resp = _http_post(
                openai,
                {
                    "model": model or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                },
                headers=headers,
            )
            if isinstance(resp, dict):
                try:
                    cmd = (
                        (resp.get("choices", [{}])[0] or {})
                        .get("message", {})
                        .get("content", "")
                    )
                except Exception:
                    cmd = ""
            elif isinstance(resp, str):
                cmd = resp
        else:
            cmd = ""

        # Ask controller to approve; some controllers may return an override command
        approval = _http_post(
            f"{controller}/approve", {"cmd": cmd, "summary": prompt}, form=True
        )
        # Determine final command
        cmd_final = cmd
        if isinstance(approval, str):
            if approval.strip().upper() == "SKIP":
                step += 1
                time.sleep(step_delay)
                continue
            # if a plain string is returned and it's not status, treat as command
            if approval and approval.strip() not in ("{\"status\": \"approved\"}", "approved"):
                cmd_final = approval
        elif isinstance(approval, dict):
            # prefer explicit override field if present
            cmd_override = approval.get("cmd") if isinstance(approval, dict) else None
            if isinstance(cmd_override, str) and cmd_override.strip():
                cmd_final = cmd_override

        # Persist proposed/approved command to DB as terminal input
        try:
            log_to_db(
                agent_name,
                "TERMINAL",
                json.dumps({
                    "step": step,
                    "direction": "input",
                    "command": cmd_final,
                }, ensure_ascii=False),
            )
        except Exception:
            pass

        # Execute the command non-interactively
        rc = None
        try:
            proc = subprocess.run(cmd_final, shell=True, capture_output=True, text=True)
            output = (proc.stdout or "") + (proc.stderr or "")
            rc = getattr(proc, "returncode", None)
        except Exception as e:
            output = f"Execution error: {e}"

        # Append to JSON array log (file)
        entry = {"step": step, "command": cmd_final, "output": output[: max_len], "returncode": rc}
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                if step:
                    f.write(",")
                json.dump(entry, f, ensure_ascii=False)
        except Exception:
            pass

        # Persist command output to DB as terminal output
        try:
            log_to_db(
                agent_name,
                "TERMINAL",
                json.dumps({
                    "step": step,
                    "direction": "output",
                    "returncode": rc,
                    "output": (output[: max_len] if isinstance(output, str) else str(output)),
                }, ensure_ascii=False),
            )
        except Exception:
            pass

        step += 1
        if step_delay:
            time.sleep(step_delay)

    # close JSON array
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("]")
    except Exception:
        pass


if __name__ == "__main__":
    import threading
    import os

    # Flask-App starten (als Thread, damit der Polling-Prozess parallel laufen kann)
    app = create_app()
    port = int(os.environ.get("PORT", 5000))

    def run_app():
        app.run(host="0.0.0.0", port=port)

    app_thread = threading.Thread(target=run_app)
    app_thread.daemon = True
    app_thread.start()

    print(f"AI Agent läuft auf Port {port}")

    # Choose operating mode: existing polling or terminal-control agent
    mode = str(os.environ.get("AGENT_MODE", "poll")).lower()
    if mode in ("terminal", "shell"):
        controller_url = os.environ.get("CONTROLLER_URL", "http://controller:8081")
        ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
        lmstudio_url = os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1/completions")
        openai_url = os.environ.get("OPENAI_URL", "https://api.openai.com/v1/chat/completions")
        openai_key = os.environ.get("OPENAI_API_KEY")
        try:
            steps = int(os.environ.get("AGENT_STEPS", "0"))
        except Exception:
            steps = 0
        try:
            step_delay = int(os.environ.get("AGENT_STEP_DELAY", "0"))
        except Exception:
            step_delay = 0
        run_agent(
            controller=controller_url,
            ollama=ollama_url,
            lmstudio=lmstudio_url,
            openai=openai_url,
            openai_api_key=openai_key,
            steps=(None if steps <= 0 else steps),
            step_delay=step_delay,
        )
    else:
        # Controller-Polling-Prozess (bestehender Modus)
        main()