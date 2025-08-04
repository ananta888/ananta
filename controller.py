from flask import Flask, request, jsonify, render_template_string, send_file, redirect
import json, os, zipfile, io
from datetime import datetime

app = Flask(__name__)

# Allow overriding data directory for testing via the DATA_DIR environment variable
DATA_DIR = os.environ.get("DATA_DIR", "/data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LOG_FILE = os.path.join(DATA_DIR, "ai_log.json")
CONTROL_LOG = os.path.join(DATA_DIR, "control_log.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.txt")
SUMMARY_FILE = os.path.join(DATA_DIR, "summary.txt")

default_config = {
    "model": "llama3",
    "max_summary_length": 300,
    "step_delay": 10,
    "auto_restart": False,
    "allow_commands": True,
    "controller_active": True,
    "prompt": "",
}


def read_config():
    cfg = default_config.copy()
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            try:
                user_cfg = json.load(f)
            except Exception:
                user_cfg = {}
        cfg.update(user_cfg)
    # Persist any new defaults such as newly added fields
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg


@app.route("/next-config")
def next_config():
    cfg = read_config()
    return jsonify(cfg)


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
        for key in default_config:
            val = request.form.get(key)
            if isinstance(default_config[key], bool):
                config[key] = (val or "").lower() == "true"
            elif isinstance(default_config[key], int):
                try:
                    config[key] = int(val)
                except Exception:
                    pass
            elif isinstance(default_config[key], str):
                if val is not None:
                    config[key] = val
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        return redirect("/")
    cfg = read_config()
    try:
        summary = open(SUMMARY_FILE).read()
    except Exception:
        summary = ""
    try:
        log = open(LOG_FILE).read()[-4000:]
    except Exception:
        log = "Kein Log"
    return render_template_string(TEMPLATE, config=cfg, summary=summary, log=log)


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
        for name in [
            "config.json",
            "ai_log.json",
            "control_log.json",
            "blacklist.txt",
            "summary.txt",
        ]:
            path = os.path.join(DATA_DIR, name)
            if os.path.exists(path):
                zf.write(path, arcname=name)
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="export.zip", mimetype="application/zip")


TEMPLATE = """<!doctype html><html><head><title>Agent Controller</title>
<style>{% raw %}body{font-family:sans-serif;padding:2em;}input{width:100%;margin:4px;}{% endraw %}</style></head><body>
<h1>🕹 Konfiguration</h1>
<form method="post">
  {% for key,val in config.items() %}
    <label>{{ key }}:</label><input name="{{ key }}" value="{{ val }}" />
  {% endfor %}
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
