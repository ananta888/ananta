import asyncio
import json
import logging
import os
import threading
import time
from typing import Dict

from flask import Flask, jsonify
from werkzeug.serving import make_server

from common.http_client import http_get, http_post
from src.agents.templates import PromptTemplates
from src.config import ConfigManager, LogManager
from src.db import get_conn, init_db
from src.models import ModelPool

logger = logging.getLogger("ai_agent")


def create_app(agent_name: str = "default") -> Flask:
    """Create the Flask application hosting agent utility routes."""
    init_db()
    app = Flask(__name__)

    @app.route("/health")
    def health_check():
        return jsonify({"status": "ok"})

    @app.route("/stop", methods=["POST"])
    def create_stop_flag():
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO agent.flags (name, value) VALUES ('stop', '1') "
            "ON CONFLICT (name) DO UPDATE SET value='1'"
        )
        conn.commit()
        cur.close()
        conn.close()
        return "OK"

    @app.route("/restart", methods=["POST"])
    def remove_stop_flag():
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM agent.flags WHERE name='stop'")
        conn.commit()
        cur.close()
        conn.close()
        return "OK"

    @app.route("/logs")
    def list_logs():
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT level, message, created_at FROM agent.logs WHERE agent=%s ORDER BY id",
            (agent_name,),
        )
        logs = [
            {
                "level": r[0],
                "message": r[1],
                "created_at": r[2].isoformat() if r[2] else None,
            }
            for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify({"agent": agent_name, "logs": logs})

    @app.route("/tasks")
    def list_tasks():
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT data FROM controller.config ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cfg = row[0] if row else {}
        current = cfg.get("agents", {}).get(agent_name, {}).get("current_task")
        cur.execute(
            "SELECT task, agent, template FROM controller.tasks WHERE agent=%s ORDER BY id",
            (agent_name,),
        )
        tasks = [
            {"task": r[0], "agent": r[1], "template": r[2]} for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify({"agent": agent_name, "current_task": current, "tasks": tasks})

    return app


class ControllerAgent:
    """Einfache Agentenklasse, die Logs in der Datenbank speichert."""

    def __init__(self, name: str):
        self.name = name

    def log_status(self, message: str, level: int = logging.INFO):
        if level >= logger.level:
            logger.log(level, message, extra={"agent": self.name})


def _stop_requested() -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM agent.flags WHERE name='stop'")
    res = cur.fetchone()
    cur.close()
    conn.close()
    return res is not None


DEFAULT_ENDPOINTS: Dict[str, str] = {
    "lmstudio": "http://host.docker.internal:1234/v1/completions"
}


def check_controller_connection(controller_url: str) -> bool:
    """Prüft die Verbindung zum Controller."""
    try:
        http_get(f"{controller_url}/health", retries=1)
        logger.info("Erfolgreich mit Controller verbunden")
        return True
    except Exception as e:  # pragma: no cover - network errors
        logger.error("Fehler bei Controller-Verbindung: %s", e)
        return False


def run_agent(
    controller: str | None = None,
    endpoints: Dict[str, str] | None = None,
    openai_api_key: str | None = None,
    steps: int | None = None,
    step_delay: int = 0,
    pool: object | None = None,
):
    """Hauptschleife des AI-Agenten."""
    cfg_manager = ConfigManager(
        os.path.join(os.path.dirname(__file__), "..", "config.json")
    )
    cfg = cfg_manager.read()
    LogManager.setup("agent")

    log_level = os.environ.get("AI_AGENT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    init_db()

    if controller is None:
        controller = cfg.get("controller_url")
    while not check_controller_connection(controller):
        logger.info("Warte auf Controller...")
        time.sleep(2)

    pool = pool or ModelPool()
    endpoint_map = {**DEFAULT_ENDPOINTS}
    for ep in cfg.get("api_endpoints", []):
        typ = ep.get("type")
        url = ep.get("url")
        if typ and url:
            endpoint_map[typ] = url
    if endpoints:
        endpoint_map.update(endpoints)

    current_agent = cfg.get("active_agent", "default")
    agent_instance = ControllerAgent(current_agent)
    logger.info("Starte AI-Agent für '%s'", current_agent)

    app = create_app(current_agent)
    server = make_server("0.0.0.0", 5000, app)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    step = 0
    while steps is None or step < steps:
        if _stop_requested():
            logger.info("STOP-Flag gefunden, beende Agent-Schleife.")
            break

        try:
            task_entry = http_get(f"{controller}/tasks/next?agent={current_agent}")
            agent_instance.log_status(
                f"Task-Empfang: {task_entry}", level=logging.DEBUG
            )
        except Exception as e:  # pragma: no cover - network errors
            agent_instance.log_status(
                f"Verbindung zum Controller fehlgeschlagen: {e}",
                level=logging.ERROR,
            )
            logger.error("Verbindung zum Controller fehlgeschlagen: %s", e)
            time.sleep(1)
            continue

        task = task_entry.get("task") if isinstance(task_entry, dict) else None
        if not task:
            task = "Standardaufgabe: Keine spezifische Aufgabe definiert."

        if task.startswith("Standardaufgabe:"):
            approve_payload = {
                "agent": current_agent,
                "task": task,
                "response": "SKIP",
            }
            approve_url = f"{controller}/approve"
            try:
                http_post(approve_url, approve_payload)
            except Exception as e:  # pragma: no cover - network errors
                agent_instance.log_status(
                    f"Fehler beim Senden an den Controller: {e}",
                    level=logging.ERROR,
                )
                logger.warning(
                    "Fehler beim Senden der Genehmigung an den Controller: %s", e
                )
            time.sleep(step_delay)
            step += 1
            continue

        agent_instance.log_status(
            f"Starte Verarbeitung des Tasks: {task}", level=logging.INFO
        )
        logger.info("[Step %s] Bearbeite Aufgabe: %s", step, task)

        templates = PromptTemplates(cfg.get("prompt_templates", {}))
        template_name = task_entry.get("template") or current_agent
        prompt = templates.render(template_name, task=task)
        if not prompt:
            prompt = f"Bitte verarbeite folgende Aufgabe: {task}"
        agent_instance.log_status(
            f"Generierter Prompt: {prompt}", level=logging.DEBUG
        )

        data_payload = {
            "prompt": prompt,
            "max_tokens": 50,
            "model": "qwen3-zero-coder-reasoning-0.8b-neo-ex",
        }

        api_url = endpoint_map.get("lmstudio")
        if api_url is None:
            logger.warning(
                "Kein gültiger API-Endpunkt gefunden. Überspringe diesen Durchlauf."
            )
        else:
            try:
                asyncio.run(
                    pool.acquire("lmstudio", "qwen3-zero-coder-reasoning-0.8b-neo-ex")
                )
                try:
                    response = http_post(api_url, data_payload)
                finally:
                    pool.release(
                        "lmstudio", "qwen3-zero-coder-reasoning-0.8b-neo-ex"
                    )
                agent_instance.log_status(
                    f"LLM-Antwort: {response}", level=logging.INFO
                )
                logger.info("Antwort des LLM von %s: %s", api_url, response)

                approve_payload = {
                    "agent": current_agent,
                    "task": task,
                    "response": response,
                }
                approve_url = f"{controller}/approve"
                try:
                    approve_resp = http_post(approve_url, approve_payload)
                    agent_instance.log_status(
                        f"Controller-Antwort: {approve_resp}", level=logging.DEBUG
                    )
                    logger.debug("Anerkennung vom Controller: %s", approve_resp)
                except Exception as e:  # pragma: no cover - network errors
                    agent_instance.log_status(
                        f"Fehler beim Senden an den Controller: {e}",
                        level=logging.ERROR,
                    )
                    logger.warning(
                        "Fehler beim Senden der Genehmigung an den Controller: %s",
                        e,
                    )
            except Exception as e:  # pragma: no cover - network errors
                agent_instance.log_status(
                    f"Fehler beim Aufruf des API-Endpunkts {api_url}: {e}",
                    level=logging.ERROR,
                )
                logger.error(
                    "Fehler beim Aufruf des API-Endpunkts %s: %s", api_url, e
                )

        time.sleep(step_delay)
        step += 1

    agent_instance.log_status("Agent beendet.", level=logging.INFO)

    server.shutdown()
    server_thread.join()

    return agent_instance


if __name__ == "__main__":
    run_agent(controller="http://controller:8081")
