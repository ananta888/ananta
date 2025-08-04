from flask import Flask, request, jsonify, render_template_string, send_file, redirect
import json, os, zipfile, io
from datetime import datetime

app = Flask(__name__)

# Allow overriding data directory for testing via the DATA_DIR environment variable
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


@app.route("/", methods=["GET", "POST"])
def dashboard():
    if request.method == "POST":
        config = read_config()
        # Task management
        if request.form.get("add_task"):
            text = request.form.get("task_text", "").strip()
            agent_field = request.form.get("task_agent", "").strip() or None
            if text:
                config.setdefault("tasks", []).append({"task": text, "agent": agent_field})
        else:
            to_delete = [
                int(k.split("_")[-1])
                for k in request.form.keys()
                if k.startswith("task_delete_")
            ]
            for idx in sorted(to_delete, reverse=True):
                if 0 <= idx < len(config.get("tasks", [])):
                    config["tasks"].pop(idx)
        # Handle new agent creation
        new_agent = request.form.get("new_agent", "").strip()
        if new_agent and new_agent not in config["agents"]:
            config["agents"][new_agent] = default_agent_config.copy()
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
    try:
        summary = open(agent_summary_file(active)).read()
    except Exception:
        summary = ""
    try:
        log = open(agent_log_file(active)).read()[-4000:]
    except Exception:
        log = "Kein Log"
    return render_template_string(
        TEMPLATE,
        config=cfg,
        active=active,
        agent_cfg=agent_cfg,
        summary=summary,
        log=log,
        providers=PROVIDERS,
        boolean_fields=BOOLEAN_FIELDS,
        pipeline_order=cfg.get("pipeline_order", []),
    )


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


