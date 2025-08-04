import os
from flask import Flask, request, jsonify, render_template_string, send_file, redirect, make_response
import json, zipfile, io
from datetime import datetime

app = Flask(__name__)

# Daten- und Konfigurationsdateien
DATA_DIR = os.environ.get("DATA_DIR", "/data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
CONTROL_LOG = os.path.join(DATA_DIR, "control_log.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.txt")


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


def read_config():
    cfg = json.loads(json.dumps(default_config))  # deep copy
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            try:
                user_cfg = json.load(f)
            except Exception:
                user_cfg = {}
        # Merge agents
        agents = cfg.get("agents", {})
        for name, agent_cfg in user_cfg.get("agents", {}).items():
            merged = default_agent_config.copy()
            merged.update(agent_cfg)
            agents[name] = merged
        cfg["agents"] = agents
        # Merge top-level keys
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
    else:
        # No user config yet: initialise from default team configuration if available
        team_path = os.path.join(os.path.dirname(__file__), "default_team_config.json")
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
    return jsonify(agent_cfg)


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
        config = read_config()
        # Pipeline reordering
        move_agent = request.form.get("move_agent")
        direction = request.form.get("direction")
        if move_agent and direction in ("up", "down"):
            order = config.get("pipeline_order", [])
            if move_agent not in order:
                order.append(move_agent)
            idx = order.index(move_agent)
            if direction == "up" and idx > 0:
                order[idx - 1], order[idx] = order[idx], order[idx - 1]
            elif direction == "down" and idx < len(order) - 1:
                order[idx + 1], order[idx] = order[idx], order[idx + 1]
            config["pipeline_order"] = order
        # Task management
        task_action = request.form.get("task_action")
        task_idx = request.form.get("task_idx")
        tasks = config.setdefault("tasks", [])
        if task_action and task_idx is not None:
            try:
                idx = int(task_idx)
            except ValueError:
                idx = None
            if idx is not None and 0 <= idx < len(tasks):
                if task_action == "move_up" and idx > 0:
                    tasks[idx - 1], tasks[idx] = tasks[idx], tasks[idx - 1]
                elif task_action == "move_down" and idx < len(tasks) - 1:
                    tasks[idx + 1], tasks[idx] = tasks[idx], tasks[idx + 1]
                elif task_action == "start":
                    task = tasks.pop(idx)
                    tasks.insert(0, task)
                elif task_action == "skip":
                    tasks.pop(idx)
        elif request.form.get("add_task"):
            text = request.form.get("task_text", "").strip()
            agent_field = request.form.get("task_agent", "").strip() or None
            if text:
                tasks.append({"task": text, "agent": agent_field})
        # Handle new agent creation
        new_agent = request.form.get("new_agent", "").strip()
        if new_agent and new_agent not in config["agents"]:
            config["agents"][new_agent] = default_agent_config.copy()
            config.setdefault("pipeline_order", []).append(new_agent)
        # Handle active agent switch
        set_active = request.form.get("set_active")
        if set_active and set_active in config["agents"]:
            config["active_agent"] = set_active
        # Update agent configuration
        agent_name = request.form.get("agent") or config.get("active_agent")
        agent_cfg = config["agents"].setdefault(agent_name, default_agent_config.copy())
        for key, default in default_agent_config.items():
            if key == "tasks":
                val = request.form.get("tasks")
                if val is not None:
                    agent_cfg["tasks"] = [t.strip() for t in val.splitlines() if t.strip()]
            else:
                val = request.form.get(key)
                if val is None:
                    continue
                if isinstance(default, bool):
                    agent_cfg[key] = (val or "").lower() == "true"
                elif isinstance(default, int):
                    try:
                        agent_cfg[key] = int(val)
                    except Exception:
                        pass
                elif isinstance(default, str):
                    agent_cfg[key] = val
        # Update API endpoints
        if request.form.get("api_endpoints_form"):
            endpoints = []
            for i, ep in enumerate(config.get("api_endpoints", [])):
                if request.form.get(f"endpoint_delete_{i}"):
                    continue
                typ = request.form.get(f"endpoint_type_{i}") or ep.get("type")
                url = request.form.get(f"endpoint_url_{i}") or ep.get("url")
                if typ and url:
                    endpoints.append({"type": typ, "url": url})
            new_type = request.form.get("new_endpoint_type")
            new_url = request.form.get("new_endpoint_url")
            if request.form.get("add_endpoint") and new_url:
                endpoints.append({"type": new_type or PROVIDERS[0], "url": new_url})
            config["api_endpoints"] = endpoints
        # Update prompt templates
        templates_field = request.form.get("prompt_templates")
        if templates_field is not None:
            try:
                config["prompt_templates"] = json.loads(templates_field)
            except Exception:
                pass
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        return redirect("/")
    cfg = read_config()
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
        current_theme=current_theme
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)