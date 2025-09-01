from __future__ import annotations

import os
import json
import time
from typing import Optional, List
from flask import Flask, jsonify, request, send_from_directory, redirect
# Optional import of additional controller routes; skip if dependencies missing
try:
    from src.controller import routes as _routes
    _ROUTES_AVAILABLE = True
except Exception:  # pragma: no cover
    _ROUTES_AVAILABLE = False
    _routes = None

# Optional SQLAlchemy/DB imports; allow controller to run without DB installed
try:
    from sqlalchemy import select, text, func
    from sqlalchemy.exc import IntegrityError
    from src.db.sa import (
        session_scope,
        ControllerTask,
        ControllerConfig,
        ControlLog,
        AgentLog,
        ControllerBlacklist,
    )
    _DB_AVAILABLE = True
except Exception:  # pragma: no cover - exercised in envs without sqlalchemy/DB
    from contextlib import contextmanager

    _DB_AVAILABLE = False
    IntegrityError = Exception  # placeholder for type references

    @contextmanager
    def session_scope():  # type: ignore
        # Force DB operations to fail and trigger fallbacks
        raise RuntimeError("db_unavailable")
        yield None  # unreachable

    # Lightweight placeholders to satisfy attribute references
    class _Dummy:  # noqa: D401
        """Placeholder model when DB is unavailable."""
        pass

    class ControllerTask(_Dummy):  # type: ignore
        id = None
        task = None
        agent = None
        template = None

    class ControllerConfig(_Dummy):  # type: ignore
        id = None
        data = {}

    class ControlLog(_Dummy):  # type: ignore
        pass

    class AgentLog(_Dummy):  # type: ignore
        pass

    def select(*args, **kwargs):  # type: ignore
        raise RuntimeError("db_unavailable")

app = Flask(__name__)
# Only register DB-dependent blueprint when explicitly enabled
if _ROUTES_AVAILABLE and _routes is not None and os.environ.get("ENABLE_DB_ROUTES") == "1":
    app.register_blueprint(_routes.blueprint)

# In-memory fallback queue for tasks if DB is unavailable
from collections import deque
from threading import Lock

_FALLBACK_Q = deque()
_FB_LOCK = Lock()

# In-memory config override when DB is unavailable or not initialized
_CONFIG_OVERRIDE: dict | None = None

def _fb_add(task: str, agent: str | None, template: str | None) -> None:
    # Store enqueue timestamp to honor consume delay in fallback mode
    enq_ts = time.time()
    with _FB_LOCK:
        _FALLBACK_Q.append({
            "task": task,
            "agent": agent,
            "template": template,
            "enqueued_at": enq_ts,
        })

def _fb_list(name: str) -> list[dict]:
    with _FB_LOCK:
        return [
            {"task": item.get("task"), "agent": item.get("agent"), "template": item.get("template")}
            for item in _FALLBACK_Q
            if item.get("agent") in (None, name)
        ]

def _fb_pop() -> str | None:
    with _FB_LOCK:
        if _FALLBACK_Q:
            return _FALLBACK_Q.popleft().get("task")
        return None

def _fb_pop_for_agent(name: str | None) -> dict | None:
    """Pop first matching task for the agent from in-memory queue, honoring TASK_CONSUME_DELAY_SECONDS."""
    # Determine delay (seconds) similar to DB-backed logic
    try:
        # Default delay increased to 8s to ensure E2E tests can observe persisted tasks before consumption (fallback)
        delay_sec = int(os.environ.get("TASK_CONSUME_DELAY_SECONDS", "8"))
    except Exception:
        delay_sec = 8
    now = time.time()
    with _FB_LOCK:
        if not _FALLBACK_Q:
            return None
        # find first matching, matured task for agent or None
        for item in list(_FALLBACK_Q):
            if item.get("agent") in (None, name):
                enq = float(item.get("enqueued_at") or 0.0)
                if delay_sec > 0 and (now - enq) < delay_sec:
                    # Not yet eligible for consumption; keep searching for an older eligible item
                    continue
                # Safely remove the first eligible matching item
                try:
                    _FALLBACK_Q.remove(item)
                except ValueError:
                    # Item was not found (possibly removed concurrently)
                    return None
                return item
        return None

