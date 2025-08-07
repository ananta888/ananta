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

LOG_LEVEL = os.environ.get("CONTROLLER_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Register additional controller blueprint routes
try:
    from src.controller.routes import bp as controller_bp
except Exception:  # pragma: no cover - fallback when packaged differently
    from controller.routes import bp as controller_bp  # type: ignore

app.register_blueprint(controller_bp)

# Daten- und Konfigurationsdateien
# Standardmäßig im Projektwurzelverzeichnis, kann über DATA_DIR überschrieben werden
DATA_DIR = os.environ.get(
    "DATA_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
AGENT_URL = os.environ.get("AI_AGENT_URL", "http://localhost:5000")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.txt")
CONTROL_LOG = os.path.join(DATA_DIR, "control_log.json")

# Optional Vue frontend distribution directory
FRONTEND_DIST = os.path.join("/app", "frontend", "dist")
from src.dashboard import DashboardManager, FileConfig
from agent.ai_agent import _http_get, _http_post


PROVIDERS = ["ollama", "lmstudio", "openai"]
BOOLEAN_FIELDS: list[str] = []


def read_config() -> dict:
    """Read configuration via the ai_agent service.

    Schlägt der HTTP-Zugriff fehl, wird eine RuntimeError ausgelöst,
    damit das Frontend einen entsprechenden Fehler anzeigen kann.
    """

    try:
        cfg = _http_get(f"{AGENT_URL}/config", retries=1, delay=0)
    except Exception as exc:  # pragma: no cover - network failure
        logger.error(f"ai-agent service ( {AGENT_URL}/config ) nicht erreichbar: %s", exc)
        raise RuntimeError(f"Der ai-agent-Dienst ( {AGENT_URL}/config ) ist nicht erreichbar.")

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
    """Persist the given configuration via the ai_agent service.

    Wenn der Dienst nicht erreichbar ist, wird die Konfiguration in
    ``CONFIG_FILE`` gespeichert.
    """

    try:
        _http_post(f"{AGENT_URL}/config", cfg, retries=1, delay=0)
    except Exception as exc:  # pragma: no cover - network failure fallback
        logger.warning("write_config fallback to file: %s", exc)
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)

def fetch_file(filename: str) -> str:
    """Helper to fetch file contents from ai_agent."""
    resp = _http_get(f"{AGENT_URL}/file/{filename}")
    if isinstance(resp, dict) and set(resp.keys()) == {"content"}:
        return resp["content"]
    if isinstance(resp, (dict, list)):
        return json.dumps(resp, indent=2)
    return resp


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


@app.route("/next-config")
def next_config():
    cfg = read_config()
    agent = cfg.get("active_agent", "default")
    tasks = cfg.get("tasks", [])
    task_entry = None
    for i, t in enumerate(tasks):
        if t.get("agent") == agent or t.get("agent") in (None, ""):
            task_entry = tasks.pop(i)
            if not task_entry.get("agent"):
                task_entry["agent"] = agent
            break
    cfg["tasks"] = tasks

    # Update agent configuration with current task information
    agents_cfg = cfg.get("agents", {})
    agent_state = agents_cfg.get(agent, {})
    if task_entry:
        agent_state["current_task"] = task_entry.get("task")
    else:
        agent_state.pop("current_task", None)
    agents_cfg[agent] = agent_state
    cfg["agents"] = agents_cfg
    write_config(cfg)

    agent_cfg = agent_state.copy()
    agent_cfg["agent"] = agent
    agent_cfg["api_endpoints"] = cfg.get("api_endpoints", [])
    agent_cfg["prompt_templates"] = cfg.get("prompt_templates", {})
    agent_cfg["tasks"] = [task_entry["task"]] if task_entry else agent_cfg.get("tasks", [])
    if task_entry and task_entry.get("template"):
        agent_cfg["template"] = task_entry.get("template")
    return jsonify(agent_cfg)


@app.route("/config")
def full_config():
    """Return the complete controller configuration as JSON."""
    return jsonify(read_config())


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
    cmd = request.form.get("cmd", "").strip()
    summary = request.form.get("summary", "").strip()
    try:
        return _http_post(
            f"{AGENT_URL}/approve",
            {"cmd": cmd, "summary": summary},
            form=True,
            retries=1,
            delay=0,
        )
    except Exception:
        os.makedirs(os.path.dirname(BLACKLIST_FILE), exist_ok=True)
        with open(BLACKLIST_FILE, "a+") as f:
            f.seek(0)
            if any(line.strip() == cmd for line in f):
                return "SKIP"
            f.write(cmd + "\n")
        os.makedirs(os.path.dirname(CONTROL_LOG), exist_ok=True)
        with open(CONTROL_LOG, "a", encoding="utf-8") as f:
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
        tasks = cfg.setdefault("tasks", [])
        for issue in issues:
            title = issue.get("title", "")
            number = issue.get("number")
            url = issue.get("html_url")
            text = f"Issue #{number}: {title} ({url})"
            if not any(t.get("task") == text for t in tasks):
                tasks.append({"task": text})
        write_config(cfg)
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
    for idx, t in enumerate(cfg.get("tasks", [])):
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
    try:
        summary = fetch_file(f"summary_{active}.txt")
    except Exception:
        summary = ""
    try:
        log = _http_get(f"{AGENT_URL}/agent/{active}/log")
        if isinstance(log, dict):
            log = ""
        else:
            log = log[-4000:]
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
    entry = {"task": task}
    agent_name = (data.get("agent") or "").strip()
    if agent_name:
        entry["agent"] = agent_name
    template = (data.get("template") or "").strip()
    if template:
        entry["template"] = template
    cfg = read_config()
    cfg.setdefault("tasks", []).append(entry)
    write_config(cfg)
    logger.info("Added task '%s' for agent '%s'", task, agent_name or "auto")
    return jsonify({"added": entry}), 201


@app.route("/agent/<name>/log")
def agent_log(name: str):
    """Return the log content for the given agent."""
    try:
        data = _http_get(
            f"{AGENT_URL}/agent/{name}/log", retries=1, delay=0
        )
    except Exception:
        log_path = os.path.join(DATA_DIR, f"ai_log_{name}.json")
        if not os.path.exists(log_path):
            return ("", 404)
        with open(log_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        return Response(content, mimetype="text/plain")
    if isinstance(data, dict) and data.get("error") == "agent not found":
        return ("", 404)
    return Response(str(data), mimetype="text/plain")


@app.route("/agent/<name>/tasks")
def agent_tasks(name: str):
    """Return current and pending tasks for the given agent."""
    cfg = read_config()
    agents = cfg.get("agents", {})
    tasks = [t for t in cfg.get("tasks", []) if t.get("agent") == name]
    current = agents.get(name, {}).get("current_task")
    return jsonify({"agent": name, "current_task": current, "tasks": tasks})


@app.route("/stop", methods=["POST"])
def stop():
    _http_post(f"{AGENT_URL}/stop", {})
    return "OK"


@app.route("/restart", methods=["POST"])
def restart():
    _http_post(f"{AGENT_URL}/restart", {})
    return "OK"


@app.route("/export")
def export_logs():
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w") as zf:
        cfg = read_config()
        zf.writestr("config.json", json.dumps(cfg, indent=2))
        try:
            zf.writestr("control_log.json", fetch_file("control_log.json"))
        except Exception:
            pass
        try:
            zf.writestr("blacklist.txt", fetch_file("blacklist.txt"))
        except Exception:
            pass
        for name in cfg.get("agents", {}):
            try:
                zf.writestr(f"ai_log_{name}.json", fetch_file(f"ai_log_{name}.json"))
            except Exception:
                pass
            try:
                zf.writestr(f"summary_{name}.txt", fetch_file(f"summary_{name}.txt"))
            except Exception:
                pass
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="export.zip", mimetype="application/zip")


@app.route("/ui")
def ui_index():
    """Serve the Vue frontend if it has been built."""
    if os.path.exists(os.path.join(FRONTEND_DIST, "index.html")):
        return send_from_directory(FRONTEND_DIST, "index.html")
    return "Frontend not built: " + os.path.join(FRONTEND_DIST, "index.html"), 404


@app.route("/ui/<path:path>")
def ui_static(path):
    if os.path.exists(os.path.join(FRONTEND_DIST, path)):
        return send_from_directory(FRONTEND_DIST, path)
    return "Frontend not built", 404

# (Weitere Importe und bereits vorhandener Code)

# Neue Funktion zum Überprüfen eines Endpoints
def check_endpoint_status(url: str, timeout: float = 2.0) -> dict:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout):
            # Bei erfolgreichem HEAD-Request
            return {"url": url, "status": "OK"}
    except Exception as e:
        return {"url": url, "status": f"Fehler: {e}"}

# Neuer Endpoint zur Abfrage des LLM-Status
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
# In controller/controller.py

def check_endpoint_status(url: str, timeout: float = 2.0) -> dict:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout):
            return {"url": url, "status": "OK"}
    except Exception as e:
        logger.debug("Fehler beim Erreichen der Adresse %s: %s", url, e)
        return {"url": url, "status": f"Fehler: {e}"}

# Beispiel: Nutzung in einer Debug-Route
@app.route("/debug/api_endpoints", methods=["GET"])
def debug_api_endpoints():
    cfg = read_config()
    endpoints = cfg.get("api_endpoints", [])
    results = []
    for ep in endpoints:
        result = check_endpoint_status(ep.get("url", ""))
        results.append(result)
    return jsonify({"endpoints_status": results})