TEMPLATE = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Agent Controller Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background: #f4f6f8; color: #333; }
    header { background: #007BFF; color: #fff; padding: 1em; text-align: center; }
    .container { max-width: 1200px; margin: 0 auto; padding: 1em; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1em; margin-bottom: 2em; }
    .card { background: #fff; border-radius: 8px; box-shadow: 0px 2px 4px rgba(0, 0, 0, 0.1); padding: 1em; transition: transform 0.3s; }
    .card:hover { transform: translateY(-5px); }
    .card.active { border: 2px solid #28a745; }
    h2 { border-bottom: 2px solid #007BFF; padding-bottom: 0.5em; }
    form { background: #fff; padding: 1em; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 2em; }
    label { display: block; margin-top: 1em; }
    input[type="text"], input[type="number"], textarea, select { width: 100%; padding: 0.5em; margin-top: 0.5em; border: 1px solid #ccc; border-radius: 4px; }
    button { padding: 0.5em 1em; margin-top: 1em; background: #007BFF; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
    button:hover { background: #0056b3; }
    .output-pane { background: #e9ecef; padding: 1em; border-left: 4px solid #007BFF; position: fixed; top: 0; right: 0; width: 300px; height: 100%; overflow-y: auto; }
    @media (max-width: 768px) { .output-pane { position: static; width: 100%; } }
  </style>
  <script>
    function attachAjax(form) {
      form.addEventListener('submit', function(e) {
        e.preventDefault();
        fetch(form.action, { method: form.method, body: new FormData(form) })
        .then(response => response.text())
        .then(text => {
          const out = document.getElementById('output');
          out.textContent += (out.textContent ? "\\n" : "") + text;
        });
      });
    }
    window.addEventListener('load', function() {
      document.querySelectorAll('.ajax-form').forEach(attachAjax);
    });
  </script>
</head>
<body>
  <header>
    <h1>Agent Controller Dashboard</h1>
  </header>
  <div class="container">
    <section>
      <h2>Agenten</h2>
      <div class="grid">
        {% for name, cfg in config['agents'].items() %}
          <div class="card {% if name == active %}active{% endif %}">
            <h3>{{ cfg.get('role', name) }}</h3>
            <p><strong>Modell:</strong> {{ cfg['model'] }}<br/>
            <strong>Anbieter:</strong> {{ cfg['provider'] }}</p>
            {% if cfg.get('purpose') %}
              <p><strong>Zweck:</strong> {{ cfg['purpose'] }}</p>
            {% endif %}
            {% if cfg.get('preferred_hardware') %}
              <p><strong>Hardware:</strong> {{ cfg['preferred_hardware'] }}</p>
            {% endif %}
            <form method="post">
              <input type="hidden" name="set_active" value="{{ name }}"/>
              <button type="submit">Aktivieren</button>
            </form>
          </div>
        {% endfor %}
      </div>
      <form method="post">
        <input type="text" name="new_agent" placeholder="Neuer Agent"/>
        <button type="submit">Agent hinzufügen</button>
      </form>
    </section>

    <section>
      <h2>Pipeline Reihenfolge</h2>
      <ol>
        {% for agent in pipeline_order %}
          <li>{{ agent }}</li>
        {% endfor %}
      </ol>
    </section>

    <section>
      <h2>Aufgaben</h2>
      <ul>
        {% for t in config.get('tasks', []) %}
          <li>
            {{ t['task'] }} - {{ t['agent'] or 'auto' }}
            <form method="post" style="display:inline">
              <button name="task_delete_{{ loop.index0 }}" value="1" type="submit">🗑 Löschen</button>
            </form>
          </li>
        {% endfor %}
      </ul>
      <form method="post">
        <input type="text" name="task_text" placeholder="Neue Aufgabe"/>
        <select name="task_agent">
          <option value="">Automatisch</option>
          {% for name in config['agents'].keys() %}
            <option value="{{ name }}">{{ name }}</option>
          {% endfor %}
        </select>
        <button name="add_task" value="1" type="submit">Aufgabe hinzufügen</button>
      </form>
    </section>

    <section>
      <h2>Einstellungen für Agent: {{ active }}</h2>
      <form method="post">
        <input type="hidden" name="agent" value="{{ active }}"/>
        {% for key, val in agent_cfg.items() if key != 'tasks' %}
          <label for="{{ key }}">{{ key }}:</label>
          {% if key in boolean_fields %}
            <select name="{{ key }}" id="{{ key }}">
              <option value="True" {% if val %}selected{% endif %}>True</option>
              <option value="False" {% if not val %}selected{% endif %}>False</option>
            </select>
          {% elif key == 'provider' %}
            <select name="provider" id="{{ key }}">
              {% for option in providers %}
                <option value="{{ option }}" {% if val == option %}selected{% endif %}>{{ option }}</option>
              {% endfor %}
            </select>
          {% elif key == 'template' %}
            <select name="template" id="{{ key }}">
              {% for tname in config['prompt_templates'].keys() %}
                <option value="{{ tname }}" {% if val == tname %}selected{% endif %}>{{ tname }}</option>
              {% endfor %}
            </select>
          {% else %}
            <input type="text" name="{{ key }}" id="{{ key }}" value="{{ val }}" />
          {% endif %}
        {% endfor %}
        <label for="tasks">Aufgaben (jeweils in neuer Zeile):</label>
        <textarea name="tasks" id="tasks">{{ agent_cfg['tasks']|join('\n') }}</textarea>
        <button type="submit">Änderungen speichern</button>
      </form>
    </section>

    <section>
      <h2>API Endpoints</h2>
      <form method="post">
        <input type="hidden" name="api_endpoints_form" value="1" />
        {% for ep in config['api_endpoints'] %}
          <div style="margin-bottom:1em;">
            <select name="endpoint_type_{{ loop.index0 }}">
              {% for option in providers %}
                <option value="{{ option }}" {% if ep['type'] == option %}selected{% endif %}>{{ option }}</option>
              {% endfor %}
            </select>
            <input type="text" name="endpoint_url_{{ loop.index0 }}" value="{{ ep['url'] }}" />
            <button name="endpoint_delete_{{ loop.index0 }}" value="1" type="submit">Löschen</button>
          </div>
        {% endfor %}
        <div style="margin-bottom:1em;">
          <select name="new_endpoint_type">
            {% for option in providers %}
              <option value="{{ option }}">{{ option }}</option>
            {% endfor %}
          </select>
          <input type="text" name="new_endpoint_url" placeholder="Neue URL" />
          <button name="add_endpoint" value="1" type="submit">Hinzufügen</button>
        </div>
        <button type="submit">API Endpoints speichern</button>
      </form>
    </section>

    <section>
      <h2>Prompt Vorlagen</h2>
      <form method="post">
        <textarea name="prompt_templates" style="width:100%; height:150px;">
{{ config['prompt_templates']|tojson(indent=2) }}
        </textarea>
        <button type="submit">Prompt Vorlagen speichern</button>
      </form>
    </section>

    <section>
      <h2>Protokolle & Zusammenfassung</h2>
      <div style="display: flex; gap: 1em;">
        <div style="flex: 1;">
          <h3>Zusammenfassung</h3>
          <pre>{{ summary }}</pre>
        </div>
        <div style="flex: 1;">
          <h3>Letzter Log</h3>
          <pre>{{ log }}</pre>
        </div>
      </div>
    </section>

    <section>
      <h2>Agent Steuerung</h2>
      <form method="post" action="/stop" class="ajax-form" style="display:inline-block;">
        <button type="submit">Agent stoppen</button>
      </form>
      <form method="post" action="/restart" class="ajax-form" style="display:inline-block;">
        <button type="submit">Agent neu starten</button>
      </form>
      <a href="/export"><button type="button">Logs exportieren</button></a>
    </section>
  </div>
  <div id="output" class="output-pane"></div>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)