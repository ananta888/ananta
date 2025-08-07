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

# Allow overriding data directory for testing via the DATA_DIR environment variable
# Fall back to the project root so controller and agent share the same files
DATA_DIR = os.environ.get(
    "DATA_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
STOP_FLAG = os.path.join(DATA_DIR, "stop.flag")

LOG_LEVEL = os.environ.get("AI_AGENT_LOG_LEVEL", "INFO").upper()
LOG_LEVEL_NUM = getattr(logging, LOG_LEVEL, logging.INFO)
logging.basicConfig(level=LOG_LEVEL_NUM, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


app = Flask(__name__)


class ControllerAgent:
    """Einfache Agentenklasse, die Logs im Speicher sammelt."""

    def __init__(self, name: str):
        self.name = name
        self._log: list[str] = []
        self._log_file, _ = _agent_files(name)
        os.makedirs(os.path.dirname(self._log_file), exist_ok=True)
        if os.path.exists(self._log_file):
            with open(self._log_file, "r", encoding="utf-8") as f:
                self._log.extend(line.rstrip("\n") for line in f)

    def log_status(self, message: str, level: int = logging.INFO):
        if level >= LOG_LEVEL_NUM:
            entry = (
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"{logging.getLevelName(level)} {message}"
            )
            self._log.append(entry)
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(entry + "\n")


# Registrierte Agenteninstanzen, die über den HTTP-Endpunkt abgefragt werden können
AGENTS: dict[str, ControllerAgent] = {}


@app.route("/agent/<name>/log")
def agent_log(name: str):
    agent = AGENTS.get(name)
    if not agent:
        return jsonify({"error": "agent not found"}), 404
    return Response("\n".join(agent._log), mimetype="text/plain")


def _safe_path(filename: str) -> str:
    """Ensure the requested filename stays within DATA_DIR."""
    path = os.path.abspath(os.path.join(DATA_DIR, filename))
    if not path.startswith(os.path.abspath(DATA_DIR)):
        raise ValueError("invalid path")
    return path


@app.route("/file/<path:filename>", methods=["GET", "POST"])
def handle_file(filename: str):
    """Generic file reader/writer within DATA_DIR."""
    try:
        path = _safe_path(filename)
    except ValueError:
        return jsonify({"error": "forbidden"}), 403

    if request.method == "GET":
        if not os.path.exists(path):
            return jsonify({"error": "not found"}), 404
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
        try:
            return jsonify(json.loads(data))
        except Exception:
            return jsonify({"content": data})

    data = request.get_json(silent=True) or {}
    content = data.get("content", data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(content, (dict, list)):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(content))
    return jsonify({"status": "ok"})


@app.route("/config", methods=["GET", "POST"])
def config_endpoint():
    """Read or write the controller configuration."""
    cfg_path = _safe_path("config.json")
    team_path = _safe_path("default_team_config.json")
    if request.method == "GET":
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(cfg_path):
            if os.path.exists(team_path):
                with open(team_path, "r", encoding="utf-8") as f:
                    try:
                        cfg = json.load(f)
                    except Exception:
                        cfg = {}
            else:
                cfg = {}
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        else:
            with open(cfg_path, "r", encoding="utf-8") as f:
                try:
                    cfg = json.load(f)
                except Exception:
                    cfg = {}

        agents_keys = list(cfg.get("agents", {}).keys())
        order = cfg.get("pipeline_order", [])
        for name in agents_keys:
            if name not in order:
                order.append(name)
        cfg["pipeline_order"] = order
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return jsonify(cfg)

    cfg = request.get_json(silent=True) or {}
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return jsonify({"status": "ok"})


@app.route("/approve", methods=["POST"])
def approve_result():
    """Persist blacklist and control log entries."""
    cmd = request.form.get("cmd", "").strip()
    summary = request.form.get("summary", "").strip()
    bl_path = _safe_path("blacklist.txt")
    log_path = _safe_path("control_log.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(bl_path, "a+") as f:
        f.seek(0)
        if any(line.strip() == cmd for line in f):
            return "SKIP"
        f.write(cmd + "\n")
    with open(log_path, "a", encoding="utf-8") as f:
        json.dump(
            {
                "received": cmd,
                "summary": summary,
                "approved": "OK",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            f,
        )
        f.write(",\n")
    return cmd


@app.route("/stop", methods=["POST"])
def create_stop_flag():
    open(STOP_FLAG, "w").close()
    return "OK"


@app.route("/restart", methods=["POST"])
def remove_stop_flag():
    try:
        os.remove(STOP_FLAG)
    except Exception:
        pass
    return "OK"


def _agent_files(agent: str) -> tuple[str, str]:
    return (
        os.path.join(DATA_DIR, f"ai_log_{agent}.json"),
        os.path.join(DATA_DIR, f"summary_{agent}.txt"),
    )


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
    # Verwende als Standard den Wert der Umgebungsvariable oder localhost, falls nicht gesetzt
    if controller is None:
        controller = os.environ.get("CONTROLLER_URL", "http://localhost:8081")
    
    pool = pool or ModelPool()
    endpoint_map = {**DEFAULT_ENDPOINTS, **(endpoints or {})}
    
    os.makedirs(DATA_DIR, exist_ok=True)

    current_agent = "default"
    _, summary_file = _agent_files(current_agent)
    agent_instance = ControllerAgent(current_agent)
    AGENTS[current_agent] = agent_instance
    logger.info("Starte AI-Agent für '%s'. Summary: %s", current_agent, summary_file)

    # Flask-Webserver im Hintergrund starten
    server = make_server("0.0.0.0", 5000, app)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    step = 0
    while steps is None or step < steps:
        if os.path.exists(STOP_FLAG):
            logger.info("STOP_FLAG gefunden, beende Agent-Schleife.")
            break

        try:
            cfg = _http_get(f"{controller}/next-config")
            agent_instance.log_status(
                f"Task-Empfang: {cfg.get('tasks')}", level=logging.DEBUG
            )
        except Exception as e:
            agent_instance.log_status(
                f"Verbindung zum Controller fehlgeschlagen: {e}",
                level=logging.ERROR,
            )
            logger.error("Verbindung zum Controller fehlgeschlagen: %s", e)
            time.sleep(1)
            continue

        # Aktualisierung der Endpunkte aus der Controller-Konfiguration
        cfg_map: dict[str, str] = {}
        for ep in cfg.get("api_endpoints", []):
            typ = ep.get("type")
            url = ep.get("url")
            if typ and url and typ not in cfg_map:
                cfg_map[typ] = url
        endpoint_map.update(cfg_map)

        # Überprüfen, ob in der Konfiguration eine Aufgabe vorhanden ist
        if "tasks" in cfg and isinstance(cfg["tasks"], list) and cfg["tasks"]:
            task_entry = cfg["tasks"].pop(0)
            task = (
                task_entry["task"]
                if isinstance(task_entry, dict) and "task" in task_entry
                else task_entry
            )
        else:
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
        template_name = cfg.get("template") or current_agent
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
                with open(summary_file, "a") as sf:
                    sf.write(f"[Step {step}] {response}\n")

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
    run_agent()
