import os
from flask import (
    Flask,
    request,
    jsonify,
    render_template_string,
    send_file,
    redirect,
    make_response,
    send_from_directory,
    Response,
    abort,
)
import json, zipfile, io, urllib.request, urllib.error, time, logging
from datetime import datetime

from src.config import ConfigManager, LogManager
from src.tasks import TaskStore
from src.db import get_conn
from psycopg2.extras import Json

LOG_LEVEL = os.environ.get("CONTROLLER_LOG_LEVEL", "INFO").upper()

app = Flask(__name__)


# Register additional controller blueprint routes
try:
    from src.controller.routes import bp as controller_bp
except Exception:  # pragma: no cover - fallback when packaged differently
    from controller.routes import bp as controller_bp  # type: ignore

app.register_blueprint(controller_bp)

# Persistenz in PostgreSQL
AGENT_URL = os.environ.get("AI_AGENT_URL", "http://localhost:5000")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.json")

config_manager = ConfigManager(CONFIG_PATH)
task_store = TaskStore()
LogManager.setup("controller")
logger = logging.getLogger("controller")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

# Optional Vue frontend distribution directory
FRONTEND_DIST = os.path.join("/app", "frontend", "dist")
from src.dashboard import DashboardManager, FileConfig
from common.http_client import http_post


PROVIDERS = ["ollama", "lmstudio", "openai"]
BOOLEAN_FIELDS: list[str] = []


def read_config() -> dict:
    """Read the controller configuration via :class:`ConfigManager`."""

    cfg = config_manager.read()
    global BOOLEAN_FIELDS
    BOOLEAN_FIELDS = sorted(
        {
            k
            for agent in cfg.get("agents", {}).values()
            for k, v in agent.items()
            if isinstance(v, bool)
        }
    )
    return cfg


def write_config(cfg: dict) -> None:
    """Persist the controller configuration via :class:`ConfigManager`."""

    config_manager.write(cfg)

def _add_blacklist(cmd: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO controller.blacklist (cmd) VALUES (%s) ON CONFLICT (cmd) DO NOTHING",
        (cmd,),
    )
    conn.commit()
    cur.close()
    conn.close()


def _add_control_log(cmd: str, summary: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO controller.control_log (received, summary, approved) VALUES (%s, %s, 'OK')",
        (cmd, summary),
    )
    conn.commit()
    cur.close()
    conn.close()


config_provider = FileConfig(read_config, write_config)
try:
    _initial_cfg = config_provider.read()
except Exception as exc:  # pragma: no cover - startup without ai-agent
    logger.error("Initial config load failed: %s", exc)
    _initial_cfg = {}
_template_agent = _initial_cfg.get("agents", {}).get(_initial_cfg.get("active_agent", ""), {})
dashboard_manager = DashboardManager(config_provider, _template_agent, PROVIDERS)


def fetch_issues(
    repo: str,
    token: str | None = None,
    *,
    retries: int = 3,
    delay: float = 1.0,
) -> list:
    """Fetch open GitHub issues for the given repository with retry.

    Parameters
    ----------
    repo: "owner/name" string specifying the repository.
    token: Optional GitHub access token for authenticated requests.
    retries: Number of attempts in case of failures.
    delay: Seconds to wait between attempts.

    Returns
    -------
    list
        A list of issue dictionaries. Pull requests are filtered out.
        On repeated failures an empty list is returned.
    """

    url = f"https://api.github.com/repos/{repo}/issues?state=open"

    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        if token:
            req.add_header("Authorization", f"token {token}")
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
            return [item for item in data if "pull_request" not in item]
        except Exception as exc:  # pragma: no cover - network errors
            logger.warning(
                "fetch_issues attempt %s/%s failed: %s", attempt, retries, exc
            )
            if attempt < retries:
                time.sleep(delay)
    logger.error("fetch_issues failed after %s attempts", retries)
    return []


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


@app.route("/next-config")
def next_config():
    cfg = read_config()
    agent = cfg.get("active_agent", "default")
    agents_cfg = cfg.get("agents", {})
    agent_state = agents_cfg.get(agent, {}).copy()
    agent_state["agent"] = agent
    agent_state["api_endpoints"] = cfg.get("api_endpoints", [])
    agent_state["prompt_templates"] = cfg.get("prompt_templates", {})
    return jsonify(agent_state)


