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
from flask import Flask, jsonify
from typing import Optional, List, Dict, Any

from src.db import get_conn


def create_app(agent: str = "default") -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

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
    """Continuously poll the controller for tasks and approve them."""
    import os
    # Umgebungsvariable für die Controller-URL auslesen
    controller_url = os.environ.get("CONTROLLER_URL", "http://controller:8081")
    print(f"Verbindung zum Controller unter: {controller_url}")

    while True:
        try:
            resp = requests.get(f"{controller_url}/next-config")
            data = resp.json()
            for task in data.get("tasks", []):
                result = {
                    "task": task,
                    "result": f"Executed {task}",
                }
                # Note: The agent app intentionally has no /approve route (see tests)
                requests.post(f"{controller_url}/approve", json=result)
        except requests.exceptions.ConnectionError as e:
            print(f"Verbindungsfehler zum Controller: {e}")
        except Exception as e:
            print(f"Fehler bei der Kommunikation mit dem Controller: {e}")
        time.sleep(5)  # Längeres Polling-Intervall für bessere Fehlertoleranz


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