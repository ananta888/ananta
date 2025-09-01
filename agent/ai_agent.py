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
from flask import Flask, jsonify, Response
from typing import Optional, List, Dict, Any

from src.db import get_conn

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
                    # Simulate processing
                    _add_log(agent_name, f"Processed: {task}")
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