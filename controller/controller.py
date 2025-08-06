import os
import shutil
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
CONTROL_LOG = os.path.join(DATA_DIR, "control_log.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.txt")

# Optional Vue frontend distribution directory
FRONTEND_DIST = os.path.join("/app", "frontend", "dist")
from src.dashboard import DashboardManager, FileConfig
from agent.ai_agent import _http_get


def agent_log_file(agent: str) -> str:
    return os.path.join(DATA_DIR, f"ai_log_{agent}.json")


def agent_summary_file(agent: str) -> str:
    return os.path.join(DATA_DIR, f"summary_{agent}.txt")
PROVIDERS = ["ollama", "lmstudio", "openai"]
BOOLEAN_FIELDS: list[str] = []


def read_config():
    """Read configuration from disk.

    If ``config.json`` does not exist, it is initialised either empty or from
    ``default_team_config.json`` if that file is available. The function also
    ensures that the pipeline order contains all defined agents and updates the
    global ``BOOLEAN_FIELDS`` list based on the loaded configuration.
    """

    os.makedirs(DATA_DIR, exist_ok=True)

    # Make sure a default team config is present in DATA_DIR for initialisation
    team_path = os.path.join(DATA_DIR, "default_team_config.json")
    if not os.path.exists(team_path):
        repo_team = os.path.join(os.path.dirname(__file__), "..", "default_team_config.json")
        if os.path.exists(repo_team):
            shutil.copy(repo_team, team_path)

    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            try:
                cfg = json.load(f)
            except Exception:
                cfg = {}
    else:
        if os.path.exists(team_path):
            with open(team_path, "r", encoding="utf-8") as f:
                try:
                    cfg = json.load(f)
                except Exception:
                    cfg = {}
        else:
            cfg = {}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

    agents_keys = list(cfg.get("agents", {}).keys())
    order = cfg.get("pipeline_order", [])
    for name in agents_keys:
        if name not in order:
            order.append(name)
    cfg["pipeline_order"] = order

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    global BOOLEAN_FIELDS
    BOOLEAN_FIELDS = sorted({
        k for agent in cfg.get("agents", {}).values() for k, v in agent.items() if isinstance(v, bool)
    })

    return cfg


def write_config(cfg: dict) -> None:
    """Persist the given configuration to disk."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


config_provider = FileConfig(read_config, write_config)
_initial_cfg = config_provider.read()
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
    with open(BLACKLIST_FILE, "a+") as f:
        f.seek(0)
        if any(line.strip() == cmd for line in f):
            return "SKIP"
        f.write(cmd + "\n")
    with open(CONTROL_LOG, "a") as f:
        json.dump({
            "received": cmd,
            "summary": summary,
            "approved": "OK",
            "timestamp": datetime.utcnow().isoformat()
        }, f)
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
        summary = open(agent_summary_file(active)).read()
    except Exception:
        summary = ""
    try:
        log = open(agent_log_file(active)).read()[-4000:]
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
    agent_name = name
    try:
        upstream = _http_get(f"/agent/{agent_name}/log")
    except Exception:
        abort(502)
    if upstream.status_code == 404:
        return ("", 404)
    resp = make_response(upstream.content)
    resp.headers["Content-Type"] = upstream.headers.get("Content-Type", "text/plain")
    return resp


@app.route("/stop", methods=["POST"])
def stop():
    open(f"{DATA_DIR}/stop.flag", "w").close()
    return "OK"


@app.route("/restart", methods=["POST"])
def restart():
    try:
        os.remove(f"{DATA_DIR}/stop.flag")
    except Exception:
        pass
    return "OK"


@app.route("/export")
def export_logs():
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w") as zf:
        cfg = read_config()
        zf.write(CONFIG_FILE, arcname="config.json")
        if os.path.exists(CONTROL_LOG):
            zf.write(CONTROL_LOG, arcname="control_log.json")
        if os.path.exists(BLACKLIST_FILE):
            zf.write(BLACKLIST_FILE, arcname="blacklist.txt")
        for name in cfg.get("agents", {}):
            log = agent_log_file(name)
            summary = agent_summary_file(name)
            if os.path.exists(log):
                zf.write(log, arcname=os.path.basename(log))
            if os.path.exists(summary):
                zf.write(summary, arcname=os.path.basename(summary))
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
    cfg = {}
    config_path = os.path.join(os.environ.get("DATA_DIR", os.getcwd()), "config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            try:
                cfg = json.load(f)
            except Exception:
                cfg = {}
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
        print(f"DEBUG: Fehler beim Erreichen der Adresse {url}: {e}")
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