@app.route("/tasks/next")
def next_task_endpoint():
    """Return the next task for the active agent."""
    cfg = read_config()
    agent = request.args.get("agent") or cfg.get("active_agent", "default")
    task = task_store.next_task(agent)
    if task:
        agents_cfg = cfg.get("agents", {})
        agent_state = agents_cfg.get(agent, {})
        agent_state["current_task"] = task.get("task")
        agents_cfg[agent] = agent_state
        cfg["agents"] = agents_cfg
        write_config(cfg)
    return jsonify(task or {"task": None})


@app.route("/config")
def full_config():
    """Return the complete controller configuration as JSON."""
    return jsonify(read_config())


@app.route("/health")
def health_check():
    """Health-Endpoint für den Controller."""
    return jsonify({"status": "ok"})


@app.route("/config/api_endpoints", methods=["POST"])
def update_api_endpoints():
    """Update API endpoints in the configuration."""
    data = request.get_json(silent=True) or {}
    endpoints = data.get("api_endpoints")
    if not isinstance(endpoints, list):
        return jsonify({"error": "api_endpoints must be a list"}), 400
    cfg = read_config()
    cfg["api_endpoints"] = [
        {
            "type": ep.get("type", ""),
            "url": ep.get("url", ""),
            "models": [m for m in ep.get("models", []) if m],
        }
        for ep in endpoints
        if ep.get("url")
    ]
    write_config(cfg)
    return jsonify({"api_endpoints": cfg["api_endpoints"]})


@app.route("/config/models", methods=["POST"])
def update_models():
    """Update available models in the configuration."""
    data = request.get_json(silent=True) or {}
    models = data.get("models")
    if not isinstance(models, list):
        return jsonify({"error": "models must be a list"}), 400
    cfg = read_config()
    cfg["models"] = [m for m in models if m]
    write_config(cfg)
    return jsonify({"models": cfg["models"]})


@app.route("/config/agents", methods=["POST"])
def update_agents():
    """Persist agent configuration."""
    data = request.get_json(silent=True) or {}
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return jsonify({"error": "agents must be a dict"}), 400
    cfg = read_config()
    cfg["agents"] = agents
    write_config(cfg)
    return jsonify({"agents": cfg["agents"]})


@app.route("/config/active_agent", methods=["POST"])
def update_active_agent():
    """Update the active agent."""
    data = request.get_json(silent=True) or {}
    agent = data.get("active_agent")
    if not isinstance(agent, str):
        return jsonify({"error": "active_agent must be a string"}), 400
    cfg = read_config()
    if agent not in cfg.get("agents", {}):
        return jsonify({"error": "unknown agent"}), 400
    cfg["active_agent"] = agent
    write_config(cfg)
    return jsonify({"active_agent": cfg["active_agent"]})


@app.route("/approve", methods=["POST"])
def approve():
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


@app.route("/issues")
def issues():
    """Return open GitHub issues as JSON.

    Optional query parameters:
    repo  - repository in the form ``owner/name`` (defaults to ``GITHUB_REPO`` env var)
    token - GitHub token, falls back to ``GITHUB_TOKEN`` env var
    enqueue=1 - add fetched issues as tasks to config.json
    """

    repo = request.args.get("repo") or os.environ.get("GITHUB_REPO")
    token = request.args.get("token") or os.environ.get("GITHUB_TOKEN")
    if not repo:
        return jsonify({"error": "repo required"}), 400
    issues = fetch_issues(repo, token)
    if request.args.get("enqueue") == "1":
        cfg = read_config()
        for issue in issues:
            title = issue.get("title", "")
            number = issue.get("number")
            url = issue.get("html_url")
            text = f"Issue #{number}: {title} ({url})"
            existing = [t.get("task") for t in task_store.list_tasks()]
            if text not in existing:
                task_store.add_task(text)
    return jsonify(issues)


# --------------------------
# Theme-Template System
# --------------------------
THEMES_DIR = os.path.join(os.path.dirname(__file__), "themes")

def list_themes():
    """Listet alle verfügbaren Themes im THEMES_DIR auf (ohne Dateiendung)."""
    try:
        return [os.path.splitext(f)[0] for f in os.listdir(THEMES_DIR) if f.endswith(".html")]
    except Exception:
        return ["default"]

def load_theme(theme_name):
    """Lädt das Theme-Template anhand des Namens (z.B. default => default.html)."""
    theme_file = os.path.join(THEMES_DIR, f"{theme_name}.html")
    if os.path.exists(theme_file):
        with open(theme_file, "r", encoding="utf-8") as f:
            return f.read()
    return None

@app.route("/set_theme", methods=["POST"])
def set_theme():
    """Setzt das gewünschte Theme via Cookie."""
    selected_theme = request.form.get("theme", "default")
    response = make_response(redirect("/"))
    # Cookie 30 Tage gültig
    response.set_cookie("theme", selected_theme, max_age=30*24*3600)
    return response

