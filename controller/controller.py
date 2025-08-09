from __future__ import annotations

from flask import Flask, jsonify, request

from src.controller import routes

app = Flask(__name__)
app.register_blueprint(routes.blueprint)

_config = {
    "api_endpoints": [],
    "agents": {},
}
_tasks = []
_templates = {}
_logs = {}


@app.get("/next-config")
def next_config():
    task = _tasks.pop(0) if _tasks else None
    return jsonify({"tasks": [task] if task else [], "templates": _templates})


@app.get("/config")
def get_config():
    return jsonify(_config)


@app.post("/config/api_endpoints")
def update_api_endpoints():
    data = request.get_json(force=True)
    _config["api_endpoints"] = data.get("api_endpoints", [])
    return jsonify({"status": "ok"})


@app.post("/approve")
def approve():
    proposal = request.get_json(force=True)
    _logs.setdefault("approved", []).append(proposal)
    return jsonify({"status": "approved"})


@app.route("/agent/<name>/log", methods=["GET", "DELETE"])
def agent_log(name: str):
    if request.method == "DELETE":
        _logs.pop(name, None)
        return jsonify({"status": "deleted"})
    return jsonify(_logs.get(name, []))


@app.post("/agent/<name>/toggle_active")
def toggle_active(name: str):
    state = _config.setdefault("agents", {}).get(name, True)
    _config["agents"][name] = not state
    return jsonify({"active": _config["agents"][name]})


@app.get("/export")
def export():
    return jsonify({"config": _config, "logs": _logs})


@app.post("/agent/add_task")
def add_task():
    data = request.get_json(force=True)
    task = data.get("task")
    if task:
        _tasks.append(task)
    return jsonify({"status": "queued"})


@app.get("/agent/<name>/tasks")
def agent_tasks(name: str):
    return jsonify({"tasks": list(_tasks)})


if __name__ == "__main__":
    app.run(debug=True)
