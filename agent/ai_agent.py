import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
import asyncio
import threading
import logging

from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

from src.models import ModelPool
from src.agents.templates import PromptTemplates
from src.config import ConfigManager, LogManager
from src.db import get_conn
from psycopg2.extras import Json

cfg_manager = ConfigManager(os.path.join(os.path.dirname(__file__), "..", "config.json"))
_cfg = cfg_manager.read()
LogManager.setup("agent")
LOG_LEVEL = os.environ.get("AI_AGENT_LOG_LEVEL", "INFO").upper()
LOG_LEVEL_NUM = getattr(logging, LOG_LEVEL, logging.INFO)
logger = logging.getLogger("ai_agent")
logger.setLevel(LOG_LEVEL_NUM)


app = Flask(__name__)


class ControllerAgent:
    """Einfache Agentenklasse, die Logs in der Datenbank speichert."""

    def __init__(self, name: str):
        self.name = name

    def log_status(self, message: str, level: int = logging.INFO):
        if level >= LOG_LEVEL_NUM:
            logger.log(level, message, extra={"agent": self.name})


# Registrierte Agenteninstanzen, die über den HTTP-Endpunkt abgefragt werden können
AGENTS: dict[str, ControllerAgent] = {}


@app.route("/agent/<name>/log")
def agent_log(name: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT message FROM agent.logs WHERE agent=%s ORDER BY id",
        (name,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        return jsonify({"error": "agent not found"}), 404
    return Response("\n".join(r[0] for r in rows), mimetype="text/plain")


@app.route("/agent/config", methods=["GET", "POST"])
def agent_config_endpoint():
    """Read or write the AI agent configuration from PostgreSQL."""
    conn = get_conn()
    cur = conn.cursor()
    if request.method == "GET":
        cur.execute("SELECT data FROM agent.config ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify(row[0] if row else {})
    cfg = request.get_json(silent=True) or {}
    cur.execute("INSERT INTO agent.config (data) VALUES (%s)", (Json(cfg),))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/approve", methods=["POST"])
def approve_result():
    """Persist blacklist and control log entries."""
    cmd = request.form.get("cmd", "").strip()
    summary = request.form.get("summary", "").strip()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM controller.blacklist WHERE cmd=%s", (cmd,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return "SKIP"
    cur.execute("INSERT INTO controller.blacklist (cmd) VALUES (%s)", (cmd,))
    cur.execute(
        "INSERT INTO controller.control_log (received, summary, approved) VALUES (%s, %s, 'OK')",
        (cmd, summary),
    )
    conn.commit()
    cur.close()
    conn.close()
    return cmd


@app.route("/stop", methods=["POST"])
def create_stop_flag():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO agent.flags (name, value) VALUES ('stop', '1') ON CONFLICT (name) DO UPDATE SET value='1'"
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


def _stop_requested() -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM agent.flags WHERE name='stop'")
    res = cur.fetchone()
    cur.close()
    conn.close()
    return res is not None




def _http_get(url: str, retries: int = 5, delay: float = 1.0):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url) as r:
                raw = r.read().decode()
                try:
                    return json.loads(raw)
                except Exception:
                    return raw
        except urllib.error.URLError as e:
            last_err = e
            if attempt < retries:
                logger.warning(
                    "[_http_get] Versuch %s/%s gescheitert, warte %ss…",
                    attempt,
                    retries,
                    delay,
                )
                time.sleep(delay)
            else:
                raise last_err


def _http_post(
    url: str,
    data: dict,
    form: bool = False,
    headers: dict | None = None,
    retries: int = 5,
    delay: float = 1.0,
):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            if form:
                body = urllib.parse.urlencode(data).encode()
                hdrs = headers or {}
            else:
                body = json.dumps(data).encode()
                hdrs = {"Content-Type": "application/json"}
                if headers:
                    hdrs.update(headers)
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req) as r:
                resp = r.read().decode()
                try:
                    return json.loads(resp)
                except Exception:
                    return resp
        except urllib.error.URLError as e:
            last_err = e
            if attempt < retries:
                logger.warning(
                    "[_http_post] Versuch %s/%s gescheitert, warte %ss…",
                    attempt,
                    retries,
                    delay,
                )
                time.sleep(delay)
            else:
                raise last_err


# Angenommene Default-Endpunkte als Fallback
DEFAULT_ENDPOINTS = {
    "lmstudio": "http://host.docker.internal:1234/v1/completions"
}


def run_agent(
    controller: str = None,
    endpoints: dict[str, str] | None = None,
    openai_api_key: str | None = None,
    steps: int | None = None,
    step_delay: int = 0,
    pool: object | None = None,
):
    """
    Hauptschleife des AI-Agenten.
    
    - Abfrage der nächsten Konfiguration und Aufgaben vom Controller via GET /next-config.
    - Rendern des Prompts via PromptTemplates (bei vorhandener Vorlage).
    - Nutzung eines ModelPools zur Begrenzung paralleler LLM-Anfragen.
    - Senden der generierten Antwort an den /approve-Endpoint des Controllers.
    - Protokollierung von Logs und Zusammenfassung.
    """
    cfg = cfg_manager.read()
    if controller is None:
        controller = cfg.get("controller_url")

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
    AGENTS[current_agent] = agent_instance
    logger.info("Starte AI-Agent für '%s'", current_agent)

    # Flask-Webserver im Hintergrund starten
    server = make_server("0.0.0.0", 5000, app)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    step = 0
    while steps is None or step < steps:
        if _stop_requested():
            logger.info("STOP-Flag gefunden, beende Agent-Schleife.")
            break

        try:
            task_entry = _http_get(f"{controller}/tasks/next?agent={current_agent}")
            agent_instance.log_status(
                f"Task-Empfang: {task_entry}", level=logging.DEBUG
            )
        except Exception as e:
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
                _http_post(approve_url, approve_payload)
            except Exception as e:
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

        # Prompt anhand der übermittelten Templates erzeugen
        templates = PromptTemplates(cfg.get("prompt_templates", {}))
        template_name = task_entry.get("template") or current_agent
        prompt = templates.render(template_name, task=task)
        if not prompt:
            prompt = f"Bitte verarbeite folgende Aufgabe: {task}"
        agent_instance.log_status(
            f"Generierter Prompt: {prompt}", level=logging.DEBUG
        )
        # Erforderliche Felder: prompt, max_tokens und model
        data_payload = {
            "prompt": prompt,
            "max_tokens": 50,  # Beispielwert, anpassen nach Bedarf
            "model": "qwen3-zero-coder-reasoning-0.8b-neo-ex"
        }

        # Wähle einen Endpunkt – als Beispiel der "lmstudio"-Endpunkt
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
                    response = _http_post(api_url, data_payload)
                finally:
                    pool.release("lmstudio", "qwen3-zero-coder-reasoning-0.8b-neo-ex")
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
                    approve_resp = _http_post(approve_url, approve_payload)
                    agent_instance.log_status(
                        f"Controller-Antwort: {approve_resp}", level=logging.DEBUG
                    )
                    logger.debug("Anerkennung vom Controller: %s", approve_resp)
                except Exception as e:
                    agent_instance.log_status(
                        f"Fehler beim Senden an den Controller: {e}",
                        level=logging.ERROR,
                    )
                    logger.warning(
                        "Fehler beim Senden der Genehmigung an den Controller: %s",
                        e,
                    )
            except Exception as e:
                agent_instance.log_status(
                    f"Fehler beim Aufruf des API-Endpunkts {api_url}: {e}",
                    level=logging.ERROR,
                )
                logger.error(
                    "Fehler beim Aufruf des API-Endpunkts %s: %s", api_url, e
                )

        # Wartezeit zwischen den Schritten
        time.sleep(step_delay)
        step += 1

    agent_instance.log_status("Agent beendet.", level=logging.INFO)

    # Webserver stoppen
    server.shutdown()
    server_thread.join()

    return agent_instance


if __name__ == "__main__":
    run_agent(controller="http://controller:8081")