# --------------------------
# Bestehender Code (z.B. read_config, agent_log_file, etc.)
# --------------------------
# ... [bestehender Code] ...

@app.route("/", methods=["GET", "POST"])
def dashboard():
    if request.method == "POST":
        dashboard_manager.handle_post(request)
        return redirect("/")
    cfg = config_provider.read()
    active = cfg.get("active_agent", "default")
    agent_cfg = cfg.get("agents", {}).get(active, {})
    order_list = cfg.get("pipeline_order", [])
    tasks_grouped = {}
    for idx, t in enumerate(task_store.list_tasks()):
        agent = t.get("agent") or "auto"
        tasks_grouped.setdefault(agent, []).append((idx, t))
    agents_ordered = []
    for name in order_list:
        agent = cfg.get("agents", {}).get(name)
        if agent:
            agents_ordered.append((name, agent))
    for name, agent in cfg.get("agents", {}).items():
        if name not in order_list:
            agents_ordered.append((name, agent))
    summary = ""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT message FROM agent.logs WHERE agent=%s ORDER BY id",
            (active,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        log = "\n".join(r[0] for r in rows)[-4000:]
    except Exception:
        log = "Kein Log"

    # Theme-Auswahl über Cookie
    current_theme = request.cookies.get("theme", "default")
    theme_template = load_theme(current_theme)
    if theme_template is None:
        theme_template = ""  # Fallback: Standard-Template aus untenstehendem Template
    return render_template_string(
        theme_template,
        config=cfg,
        active=active,
        agent_cfg=agent_cfg,
        summary=summary,
        log=log,
        providers=PROVIDERS,
        boolean_fields=BOOLEAN_FIELDS,
        pipeline_order=order_list,
        agents_ordered=agents_ordered,
        tasks_grouped=tasks_grouped,
        available_themes=list_themes(),
        current_theme=current_theme,
        github_repo=os.environ.get("GITHUB_REPO")
    )
#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Controller-Modul für das Ananta-System."""

import os
import logging
from flask import Flask, jsonify, send_from_directory

app = Flask(__name__)

# Logging konfigurieren
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pfad zum Vue-Frontend-Build-Verzeichnis
FRONTEND_DIST = os.path.join("/app", "frontend", "dist")

@app.route('/health')
def health_check():
    """Endpunkt für Health-Checks."""
    return jsonify({"status": "healthy", "service": "controller"})

@app.route('/config')
def get_config():
    """Gibt die aktuelle Konfiguration zurück."""
    return jsonify({"config": "example", "version": "1.0.0"})

@app.route("/ui", endpoint="ui_frontend")
def ui_index():
    """Serve the Vue frontend index.html."""
    if os.path.exists(os.path.join(FRONTEND_DIST, "index.html")):
        return send_from_directory(FRONTEND_DIST, "index.html")
    return "Frontend nicht gebaut: " + os.path.join(FRONTEND_DIST, "index.html"), 404

@app.route("/ui/", endpoint="ui_index_slash")
def ui_index_with_slash():
    """Serve the Vue frontend index.html with trailing slash."""
    if os.path.exists(os.path.join(FRONTEND_DIST, "index.html")):
        return send_from_directory(FRONTEND_DIST, "index.html")
    return "Frontend nicht gebaut: " + os.path.join(FRONTEND_DIST, "index.html"), 404

@app.route("/ui/<path:path>")
def ui_static(path):
    """Serve static files from the Vue frontend build."""
    if os.path.exists(os.path.join(FRONTEND_DIST, path)):
        return send_from_directory(FRONTEND_DIST, path)
    return "Frontend nicht gebaut", 404

if __name__ == "__main__":
    # Serverport aus Umgebungsvariable oder Standard 8081
    port = int(os.environ.get("PORT", 8081))

    logger.info(f"Starting controller on port {port}")
    logger.info(f"Vue Frontend wird bereitgestellt unter: http://localhost:{port}/ui")

    # Server starten
    app.run(host="0.0.0.0", port=port, debug=False)

@app.route("/agent/<name>/toggle_active", methods=["POST"])
def toggle_agent_active(name: str):
    """Toggle the ``controller_active`` flag for a given agent.

    Returns JSON with the new status or a 404 if the agent does not exist.
    """
    cfg = read_config()
    agent_cfg = cfg.get("agents", {}).get(name)
    if not agent_cfg:
        return ("Agent not found", 404)
    agent_cfg["controller_active"] = not agent_cfg.get("controller_active", True)
    write_config(cfg)
    return jsonify({"controller_active": agent_cfg["controller_active"]})


@app.route("/agent/add_task", methods=["POST"])
def add_task():
    """Add a task to the global configuration."""
    data = request.get_json(silent=True) or request.form.to_dict()
    logger.debug("/agent/add_task payload: %s", data)
    task = (data.get("task") or "").strip()
    if not task:
        logger.error("/agent/add_task called without task")
        return jsonify({"error": "task required"}), 400
    agent_name = (data.get("agent") or "").strip() or None
    template = (data.get("template") or "").strip() or None
    entry = task_store.add_task(task, agent=agent_name, template=template)
    logger.info("Added task '%s' for agent '%s'", task, agent_name or "auto")
    return jsonify({"added": entry}), 201


@app.route("/agent/<name>/log", methods=["GET", "DELETE"])
def agent_log(name: str):
    """Return or clear log entries for the given agent."""

    if request.method == "DELETE":
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM agent.logs WHERE agent=%s", (name,))
        conn.commit()
        cur.close()
        conn.close()
        return ("", 204)

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
        return ("", 404)
    return Response("\n".join(r[0] for r in rows), mimetype="text/plain")


@app.route("/agent/<name>/tasks")
def agent_tasks(name: str):
    """Return current and pending tasks for the given agent."""
    cfg = read_config()
    agents = cfg.get("agents", {})
    tasks = task_store.list_tasks(agent=name)
    current = agents.get(name, {}).get("current_task")
    return jsonify({"agent": name, "current_task": current, "tasks": tasks})


@app.route("/stop", methods=["POST"])
def stop():
    http_post(f"{AGENT_URL}/stop", {})
    return "OK"


@app.route("/restart", methods=["POST"])
def restart():
    http_post(f"{AGENT_URL}/restart", {})
    return "OK"


@app.route("/export")
def export_logs():
    mem = io.BytesIO()
    cfg = read_config()
    with zipfile.ZipFile(mem, "w") as zf:
        zf.writestr("config.json", json.dumps(cfg, indent=2))
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT cmd FROM controller.blacklist")
        bl = "\n".join(row[0] for row in cur.fetchall())
        zf.writestr("blacklist.txt", bl)
        cur.execute(
            "SELECT received, summary, approved, timestamp FROM controller.control_log"
        )
        log_entries = [
            {
                "received": r[0],
                "summary": r[1],
                "approved": r[2],
                "timestamp": r[3].isoformat() if r[3] else None,
            }
            for r in cur.fetchall()
        ]
        zf.writestr("control_log.json", json.dumps(log_entries, indent=2))
        for name in cfg.get("agents", {}):
            cur.execute(
                "SELECT level, message, created_at FROM agent.logs WHERE agent=%s ORDER BY id",
                (name,),
            )
            logs = [
                {
                    "level": r[0],
                    "message": r[1],
                    "timestamp": r[2].isoformat() if r[2] else None,
                }
                for r in cur.fetchall()
            ]
            zf.writestr(f"ai_log_{name}.json", json.dumps(logs, indent=2))
        cur.close()
        conn.close()
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="export.zip", mimetype="application/zip")


