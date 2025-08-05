import os
import sys
from flask import (
    Flask,
    request,
    jsonify,
    render_template_string,
    send_file,
    redirect,
    make_response,
    send_from_directory,
    Response
)
import json, zipfile, io, urllib.request
from datetime import datetime

app = Flask(__name__)

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


def agent_log_file(agent: str) -> str:
    return os.path.join(DATA_DIR, f"ai_log_{agent}.json")


def agent_summary_file(agent: str) -> str:
    return os.path.join(DATA_DIR, f"summary_{agent}.txt")


default_agent_config = {
    "model": "llama3",
    "provider": "ollama",
    "template": "default",
    "max_summary_length": 300,
    "step_delay": 10,
    "auto_restart": False,
    "allow_commands": True,
    "controller_active": True,
    "prompt": "",
    "tasks": [],
}

default_config = {
    "agents": {"default": default_agent_config.copy()},
    "active_agent": "default",
    "prompt_templates": {"default": "{task}"},
    "api_endpoints": [
        {"type": "ollama", "url": "http://localhost:11434/api/generate"},
        {"type": "ollama", "url": "http://192.168.178.88:11434/api/generate"},
        {"type": "lmstudio", "url": "http://localhost:1234/v1/completions"},
        {"type": "openai", "url": "https://api.openai.com/v1/chat/completions"},
    ],
    "tasks": [],
    "pipeline_order": [],
}

PROVIDERS = ["ollama", "lmstudio", "openai"]
BOOLEAN_FIELDS = [k for k, v in default_agent_config.items() if isinstance(v, bool)]


def load_team_config(path: str) -> dict:
    """Parse a team configuration file.

    The structure is transformed into the controller's ``default_config`` format.
    If ``path`` does not exist or cannot be parsed the function returns an
    empty dictionary so that the controller can continue with built-in
    defaults.
    """

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}

    # Support both legacy list-based and new dict-based formats
    if isinstance(data.get("agents"), list):
        team_cfg = {"agents": {}, "prompt_templates": {}, "pipeline_order": []}

        for agent in data.get("agents", []):
            role = agent.get("role")
            if not role:
                continue
            cfg = default_agent_config.copy()
            cfg["role"] = role
            model_info = agent.get("model", {})
            if isinstance(model_info, dict):
                cfg["model"] = model_info.get("name", cfg.get("model"))
                cfg["model_info"] = model_info
            elif isinstance(model_info, str):
                cfg["model"] = model_info
            if "purpose" in agent:
                cfg["purpose"] = agent["purpose"]
            if "preferred_hardware" in agent:
                cfg["preferred_hardware"] = agent["preferred_hardware"]
            team_cfg["agents"][role] = cfg
            template = agent.get("prompt_template")
            if template:
                team_cfg["prompt_templates"][role] = template

        team_cfg["pipeline_order"] = data.get("pipeline_order", [])
        return team_cfg

    # Already in controller format
    return {
        "agents": data.get("agents", {}),
        "prompt_templates": data.get("prompt_templates", {}),
        "pipeline_order": data.get("pipeline_order", []),
    }


def read_config():
    os.makedirs(DATA_DIR, exist_ok=True)
    cfg = json.loads(json.dumps(default_config))  # deep copy
    # Load team defaults first
    team_path = os.path.join(DATA_DIR, "default_team_config.json")
    team_cfg = load_team_config(team_path)
    if team_cfg:
        agents = cfg.get("agents", {})
        for name, agent_cfg in team_cfg.get("agents", {}).items():
            merged = default_agent_config.copy()
            merged.update(agent_cfg)
            agents[name] = merged
        cfg["agents"] = agents
        if team_cfg.get("prompt_templates"):
            cfg["prompt_templates"].update(team_cfg["prompt_templates"])
        if team_cfg.get("pipeline_order") is not None:
            cfg["pipeline_order"] = team_cfg.get("pipeline_order", [])
            if cfg["pipeline_order"]:
                cfg["active_agent"] = cfg["pipeline_order"][0]

    # Merge user configuration on top
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            try:
                user_cfg = json.load(f)
            except Exception:
                user_cfg = {}
        agents = cfg.get("agents", {})
        for name, agent_cfg in user_cfg.get("agents", {}).items():
            merged = default_agent_config.copy()
            merged.update(agent_cfg)
            agents[name] = merged
        cfg["agents"] = agents
        if "active_agent" in user_cfg:
            cfg["active_agent"] = user_cfg["active_agent"]
        if "prompt_templates" in user_cfg:
            cfg["prompt_templates"].update(user_cfg["prompt_templates"])
        if "api_endpoints" in user_cfg:
            cfg["api_endpoints"] = user_cfg["api_endpoints"]
        if "tasks" in user_cfg:
            cfg["tasks"] = user_cfg.get("tasks", [])
        if "pipeline_order" in user_cfg:
            cfg["pipeline_order"] = user_cfg.get("pipeline_order", [])
    # Ensure pipeline order contains all agents
    agents_keys = list(cfg.get("agents", {}).keys())
    order = cfg.get("pipeline_order", [])
    for name in agents_keys:
        if name not in order:
            order.append(name)
    cfg["pipeline_order"] = order
    # Persist any new defaults such as newly added fields
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg


def write_config(cfg: dict) -> None:
    """Persist the given configuration to disk."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


config_provider = FileConfig(read_config, write_config)
dashboard_manager = DashboardManager(config_provider, default_agent_config, PROVIDERS)


def fetch_issues(repo: str, token: str | None = None) -> list:
    """Fetch open GitHub issues for the given repository.

    Parameters
    ----------
    repo: "owner/name" string specifying the repository.
    token: Optional GitHub access token for authenticated requests.

    Returns
    -------
    list
        A list of issue dictionaries. Pull requests are filtered out.
    """

    url = f"https://api.github.com/repos/{repo}/issues?state=open"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return []
    return [item for item in data if "pull_request" not in item]


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
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    agent_cfg = cfg.get("agents", {}).get(agent, {}).copy()
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
        {"type": ep.get("type", ""), "url": ep.get("url", "")}
        for ep in endpoints
        if ep.get("url")
    ]
    write_config(cfg)
    return jsonify({"api_endpoints": cfg["api_endpoints"]})


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
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
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
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    return jsonify({"controller_active": agent_cfg["controller_active"]})


@app.route("/agent/<name>/log")
def agent_log(name: str):
    """Return the log content for the given agent."""
    cfg = read_config()
    if name not in cfg.get("agents", {}):
        return ("Agent not found", 404)
    try:
        content = open(agent_log_file(name)).read()[-4000:]
    except Exception:
        content = ""
    return content


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
            "ollama": "http://localhost:11434/api/generate",
            "lmstudio": "http://localhost:1234/v1/completions",
            "openai": "https://api.openai.com/v1/chat/completions"
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