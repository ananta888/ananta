from __future__ import annotations

import os
from flask import Flask, jsonify, request, send_from_directory
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.controller import routes
from src.db.sa import (
    session_scope,
    ControllerTask,
    ControllerConfig,
    ControlLog,
    AgentLog,
)

app = Flask(__name__)
app.register_blueprint(routes.blueprint)

# Serve built Vue frontend from frontend/dist under /ui
_UI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))

@app.get("/ui/")
def ui_index():
    if os.path.isdir(_UI_DIR):
        return send_from_directory(_UI_DIR, "index.html")
    return jsonify({"error": "ui_not_built"}), 404

@app.get("/ui/<path:filename>")
def ui_static(filename: str):
    if os.path.isdir(_UI_DIR):
        try:
            return send_from_directory(_UI_DIR, filename)
        except Exception:
            # Fallback to index for SPA routes
            return send_from_directory(_UI_DIR, "index.html")
    return jsonify({"error": "ui_not_built"}), 404


@app.after_request
def set_security_headers(resp):
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    # Adjust CSP as needed; keeping restrictive default
    resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
    return resp


@app.get("/next-config")
def next_config():
    """Return next task (if any) and the latest templates from config.
    Response: {"tasks": [task_or_none], "templates": {...}}
    """
    try:
        with session_scope() as s:
            # Next task: FIFO by id
            task_row = s.query(ControllerTask).order_by(ControllerTask.id.asc()).first()
            task_val = None
            if task_row:
                task_val = task_row.task
                s.delete(task_row)

            # Latest config templates (if present)
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            templates = {}
            if cfg and isinstance(cfg.data, dict):
                templates = cfg.data.get("templates", {}) or {}
            return jsonify({"tasks": [task_val] if task_val else [], "templates": templates})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.get("/config")
def get_config():
    try:
        with session_scope() as s:
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            return jsonify(cfg.data if cfg else {"api_endpoints": [], "agents": {}, "templates": {}})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.post("/config/api_endpoints")
def update_api_endpoints():
    data = request.get_json(silent=True) or {}
    api_eps = data.get("api_endpoints")
    if not isinstance(api_eps, list) or any(not isinstance(x, str) for x in api_eps):
        return jsonify({"error": "invalid_api_endpoints"}), 400
    if len(api_eps) > 1000:
        return jsonify({"error": "too_many"}), 400
    try:
        with session_scope() as s:
            # Merge with existing config
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            new_data = {"api_endpoints": api_eps, "agents": {}, "templates": {}}
            if cfg and isinstance(cfg.data, dict):
                new_data = dict(cfg.data)
                new_data["api_endpoints"] = api_eps
            s.add(ControllerConfig(data=new_data))
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.post("/approve")
def approve():
    payload = request.get_json(silent=True) or {}
    try:
        with session_scope() as s:
            s.add(ControlLog(received=str(payload), summary=None, approved=str(payload)))
        return jsonify({"status": "approved"})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.route("/agent/<name>/log", methods=["GET", "DELETE"])
def agent_log(name: str):
    if not name or len(name) > 128:
        return jsonify({"error": "invalid_name"}), 400
    if request.method == "DELETE":
        try:
            with session_scope() as s:
                s.query(AgentLog).filter(AgentLog.agent == name).delete()
            return jsonify({"status": "deleted"})
        except Exception as e:
            return jsonify({"error": "internal_error", "detail": str(e)}), 500
    # GET with pagination params
    try:
        limit = request.args.get("limit", default=100, type=int)
        limit = max(1, min(limit, 1000))
        with session_scope() as s:
            rows = (
                s.query(AgentLog)
                .filter(AgentLog.agent == name)
                .order_by(AgentLog.created_at.asc())
                .limit(limit)
                .all()
            )
            return jsonify([{"agent": r.agent, "level": r.level, "message": r.message} for r in rows])
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.post("/agent/<name>/toggle_active")
def toggle_active(name: str):
    if not name or len(name) > 128:
        return jsonify({"error": "invalid_name"}), 400
    try:
        with session_scope() as s:
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            data = {"api_endpoints": [], "agents": {}, "templates": {}}
            if cfg and isinstance(cfg.data, dict):
                data = dict(cfg.data)
            agents = data.setdefault("agents", {})
            current = agents.get(name, {}).get("active", True)
            agents[name] = {**agents.get(name, {}), "active": not current}
            s.add(ControllerConfig(data=data))
            return jsonify({"active": agents[name]["active"]})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.get("/export")
def export():
    try:
        with session_scope() as s:
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            # We export last 100 control logs for brevity
            logs = [
                {"received": c.received, "approved": c.approved, "timestamp": c.timestamp.isoformat()}
                for c in s.query(ControlLog).order_by(ControlLog.id.desc()).limit(100).all()
            ]
            return jsonify({"config": (cfg.data if cfg else {}), "logs": logs})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.post("/config/active_agent")
def set_active_agent():
    data = request.get_json(silent=True) or {}
    active_agent = data.get("active_agent")
    if not isinstance(active_agent, str) or len(active_agent) > 128:
        return jsonify({"error": "invalid_active_agent"}), 400
    try:
        with session_scope() as s:
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            new_data = {"api_endpoints": [], "agents": {}, "templates": {}}
            if cfg and isinstance(cfg.data, dict):
                new_data = dict(cfg.data)
            new_data["active_agent"] = active_agent
            s.add(ControllerConfig(data=new_data))
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.get("/agent/config")
def get_agent_config():
    """Return agent-related configuration snapshot.
    Currently proxies controller config 'agents' and 'active_agent'.
    """
    try:
        with session_scope() as s:
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            data = cfg.data if cfg else {}
            return jsonify({
                "active_agent": data.get("active_agent"),
                "agents": data.get("agents", {}),
            })
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.post("/agent/add_task")
def add_task():
    data = request.get_json(silent=True) or {}
    task = data.get("task")
    if not isinstance(task, str) or not task.strip():
        return jsonify({"error": "invalid_task"}), 400
    agent = data.get("agent")
    template = data.get("template")
    if agent is not None and (not isinstance(agent, str) or len(agent) > 128):
        return jsonify({"error": "invalid_agent"}), 400
    if template is not None and (not isinstance(template, str) or len(template) > 128):
        return jsonify({"error": "invalid_template"}), 400
    try:
        with session_scope() as s:
            s.add(ControllerTask(task=task.strip(), agent=agent, template=template))
        return jsonify({"status": "queued"})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.get("/agent/<name>/tasks")
def agent_tasks(name: str):
    if not name or len(name) > 128:
        return jsonify({"error": "invalid_name"}), 400
    try:
        with session_scope() as s:
            rows = (
                s.query(ControllerTask.task)
                .filter((ControllerTask.agent == name) | (ControllerTask.agent.is_(None)))
                .order_by(ControllerTask.id.asc())
                .all()
            )
            return jsonify({"tasks": [r[0] for r in rows]})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