def check_endpoint_status(url: str, timeout: float = 2.0) -> dict:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout):
            # Bei erfolgreichem HEAD-Request
            return {"url": url, "status": "OK"}
    except Exception as e:
        return {"url": url, "status": f"Fehler: {e}"}


@app.route("/llm_status", methods=["GET"])
def llm_status():
    # Wir benutzen entweder die im Config gespeicherten Endpunkte oder DEFAULT_ENDPOINTS
    cfg = read_config()
    api_endpoints = cfg.get("api_endpoints", [])
    # Wenn keine Konfiguration vorliegt, greifen wir auf die Default-Werte zurück:
    if not api_endpoints:
        api_endpoints = [{"type": k, "url": v} for k, v in {
            "lmstudio": "http://host.docker.internal:1234/v1/chat/completions"
        }.items()]
    status_list = []
    for ep in api_endpoints:
        url = ep.get("url")
        typ = ep.get("type", "unbekannt")
        if url:
            result = check_endpoint_status(url)
            result["type"] = typ
            status_list.append(result)
    return jsonify(status_list)


@app.route("/debug/api_endpoints", methods=["GET"])
def debug_api_endpoints():
    cfg = read_config()
    endpoints = cfg.get("api_endpoints", [])
    results = []
    for ep in endpoints:
        result = check_endpoint_status(ep.get("url", ""))
        results.append(result)
    return jsonify({"endpoints_status": results})
