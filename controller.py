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
}

PROVIDERS = ["ollama", "lmstudio", "openai"]
BOOLEAN_FIELDS = [k for k, v in default_agent_config.items() if isinstance(v, bool)]


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
    # Persist any new defaults such as newly added fields
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg


@app.route("/next-config")
def next_config():
    cfg = read_config()
    agent = cfg.get("active_agent", "default")
    agent_cfg = cfg.get("agents", {}).get(agent, {}).copy()
    agent_cfg["agent"] = agent
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


TEMPLATE = """<!doctype html><html><head><title>Agent Controller</title>
<style>{% raw %}body{font-family:sans-serif;padding:2em;}input,textarea{width:100%;margin:4px;}li{margin-bottom:4px;}{% endraw %}</style></head><body>
<h1>🕹 Agents</h1>
<ul>
{% for name, cfg in config['agents'].items() %}
  <li>{% if name == active %}<strong>{% endif %}{{ name }} - {{ cfg['model'] }} via {{ cfg['provider'] }}{% if name == active %}</strong>{% endif %}
    <form method="post" style="display:inline"><input type="hidden" name="set_active" value="{{ name }}"/><button>Aktivieren</button></form>
  </li>
{% endfor %}
</ul>
<form method="post"><input name="new_agent" placeholder="Neuer Agent"/><button>Agent hinzufügen</button></form>

<h2>⚙️ Einstellungen für {{ active }}</h2>
<form method="post">
  <input type="hidden" name="agent" value="{{ active }}"/>
  {% for key,val in agent_cfg.items() if key != 'tasks' %}
    <label>{{ key }}:</label>
    {% if key in boolean_fields %}
      <select name="{{ key }}">
        <option value="True" {% if val %}selected{% endif %}>True</option>
        <option value="False" {% if not val %}selected{% endif %}>False</option>
      </select>
    {% elif key == 'provider' %}
      <select name="provider">
        {% for option in providers %}
          <option value="{{ option }}" {% if val == option %}selected{% endif %}>{{ option }}</option>
        {% endfor %}
      </select>
    {% else %}
      <input name="{{ key }}" value="{{ val }}" />
    {% endif %}
  {% endfor %}
  <label>tasks:</label><textarea name="tasks">{{ agent_cfg['tasks']|join('\n') }}</textarea>
  <button type="submit">✅ Speichern</button>
</form>
<h2>📄 Zusammenfassung</h2><pre>{{ summary }}</pre>
<h2>📝 Letzter Log</h2><pre>{{ log }}</pre>
<form method="post" action="/stop"><button>🛑 Stop Agent</button></form>
<form method="post" action="/restart"><button>♻️ Restart Agent</button></form>
<a href="/export"><button>📦 Export Logs</button></a>
</body></html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
