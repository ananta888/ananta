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

from src.db import get_conn

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
                    try:
                        conn = get_conn()
                        cur = conn.cursor()
                        try:
                            cur.execute(
                                "INSERT INTO agent.logs (agent, level, message) VALUES (%s, %s, %s)",
                                (agent_name, "INFO", f"Received task: {task}")
                            )
                            conn.commit()
                        finally:
                            cur.close()
                            conn.close()
                    except Exception as e:
                        print(f"Warn: failed to persist agent log (received): {e}")

                    # Simulate processing
                    _add_log(agent_name, f"Processed: {task}")
                    try:
                        conn = get_conn()
                        cur = conn.cursor()
                        try:
                            cur.execute(
                                "INSERT INTO agent.logs (agent, level, message) VALUES (%s, %s, %s)",
                                (agent_name, "INFO", f"Processed: {task}")
                            )
                            conn.commit()
                        finally:
                            cur.close()
                            conn.close()
                    except Exception as e:
                        print(f"Warn: failed to persist agent log (processed): {e}")

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

    # Controller-Polling-Prozess starten
    main()