# Serve built Vue frontend from FRONTEND_DIST (env) or default /frontend/dist under /ui
_DEFAULT_UI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
_UI_DIR = os.path.abspath(os.environ.get("FRONTEND_DIST", _DEFAULT_UI_DIR))

@app.get("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/status")
def status():
    """Status endpoint for end-to-end tests."""
    return jsonify({"status": "ok"})


@app.get("/")
def root():
    return redirect("/ui/")

@app.get("/ui/")
@app.get("/ui")
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
    # Relaxed CSP suitable for serving the Vue app built assets under /ui
    csp = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self' http://localhost:8081 http://controller:8081 ws: wss:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )
    resp.headers.setdefault("Content-Security-Policy", csp)
    return resp


@app.get("/next-config")
def next_config():
    """Return next task plus config snapshot used by agents/UI.
    Response shape expected by tests: {"agent": str|None, "api_endpoints": list, "prompt_templates": dict}
    """
    def _cfg_from_db() -> dict | None:
        try:
            with session_scope() as s:
                cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
                if cfg and isinstance(cfg.data, dict):
                    return dict(cfg.data)
        except Exception:
            return None
        return None

    data = _cfg_from_db()
    if not data:
        # Prefer in-memory override if present
        if isinstance(_CONFIG_OVERRIDE, dict):
            data = dict(_CONFIG_OVERRIDE)
        else:
            # Load defaults from files (same logic as /config)
            try:
                base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                # Allow tests or deployments to override the config path via ENV
                env_path = os.environ.get("ANANTA_CONFIG_PATH") or os.environ.get("CONFIG_PATH")
                candidates = []
                if env_path:
                    candidates.append(env_path)
                candidates.extend([
                    os.path.join(base_dir, "data", "config.json"),
                    os.path.join(base_dir, "config.json"),
                    os.path.join(base_dir, "data", "default_team_config.json"),
                ])
                for p in candidates:
                    if os.path.isfile(p):
                        with open(p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            break
            except Exception:
                data = {}
    data = data or {}
    # Normalize keys to match tests
    api_eps = data.get("api_endpoints", [])
    prompt_templates = data.get("prompt_templates", data.get("templates", {})) or {}
    agent = data.get("active_agent")
    return jsonify({
        "agent": agent,
        "api_endpoints": api_eps,
        "prompt_templates": prompt_templates,
    })


@app.get("/config")
def get_config():
    # Serve in-memory override if present (e.g., when DB is unavailable)
    global _CONFIG_OVERRIDE
    if isinstance(_CONFIG_OVERRIDE, dict):
        data = dict(_CONFIG_OVERRIDE)
        # ensure defaults
        data.setdefault("agents", {})
        data.setdefault("prompt_templates", data.get("templates", {}))
        data.setdefault("api_endpoints", [])
        data.setdefault("models", [])
        data.setdefault("pipeline_order", list(data.get("agents", {}).keys()))
        data.setdefault("active_agent", None)
        data.setdefault("tasks", [])
        # Inject E2E test models if enabled
        try:
            if str(os.environ.get("ENABLE_E2E_TEST_MODELS", "")).lower() in ("1", "true", "yes"):
                models = list(data.get("models") or [])
                if "m1" not in models:
                    models.append("m1")
                if "m2" not in models:
                    models.append("m2")
                data["models"] = models
        except Exception:
            pass
        return jsonify(data)

    def _normalize_keys(d: dict) -> dict:
        # map legacy key 'templates' to 'prompt_templates' if needed
        if isinstance(d, dict):
            if "prompt_templates" not in d and isinstance(d.get("templates"), dict):
                d = dict(d)
                d["prompt_templates"] = d.pop("templates")
        return d

    def _load_default_config() -> dict:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        env_path = os.environ.get("ANANTA_CONFIG_PATH") or os.environ.get("CONFIG_PATH")
        candidates = []
        if env_path:
            candidates.append(env_path)
        candidates.extend([
            os.path.join(base_dir, "data", "config.json"),
            os.path.join(base_dir, "config.json"),
            os.path.join(base_dir, "data", "default_team_config.json"),
        ])
        for p in candidates:
            try:
                if os.path.isfile(p):
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            data = _normalize_keys(data)
                            # ensure all expected keys exist
                            result = {
                                "agents": data.get("agents", {}),
                                "prompt_templates": data.get("prompt_templates", {}),
                                "api_endpoints": data.get("api_endpoints", []),
                                "models": data.get("models", []),
                                "pipeline_order": data.get("pipeline_order", list(data.get("agents", {}).keys())),
                                "active_agent": data.get("active_agent"),
                            }

                            # Standard-Endpunkttyp für E2E-Tests setzen
                            default_endpoint_type = os.environ.get("DEFAULT_ENDPOINT_TYPE", "")
                            if default_endpoint_type and result["api_endpoints"]:
                                for endpoint in result["api_endpoints"]:
                                    if isinstance(endpoint, dict):
                                        endpoint["type"] = default_endpoint_type
                            # choose a sensible active_agent if missing
                            if not result["active_agent"]:
                                if isinstance(result["pipeline_order"], list) and result["pipeline_order"]:
                                    result["active_agent"] = result["pipeline_order"][0]
                                elif isinstance(result["agents"], dict) and result["agents"]:
                                    result["active_agent"] = next(iter(result["agents"].keys()))
                            return result
            except Exception:
                continue
        # final fallback
        return {"agents": {}, "prompt_templates": {}, "api_endpoints": [], "models": [], "pipeline_order": [], "active_agent": None}

    try:
        with session_scope() as s:
            try:
                cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
                if cfg and isinstance(cfg.data, dict):
                    data = _normalize_keys(dict(cfg.data))
                    # Inject E2E test models if enabled
                    try:
                        if str(os.environ.get("ENABLE_E2E_TEST_MODELS", "")).lower() in ("1", "true", "yes"):
                            models = list(data.get("models") or [])
                            if "m1" not in models:
                                models.append("m1")
                            if "m2" not in models:
                                models.append("m2")
                            data["models"] = models
                    except Exception:
                        pass
                    return jsonify(data)
            except Exception as db_error:
                # Log the specific DB error for debugging
                print(f"DB query error: {db_error}")
                # Try to create the table if it doesn't exist
                try:
                    s.execute(text("""
                        CREATE SCHEMA IF NOT EXISTS controller;
                        CREATE TABLE IF NOT EXISTS controller.config (
                            id SERIAL PRIMARY KEY,
                            data JSONB,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        );
                    """))
                    s.commit()
                    print("Versuchte, Schema controller und Tabelle config zu erstellen")
                except Exception as schema_error:
                    print(f"Schema-Erstellungsversuch fehlgeschlagen: {schema_error}")
            # No DB config yet: serve defaults
            _def = _load_default_config()
            try:
                if str(os.environ.get("ENABLE_E2E_TEST_MODELS", "")).lower() in ("1", "true", "yes"):
                    models = list(_def.get("models") or [])
                    if "m1" not in models:
                        models.append("m1")
                    if "m2" not in models:
                        models.append("m2")
                    _def["models"] = models
            except Exception:
                pass
            return jsonify(_def)
    except Exception as e:
        print(f"Genereller Fehler beim Laden der Konfiguration: {e}")
        # If DB access fails, still try to serve defaults so UI can load
        try:
            _def = _load_default_config()
            try:
                if str(os.environ.get("ENABLE_E2E_TEST_MODELS", "")).lower() in ("1", "true", "yes"):
                    models = list(_def.get("models") or [])
                    if "m1" not in models:
                        models.append("m1")
                    if "m2" not in models:
                        models.append("m2")
                    _def["models"] = models
            except Exception:
                pass
            return jsonify(_def)
        except Exception as e2:
            return jsonify({"error": "internal_error", "detail": str(e2)}), 500


@app.post("/config/api_endpoints")
def update_api_endpoints():
    global _CONFIG_OVERRIDE
    data = request.get_json(silent=True) or {}
    api_eps = data.get("api_endpoints")

    # Accept either a list of endpoint objects or a list of strings (URLs)
    if not isinstance(api_eps, list):
        return jsonify({"error": "invalid_api_endpoints"}), 400

    preserve_strings = all(isinstance(item, str) for item in api_eps)

    try:
        if preserve_strings:
            normalized = [item for item in api_eps if isinstance(item, str)]
        else:
            normalized = []
            for item in api_eps:
                if isinstance(item, dict):
                    url = item.get("url")
                    if not isinstance(url, str) or not url:
                        return jsonify({"error": "invalid_api_endpoints"}), 400
                    normalized.append({
                        "type": item.get("type", ""),
                        "url": url,
                        "models": item.get("models", []) if isinstance(item.get("models", []), list) else []
                    })
                elif isinstance(item, str):
                    # allow mixing, but convert strings to object form
                    normalized.append({"type": "", "url": item, "models": []})
                else:
                    return jsonify({"error": "invalid_api_endpoints"}), 400
    except Exception:
        return jsonify({"error": "invalid_api_endpoints"}), 400

    if len(normalized) > 1000:
        return jsonify({"error": "too_many"}), 400

    try:
        with session_scope() as s:
            # Merge with existing config
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            new_data = {"api_endpoints": normalized, "agents": {}, "prompt_templates": {}}
            if cfg and isinstance(cfg.data, dict):
                new_data = dict(cfg.data)
                new_data["api_endpoints"] = normalized
            s.add(ControllerConfig(data=new_data))
        # Also mirror in in-memory override so GET /config reflects immediately
        _CONFIG_OVERRIDE = dict(new_data)
        return jsonify({"status": "ok"})
    except Exception:
        # No DB: update in-memory override
        base_data = _CONFIG_OVERRIDE if isinstance(_CONFIG_OVERRIDE, dict) else None
        if not base_data:
            # try to load defaults from files
            base_data = {}
            try:
                base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                for p in [
                    os.path.join(base_dir, "data", "config.json"),
                    os.path.join(base_dir, "config.json"),
                    os.path.join(base_dir, "data", "default_team_config.json"),
                ]:
                    if os.path.isfile(p):
                        with open(p, "r", encoding="utf-8") as f:
                            base_data = json.load(f)
                            break
            except Exception:
                base_data = {}
        new_data = dict(base_data)
        new_data["api_endpoints"] = normalized
        _CONFIG_OVERRIDE = new_data
        return jsonify({"status": "ok"})


@app.post("/approve")
def approve():
    payload = request.get_json(silent=True) or {}
    try:
        with session_scope() as s:
            s.add(ControlLog(received=str(payload), summary=None, approved=str(payload)))
    except Exception:
        # If DB is not available, still behave idempotently
        pass
    return jsonify({"status": "approved"})


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
        def _level_to_name(v):
            try:
                v_int = int(v)
            except Exception:
                return str(v)
            return {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "CRITICAL"}.get(v_int, str(v_int))
        with session_scope() as s:
            rows = (
                s.query(AgentLog)
                .filter(AgentLog.agent == name)
                .order_by(AgentLog.created_at.asc())
                .limit(limit)
                .all()
            )
            return jsonify([
                {
                    "agent": r.agent,
                    "level": _level_to_name(r.level),
                    "message": r.message,
                    "timestamp": (r.created_at.isoformat() if getattr(r, "created_at", None) else None),
                }
                for r in rows
            ])
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
    Falls back to default JSON config files if DB is empty/unavailable.
    """
    def _fallback_agents() -> dict:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        candidates = [
            os.path.join(base_dir, "data", "config.json"),
            os.path.join(base_dir, "config.json"),
            os.path.join(base_dir, "data", "default_team_config.json"),
        ]
        for p in candidates:
            try:
                if os.path.isfile(p):
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            return {
                                "active_agent": data.get("active_agent"),
                                "agents": data.get("agents", {}),
                            }
            except Exception:
                continue
        return {"active_agent": None, "agents": {}}

    try:
        with session_scope() as s:
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            if cfg and isinstance(cfg.data, dict):
                data = cfg.data
                return jsonify({
                    "active_agent": data.get("active_agent"),
                    "agents": data.get("agents", {}),
                })
            # No DB config: fallback
            return jsonify(_fallback_agents())
    except Exception:
        try:
            return jsonify(_fallback_agents())
        except Exception as e2:
            return jsonify({"error": "internal_error", "detail": str(e2)}), 500


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
    # Normalize whitespace-only values to None
    if isinstance(agent, str):
        agent = agent.strip()
        if agent == "":
            agent = None
    if isinstance(template, str):
        template = template.strip()
        if template == "":
            template = None
    try:
        with session_scope() as s:
            new_task = ControllerTask(task=task.strip(), agent=agent, template=template)
            s.add(new_task)
            # Ensure ID is generated before we return
            try:
                s.flush()
                new_id = getattr(new_task, "id", None)
            except Exception:
                new_id = None
        resp = {"status": "queued"}
        if new_id is not None:
            resp["id"] = new_id
        return jsonify(resp)
    except Exception:
        # Fallback to in-memory queue when DB is unavailable
        try:
            _fb_add(task.strip(), agent, template)
            return jsonify({"status": "queued"})
        except Exception as e2:
            return jsonify({"error": "internal_error", "detail": str(e2)}), 500


# Compatibility alias for tests expecting /api prefix
@app.post("/api/agent/add_task")
def add_task_api_alias():
    return add_task()


@app.get("/agent/<name>/tasks")
@app.get("/api/agents/<name>/tasks")
def agent_tasks(name: str):
    if not name or len(name) > 128:
        return jsonify({"error": "invalid_name"}), 400
    try:
        with session_scope() as s:
            rows = (
                s.query(ControllerTask)
                .filter((ControllerTask.agent == name) | (ControllerTask.agent.is_(None)))
                .order_by(ControllerTask.id.asc())
                .all()
            )
            return jsonify({
                "tasks": [
                    {"id": r.id, "task": r.task, "agent": r.agent, "template": r.template}
                    for r in rows
                ]
            })
    except Exception:
        # Fallback to in-memory list when DB is unavailable
        try:
            return jsonify({"tasks": _fb_list(name)})
        except Exception as e2:
            return jsonify({"error": "internal_error", "detail": str(e2)}), 500


@app.get("/tasks/next")
def tasks_next():
    """Return and remove the next task for the requested agent (or any).
    Response: {"task": str|null}
    """
    try:
        agent = request.args.get("agent")
    except Exception:
        agent = None
    # Try DB first
    try:
        with session_scope() as s:
            q = s.query(ControllerTask).order_by(ControllerTask.id.asc())
            if agent:
                q = q.filter((ControllerTask.agent == agent) | (ControllerTask.agent.is_(None)))
            else:
                # If no agent provided, prefer tasks without specific agent
                q = q.filter(ControllerTask.agent.is_(None))
            # Apply a small grace period to let UI observe the queued task before it is consumed by the agent
            try:
                # Default delay increased to 8s to ensure E2E tests can observe persisted tasks before consumption
                delay_sec = int(os.environ.get("TASK_CONSUME_DELAY_SECONDS", "8"))
            except Exception:
                delay_sec = 8
            if delay_sec > 0:
                q = q.filter(ControllerTask.created_at <= func.now() - text(f"INTERVAL '{delay_sec} seconds'"))
            row = q.first()
            if not row:
                return jsonify({"task": None})
            task_value = row.task
            s.delete(row)
            return jsonify({"task": task_value})
    except Exception:
        # Fallback to in-memory queue when DB is unavailable
        try:
            item = _fb_pop_for_agent(agent)
            return jsonify({"task": (item.get("task") if item else None)})
        except Exception as e2:
            return jsonify({"error": "internal_error", "detail": str(e2)}), 500


@app.delete("/api/tasks/<int:task_id>")
def delete_task(task_id: int):
    """Delete a task by id for test cleanup.
    Guarded by ENABLE_E2E_TEST_MODELS env flag and task name prefix 'e2e-task-'.
    This prevents accidental deletion of non-test data.
    """
    # Only allow when explicitly enabled for tests
    enabled = str(os.environ.get("ENABLE_E2E_TEST_MODELS", "")).lower() in ("1", "true", "yes")
    if not enabled:
        return jsonify({"error": "forbidden"}), 403
    try:
        with session_scope() as s:
            row = s.query(ControllerTask).filter(ControllerTask.id == task_id).first()
            if not row:
                return jsonify({"error": "not_found"}), 404
            # Only allow deletion of tasks created by tests
            if not isinstance(row.task, str) or not row.task.startswith("e2e-task-"):
                return jsonify({"error": "forbidden"}), 403
            s.delete(row)
            return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


if __name__ == "__main__":
    # Bind to 0.0.0.0:8081 by default so Playwright webServer can reach it
    port = int(os.environ.get("PORT", "8081"))
    app.run(host="0.0.0.0", port=port)


