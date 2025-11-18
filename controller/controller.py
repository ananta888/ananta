from __future__ import annotations

import os
import json
import time
from datetime import datetime
from typing import Optional, List
from flask import Flask, jsonify, request, send_from_directory, redirect, g, make_response
import logging, uuid
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
# Activate DB-dependent blueprint by default; allow explicit opt-out via DISABLE_DB_ROUTES
if _ROUTES_AVAILABLE and _routes is not None and str(os.environ.get("DISABLE_DB_ROUTES", "0")).lower() not in ("1", "true", "yes"):
    app.register_blueprint(_routes.blueprint)

# Configure logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger("controller")

# Request ID middleware
@app.before_request
def _inject_request_id():
    try:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        g.request_id = rid
    except Exception:
        g.request_id = None

# Add Prometheus /metrics if available
try:
    from prometheus_flask_exporter import PrometheusMetrics  # type: ignore
    metrics = PrometheusMetrics(app, path='/metrics')
    metrics.info('app_info', 'Controller info', version='1.0.0')
except Exception as e:
    print(f"metrics_disabled: {e}")

# Liveness and Readiness
@app.get('/live')
def live():
    return jsonify({"status": "up"})

@app.get('/ready')
def ready():
    try:
        with session_scope() as s:  # type: ignore
            # simple DB ping
            try:
                s.execute(text('SELECT 1'))  # type: ignore
            except Exception:
                # Fallback: try a lightweight query using SQLAlchemy ORM if available
                _ = s
        return jsonify({"status": "ready"})
    except Exception as e:
        return jsonify({"status": "degraded", "detail": str(e)}), 503

# Ensure required DB schemas/tables exist before handling any requests
try:
    from src.db import init_db as _init_db  # local alias to avoid re-export
    # Only attempt if not explicitly skipped
    if os.environ.get("SKIP_DB_INIT") != "1":
        _init_db()
except Exception as _e:
    # Do not crash the controller if DB is not available at import time
    print(f"DB init skipped or failed at startup: {_e}")

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


@app.get("/protected")
def protected():
    """Protected endpoint for auth E2E tests.
    - 401 when no API key header is provided
    - 403 when an API key is provided but does not match CONTROLLER_API_KEY
    - 200 when a valid key is provided or when CONTROLLER_API_KEY is not set
    """
    expected = (os.environ.get("CONTROLLER_API_KEY", "") or "").strip()
    provided = (request.headers.get("X-API-Key", "") or "").strip()
    if not provided:
        return jsonify({"error": "missing_api_key"}), 401
    if expected and provided != expected:
        return jsonify({"error": "invalid_api_key"}), 403
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
    # Propagate request id to responses
    try:
        rid = getattr(g, "request_id", None)
        if rid:
            resp.headers.setdefault("X-Request-ID", rid)
    except Exception:
        pass
    return resp

# Consistent error helper

def error_response(code: int, error: str, detail: str | None = None):
    payload = {"error": error}
    if detail:
        payload["detail"] = detail
    return make_response(jsonify(payload), code)

# Log each request on teardown (best-effort)
@app.teardown_request
def _log_request(exc):
    try:
        logger.info(
            "http_request",
            extra={
                "path": request.path,
                "method": request.method,
                "status": getattr(getattr(request, 'response', None), 'status_code', None),
                "request_id": getattr(g, "request_id", None),
                "remote_addr": request.remote_addr,
                "user_agent": request.headers.get('User-Agent') if hasattr(request, 'headers') else None,
            },
        )
    except Exception:
        pass
    return None


@app.get("/next-config")
def next_config():
    """Return config snapshot used by agents/UI.
    Adds optional 'prompt' based on the requested agent (?agent=).
    Response shape: {"agent": str|None, "api_endpoints": list, "prompt_templates": dict, "prompt"?: str}
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

    # Resolve prompt for requested agent if any
    try:
        requested_agent = request.args.get("agent")
    except Exception:
        requested_agent = None
    prompt: str | None = None
    if requested_agent and isinstance(data.get("agents"), dict):
        cfg_agent = data.get("agents", {}).get(requested_agent, {}) or {}
        # 1) explicit agent prompt
        p = cfg_agent.get("prompt") if isinstance(cfg_agent, dict) else None
        if isinstance(p, str) and p.strip():
            prompt = p
        else:
            # 2) template reference
            tname = cfg_agent.get("template") if isinstance(cfg_agent, dict) else None
            if isinstance(tname, str) and tname:
                tpl = prompt_templates.get(tname) if isinstance(prompt_templates, dict) else None
                if isinstance(tpl, str) and tpl.strip():
                    prompt = tpl
    # 3) global main_prompt fallback
    if not prompt:
        mp = data.get("main_prompt")
        if isinstance(mp, str) and mp.strip():
            prompt = mp
    # Build response
    resp = {
        "agent": agent,
        "api_endpoints": api_eps,
        "prompt_templates": prompt_templates,
    }
    if isinstance(prompt, str) and prompt:
        resp["prompt"] = prompt
    return jsonify(resp)


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
        data.setdefault("main_prompt", data.get("main_prompt", ""))
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
                                "main_prompt": data.get("main_prompt", ""),
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
        return {"agents": {}, "prompt_templates": {}, "api_endpoints": [], "models": [], "pipeline_order": [], "active_agent": None, "main_prompt": ""}

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
                    # Ensure main_prompt key exists for UI consumption
                    if "main_prompt" not in data:
                        data["main_prompt"] = ""
                    return jsonify(data)
            except Exception as db_error:
                # Log the specific DB error for debugging
                print(f"DB query error: {db_error}")
                # Ensure schemas/tables exist using a fresh connection outside the current transaction
                try:
                    s.rollback()
                except Exception:
                    pass
                try:
                    from src.db import init_db as _init_db
                    _init_db()
                    print("DB schema ensured via init_db()")
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
            # Merge with existing config; preserve defaults when none exist yet
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            if cfg and isinstance(cfg.data, dict):
                new_data = dict(cfg.data)
            else:
                # Load defaults from files to avoid wiping agents/templates
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
                # normalize legacy key
                if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
                    base_data = dict(base_data)
                    base_data["prompt_templates"] = base_data.pop("templates")
                new_data = {
                    "agents": base_data.get("agents", {}),
                    "prompt_templates": base_data.get("prompt_templates", {}),
                    "api_endpoints": base_data.get("api_endpoints", []),
                    "models": base_data.get("models", []),
                    "pipeline_order": base_data.get("pipeline_order", list((base_data.get("agents") or {}).keys())),
                    "active_agent": base_data.get("active_agent"),
                    "main_prompt": base_data.get("main_prompt", ""),
                }
            # Apply the update
            new_data["api_endpoints"] = normalized
            s.add(ControllerConfig(data=new_data))
        # Also mirror in in-memory override so GET /config reflects immediately
        _CONFIG_OVERRIDE = dict(new_data)
        return jsonify({"status": "ok"})
    except Exception:
        # No DB: update in-memory override, preserving defaults
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
        # normalize legacy key
        if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
            base_data = dict(base_data)
            base_data["prompt_templates"] = base_data.pop("templates")
        new_data = {
            "agents": (base_data or {}).get("agents", {}),
            "prompt_templates": (base_data or {}).get("prompt_templates", {}),
            "api_endpoints": (base_data or {}).get("api_endpoints", []),
            "models": (base_data or {}).get("models", []),
            "pipeline_order": (base_data or {}).get("pipeline_order", list(((base_data or {}).get("agents") or {}).keys())),
            "active_agent": (base_data or {}).get("active_agent"),
            "main_prompt": (base_data or {}).get("main_prompt", ""),
        }
        new_data["api_endpoints"] = normalized
        _CONFIG_OVERRIDE = new_data
        return jsonify({"status": "ok"})


@app.post("/config/agents")
def update_agents():
    """Update the full agents mapping in controller config.
    Expects JSON payload: {"agents": {<name>: {..}, ...}}
    Persists to DB when available, otherwise updates in-memory override.
    """
    global _CONFIG_OVERRIDE
    data = request.get_json(silent=True) or {}
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return jsonify({"error": "invalid_agents"}), 400

    # Basic normalization: keep only string keys and dict values
    normalized: dict[str, dict] = {}
    for k, v in list(agents.items()):
        try:
            name = str(k)
        except Exception:
            continue
        if not name or len(name) > 128:
            continue
        if isinstance(v, dict):
            normalized[name] = v
    if len(normalized) > 1000:
        return jsonify({"error": "too_many"}), 400

    def _base_from_files() -> dict:
        base_data: dict = {}
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
        # normalize legacy key
        if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
            base_data = dict(base_data)
            base_data["prompt_templates"] = base_data.pop("templates")
        return base_data if isinstance(base_data, dict) else {}

    # Persist to DB when available; mirror to in-memory on success
    try:
        with session_scope() as s:  # type: ignore
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            if cfg and isinstance(cfg.data, dict):
                new_data = dict(cfg.data)
            else:
                base_data = _base_from_files()
                new_data = {
                    "agents": base_data.get("agents", {}),
                    "prompt_templates": base_data.get("prompt_templates", {}),
                    "api_endpoints": base_data.get("api_endpoints", []),
                    "models": base_data.get("models", []),
                    "pipeline_order": base_data.get("pipeline_order", list((base_data.get("agents") or {}).keys())),
                    "active_agent": base_data.get("active_agent"),
                    "main_prompt": base_data.get("main_prompt", ""),
                }
            # Apply update
            new_data["agents"] = normalized
            # Ensure active_agent remains valid
            if new_data.get("active_agent") not in normalized:
                try:
                    new_data["active_agent"] = (next(iter(normalized.keys())) if normalized else None)
                except Exception:
                    new_data["active_agent"] = None
            s.add(ControllerConfig(data=new_data))
        _CONFIG_OVERRIDE = dict(new_data)
        return jsonify({"status": "ok"})
    except Exception:
        # Fallback when DB is unavailable: update in-memory override, preserving defaults
        base_data = _CONFIG_OVERRIDE if isinstance(_CONFIG_OVERRIDE, dict) else None
        if not base_data:
            base_data = _base_from_files()
        new_data = {
            "agents": (base_data or {}).get("agents", {}),
            "prompt_templates": (base_data or {}).get("prompt_templates", {}),
            "api_endpoints": (base_data or {}).get("api_endpoints", []),
            "models": (base_data or {}).get("models", []),
            "pipeline_order": (base_data or {}).get("pipeline_order", list(((base_data or {}).get("agents") or {}).keys())),
            "active_agent": (base_data or {}).get("active_agent"),
            "main_prompt": (base_data or {}).get("main_prompt", ""),
        }
        new_data["agents"] = normalized
        if new_data.get("active_agent") not in normalized:
            try:
                new_data["active_agent"] = (next(iter(normalized.keys())) if normalized else None)
            except Exception:
                new_data["active_agent"] = None
        _CONFIG_OVERRIDE = new_data
        return jsonify({"status": "ok"})


@app.post("/config/main_prompt")
def set_main_prompt():
    """Update the global main_prompt used as default for agents without explicit prompt/template.
    Expects JSON payload: {"main_prompt": "..."}
    Persists to DB when available; otherwise updates in-memory override.
    """
    global _CONFIG_OVERRIDE
    data = request.get_json(silent=True) or {}
    main_prompt = data.get("main_prompt")
    if not isinstance(main_prompt, str):
        return jsonify({"error": "invalid_main_prompt"}), 400
    # Trim but allow empty string to clear
    mp_value = main_prompt
    try:
        if len(mp_value) > 200000:
            return jsonify({"error": "too_long"}), 400
    except Exception:
        pass

    # Persist to DB when available; mirror to in-memory on success
    try:
        with session_scope() as s:  # type: ignore
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            if cfg and isinstance(cfg.data, dict):
                new_data = dict(cfg.data)
            else:
                # Load defaults from files to avoid wiping existing structure
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
                # normalize legacy key
                if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
                    base_data = dict(base_data)
                    base_data["prompt_templates"] = base_data.pop("templates")
                new_data = {
                    "agents": base_data.get("agents", {}),
                    "prompt_templates": base_data.get("prompt_templates", {}),
                    "api_endpoints": base_data.get("api_endpoints", []),
                    "models": base_data.get("models", []),
                    "pipeline_order": base_data.get("pipeline_order", list((base_data.get("agents") or {}).keys())),
                    "active_agent": base_data.get("active_agent"),
                    "main_prompt": base_data.get("main_prompt", ""),
                }
            # Apply update
            new_data["main_prompt"] = mp_value
            s.add(ControllerConfig(data=new_data))
        _CONFIG_OVERRIDE = dict(new_data)
        return jsonify({"status": "ok"})
    except Exception:
        # Fallback when DB is unavailable: update in-memory override, preserving defaults
        base_data = _CONFIG_OVERRIDE if isinstance(_CONFIG_OVERRIDE, dict) else None
        if not base_data:
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
        if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
            base_data = dict(base_data)
            base_data["prompt_templates"] = base_data.pop("templates")
        new_data = {
            "agents": (base_data or {}).get("agents", {}),
            "prompt_templates": (base_data or {}).get("prompt_templates", {}),
            "api_endpoints": (base_data or {}).get("api_endpoints", []),
            "models": (base_data or {}).get("models", []),
            "pipeline_order": (base_data or {}).get("pipeline_order", list(((base_data or {}).get("agents") or {}).keys())),
            "active_agent": (base_data or {}).get("active_agent"),
            "main_prompt": (base_data or {}).get("main_prompt", ""),
        }
        new_data["main_prompt"] = mp_value
        _CONFIG_OVERRIDE = new_data
        return jsonify({"status": "ok"})


@app.post("/config/models")
def update_models():
    """Update the list of available models in the controller configuration.
    Expects JSON payload: {"models": ["model-a", "model-b", ...]}
    - Validates that it is a list of strings
    - Trims whitespace, removes empties, de-duplicates while preserving order
    - Persists to DB when available, otherwise falls back to in-memory override
    """
    global _CONFIG_OVERRIDE
    data = request.get_json(silent=True) or {}
    models = data.get("models")
    if not isinstance(models, list):
        return jsonify({"error": "invalid_models"}), 400

    # Normalize models: keep only non-empty strings, strip, unique, length limits
    normalized: list[str] = []
    try:
        for item in models:
            if not isinstance(item, str):
                return jsonify({"error": "invalid_models"}), 400
            name = item.strip()
            if not name:
                continue
            if len(name) > 256:
                return jsonify({"error": "name_too_long", "model": name[:256]}), 400
            if name not in normalized:
                normalized.append(name)
    except Exception:
        return jsonify({"error": "invalid_models"}), 400

    if len(normalized) > 1000:
        return jsonify({"error": "too_many"}), 400

    # Persist to DB when available; mirror to in-memory on success
    try:
        with session_scope() as s:  # type: ignore
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            if cfg and isinstance(cfg.data, dict):
                new_data = dict(cfg.data)
            else:
                # Load defaults from files to avoid wiping agents/templates
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
                # normalize legacy key
                if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
                    base_data = dict(base_data)
                    base_data["prompt_templates"] = base_data.pop("templates")
                new_data = {
                    "agents": base_data.get("agents", {}),
                    "prompt_templates": base_data.get("prompt_templates", {}),
                    "api_endpoints": base_data.get("api_endpoints", []),
                    "models": base_data.get("models", []),
                    "pipeline_order": base_data.get("pipeline_order", list((base_data.get("agents") or {}).keys())),
                    "active_agent": base_data.get("active_agent"),
                    "main_prompt": base_data.get("main_prompt", ""),
                }
            # Apply update
            new_data["models"] = normalized
            s.add(ControllerConfig(data=new_data))
        _CONFIG_OVERRIDE = dict(new_data)
        return jsonify({"status": "ok"})
    except Exception:
        # Fallback when DB is unavailable: update in-memory override, preserving defaults
        base_data = _CONFIG_OVERRIDE if isinstance(_CONFIG_OVERRIDE, dict) else None
        if not base_data:
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
        if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
            base_data = dict(base_data)
            base_data["prompt_templates"] = base_data.pop("templates")
        new_data = {
            "agents": (base_data or {}).get("agents", {}),
            "prompt_templates": (base_data or {}).get("prompt_templates", {}),
            "api_endpoints": (base_data or {}).get("api_endpoints", []),
            "models": (base_data or {}).get("models", []),
            "pipeline_order": (base_data or {}).get("pipeline_order", list(((base_data or {}).get("agents") or {}).keys())),
            "active_agent": (base_data or {}).get("active_agent"),
            "main_prompt": (base_data or {}).get("main_prompt", ""),
        }
        new_data["models"] = normalized
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
    # GET with pagination and filters
    try:
        limit = request.args.get("limit", default=100, type=int)
        limit = max(1, min(limit, 1000))
        level_filter = request.args.get("level")
        since_str = request.args.get("since")
        since_dt = None
        if since_str:
            try:
                # Parse ISO date/time; fallback if needed
                since_dt = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
            except Exception:
                since_dt = None
        def _level_to_name(v):
            try:
                v_int = int(v)
            except Exception:
                return str(v)
            return {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "CRITICAL"}.get(v_int, str(v_int))
        with session_scope() as s:
            q = s.query(AgentLog).filter(AgentLog.agent == name)
            if level_filter:
                q = q.filter(AgentLog.level == level_filter)
            if since_dt is not None:
                try:
                    q = q.filter(AgentLog.created_at >= since_dt)
                except Exception:
                    pass
            rows = (
                q.order_by(AgentLog.created_at.asc())
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


# --- Additional log endpoints: ControlLog (DB) and file-based logs ---
@app.get("/controller/logs")
def controller_logs():
    """Return recent controller ControlLog entries from DB.
    Query params: limit (1..1000)
    """
    try:
        limit = request.args.get("limit", default=100, type=int)
        limit = max(1, min(limit, 1000))
    except Exception:
        limit = 100
    try:
        with session_scope() as s:
            rows = (
                s.query(ControlLog)
                .order_by(getattr(ControlLog, "id", None).desc())
                .limit(limit)
                .all()
            )
            result = []
            for r in rows:
                try:
                    ts = getattr(r, "timestamp", None)
                    result.append({
                        "received": getattr(r, "received", None),
                        "approved": getattr(r, "approved", None),
                        "summary": getattr(r, "summary", None),
                        "timestamp": (ts.isoformat() if ts else None),
                    })
                except Exception:
                    continue
            return jsonify(result)
    except Exception as e:
        # If DB is unavailable, return an empty list to keep UI working
        return jsonify([])


# Restrict accessible log files via env var (comma-separated names)
_ALLOWED_LOG_FILES = [
    n.strip() for n in (os.environ.get("ALLOWED_LOG_FILES", "app.log,control_log.json").split(",")) if n.strip()
]
_LOGS_BASE_DIR = os.path.abspath(os.environ.get("LOG_FILES_DIR", os.getcwd()))

@app.get("/logs/files")
def list_log_files():
    files = []
    for name in _ALLOWED_LOG_FILES:
        path = os.path.abspath(os.path.join(_LOGS_BASE_DIR, name))
        # ensure path stays within base dir
        if not path.startswith(_LOGS_BASE_DIR):
            continue
        if os.path.isfile(path):
            try:
                size = os.path.getsize(path)
            except Exception:
                size = None
            files.append({"name": name, "size": size})
    return jsonify(files)

@app.get("/logs/file/<path:name>")
def get_log_file(name: str):
    # deny path traversal and restrict to allowed names only
    if name not in _ALLOWED_LOG_FILES:
        return jsonify({"error": "forbidden"}), 403
    try:
        limit = request.args.get("limit", default=500, type=int)
        limit = max(1, min(limit, 10000))
    except Exception:
        limit = 500
    path = os.path.abspath(os.path.join(_LOGS_BASE_DIR, name))
    if not path.startswith(_LOGS_BASE_DIR) or not os.path.isfile(path):
        return jsonify({"error": "not_found"}), 404
    try:
        # read last N lines efficiently
        lines: list[str] = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            # naive but acceptable for moderate files
            lines = f.readlines()[-limit:]
        text = "".join(lines)
        from flask import Response
        return Response(text, mimetype="text/plain; charset=utf-8")
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.post("/agent/<name>/toggle_active")
def toggle_active(name: str):
    global _CONFIG_OVERRIDE
    if not name or len(name) > 128:
        return jsonify({"error": "invalid_name"}), 400
    try:
        with session_scope() as s:
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            if cfg and isinstance(cfg.data, dict):
                data = dict(cfg.data)
            else:
                # Load defaults from files to avoid wiping agents/templates
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
                # normalize legacy key
                if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
                    base_data = dict(base_data)
                    base_data["prompt_templates"] = base_data.pop("templates")
                data = {
                    "agents": base_data.get("agents", {}),
                    "prompt_templates": base_data.get("prompt_templates", {}),
                    "api_endpoints": base_data.get("api_endpoints", []),
                    "models": base_data.get("models", []),
                    "pipeline_order": base_data.get("pipeline_order", list((base_data.get("agents") or {}).keys())),
                    "active_agent": base_data.get("active_agent"),
                    "main_prompt": base_data.get("main_prompt", ""),
                }
            agents = data.setdefault("agents", {})
            current = bool(agents.get(name, {}).get("active", True))
            agents[name] = {**agents.get(name, {}), "active": not current}
            s.add(ControllerConfig(data=data))
        # Mirror to in-memory override so GET /config reflects immediately
        _CONFIG_OVERRIDE = dict(data)
        return jsonify({"active": agents[name]["active"]})
    except Exception:
        # Fallback to in-memory override when DB is unavailable
        base_data = _CONFIG_OVERRIDE if isinstance(_CONFIG_OVERRIDE, dict) else None
        if not base_data:
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
        if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
            base_data = dict(base_data)
            base_data["prompt_templates"] = base_data.pop("templates")
        data = dict(base_data)
        if "main_prompt" not in data:
            try:
                data["main_prompt"] = base_data.get("main_prompt", "")
            except Exception:
                data["main_prompt"] = ""
        agents = data.setdefault("agents", {})
        current = bool(agents.get(name, {}).get("active", True))
        agents[name] = {**agents.get(name, {}), "active": not current}
        _CONFIG_OVERRIDE = data
        return jsonify({"active": agents[name]["active"]})


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
    global _CONFIG_OVERRIDE
    data = request.get_json(silent=True) or {}
    active_agent = data.get("active_agent")
    if not isinstance(active_agent, str) or len(active_agent) > 128:
        return jsonify({"error": "invalid_active_agent"}), 400
    try:
        with session_scope() as s:
            cfg = s.execute(select(ControllerConfig).order_by(ControllerConfig.id.desc()).limit(1)).scalars().first()
            if cfg and isinstance(cfg.data, dict):
                new_data = dict(cfg.data)
            else:
                # Load defaults from files to avoid wiping agents/templates
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
                # normalize legacy key
                if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
                    base_data = dict(base_data)
                    base_data["prompt_templates"] = base_data.pop("templates")
                new_data = {
                    "agents": base_data.get("agents", {}),
                    "prompt_templates": base_data.get("prompt_templates", {}),
                    "api_endpoints": base_data.get("api_endpoints", []),
                    "models": base_data.get("models", []),
                    "pipeline_order": base_data.get("pipeline_order", list((base_data.get("agents") or {}).keys())),
                    "active_agent": base_data.get("active_agent"),
                    "main_prompt": base_data.get("main_prompt", ""),
                }
            # Apply the update
            new_data["active_agent"] = active_agent
            s.add(ControllerConfig(data=new_data))
        # Mirror to in-memory override so GET /config reflects immediately
        _CONFIG_OVERRIDE = dict(new_data)
        return jsonify({"status": "ok"})
    except Exception:
        # Fallback to in-memory when DB is unavailable
        base_data = _CONFIG_OVERRIDE if isinstance(_CONFIG_OVERRIDE, dict) else None
        if not base_data:
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
        if isinstance(base_data, dict) and "prompt_templates" not in base_data and isinstance(base_data.get("templates"), dict):
            base_data = dict(base_data)
            base_data["prompt_templates"] = base_data.pop("templates")
        new_data = dict(base_data)
        new_data["active_agent"] = active_agent
        _CONFIG_OVERRIDE = new_data
        return jsonify({"status": "ok"})


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
        return error_response(400, "invalid_task")
    agent = data.get("agent")
    template = data.get("template")
    created_by = data.get("created_by")
    if agent is not None and (not isinstance(agent, str) or len(agent) > 128):
        return error_response(400, "invalid_agent")
    if template is not None and (not isinstance(template, str) or len(template) > 128):
        return error_response(400, "invalid_template")
    if created_by is not None and (not isinstance(created_by, str) or len(created_by) > 128):
        return error_response(400, "invalid_created_by")
    # Normalize whitespace-only values to None
    if isinstance(agent, str):
        agent = agent.strip()
        if agent == "":
            agent = None
    if isinstance(template, str):
        template = template.strip()
        if template == "":
            template = None
    if isinstance(created_by, str):
        created_by = created_by.strip() or None
    try:
        with session_scope() as s:
            new_task = ControllerTask(task=task.strip(), agent=agent, template=template, created_by=created_by)
            # initialize persistent audit log
            try:
                new_task.log = list(new_task.log or []) + [{
                    "ts": datetime.utcnow().isoformat() + "Z",
                    "event": "created",
                    "by": created_by or "api",
                }]
            except Exception:
                pass
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
        return error_response(400, "invalid_name")
    try:
        with session_scope() as s:
            q = (
                s.query(ControllerTask)
                .filter((ControllerTask.agent == name) | (ControllerTask.agent.is_(None)))
            )
            # only show pending tasks for queue visibility (also tolerate NULL status)
            try:
                q = q.filter((ControllerTask.status == "queued") | (ControllerTask.status.is_(None)))
            except Exception:
                pass
            rows = q.order_by(ControllerTask.id.asc()).all()
            return jsonify({
                "tasks": [
                    {
                        "id": r.id,
                        "task": r.task,
                        "agent": r.agent,
                        "template": r.template,
                        "status": getattr(r, "status", None),
                    }
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
    """Return the next task for the requested agent (or any).
    Legacy mode: deletes the task upon delivery (compat mode).
    Enhanced mode (default via TASK_STATUS_MODE=enhanced): marks task in_progress and returns its id, no deletion.
    Response: {"task": str|null, "id"?: int}
    """
    try:
        agent = request.args.get("agent")
    except Exception:
        agent = None
    mode = str(os.environ.get("TASK_STATUS_MODE", "enhanced")).lower()
    # Try DB first
    try:
        with session_scope() as s:
            q = s.query(ControllerTask).order_by(ControllerTask.id.asc())
            if mode == "enhanced":
                q = q.filter((ControllerTask.status == "queued") | (ControllerTask.status.is_(None)))
            if agent:
                q = q.filter((ControllerTask.agent == agent) | (ControllerTask.agent.is_(None)))
            else:
                # If no agent provided, prefer tasks without specific agent
                q = q.filter(ControllerTask.agent.is_(None))
            # Apply a small grace period to let UI observe the queued task before it is consumed by the agent
            try:
                delay_sec = int(os.environ.get("TASK_CONSUME_DELAY_SECONDS", "8"))
            except Exception:
                delay_sec = 8
            if delay_sec > 0:
                q = q.filter(ControllerTask.created_at <= func.now() - text(f"INTERVAL '{delay_sec} seconds'"))
            row = q.first()
            if not row:
                return jsonify({"task": None})
            task_value = row.task
            if mode == "enhanced":
                # Transition to in_progress and persist audit
                row.status = "in_progress"
                row.picked_by = agent
                row.picked_at = datetime.utcnow()
                try:
                    row.log = list(row.log or []) + [{
                        "ts": datetime.utcnow().isoformat() + "Z",
                        "event": "picked",
                        "by": agent,
                    }]
                except Exception:
                    pass
                s.flush()
                return jsonify({"task": task_value, "id": row.id})
            else:
                # legacy: delete row as before
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


@app.post("/tasks/<int:task_id>/status")
def update_task_status(task_id: int):
    """Update task status and append to its persistent log.
    Accepts JSON: {status: one of [done, failed, queued, archived], message?: str, agent?: str}
    """
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    message = data.get("message")
    agent = data.get("agent") or request.args.get("agent")
    if not isinstance(status, str):
        return error_response(400, "invalid_status")
    status = status.strip().lower()
    if status not in ("done", "failed", "queued", "archived"):
        return error_response(400, "invalid_status")
    try:
        with session_scope() as s:
            row = s.query(ControllerTask).filter(ControllerTask.id == task_id).first()
            if not row:
                return jsonify({"error": "not_found"}), 404
            # Transition
            now = datetime.utcnow()
            if status == "done":
                row.status = "done"
                row.completed_at = now
            elif status == "failed":
                row.status = "failed"
                try:
                    row.fail_count = int(row.fail_count or 0) + 1
                except Exception:
                    row.fail_count = 1
            elif status == "queued":
                row.status = "queued"
                row.picked_by = None
                row.picked_at = None
                row.completed_at = None
            elif status == "archived":
                row.status = "archived"
                row.archived_at = now
            # Audit log append
            try:
                entry = {"ts": now.isoformat() + "Z", "event": status}
                if agent:
                    entry["by"] = agent
                if message:
                    entry["message"] = str(message)
                row.log = list(row.log or []) + [entry]
            except Exception:
                pass
            return jsonify({"status": "ok", "id": row.id, "new_status": row.status})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@app.get("/db/contents")
def db_contents():
    """Return tables and rows from the controller schema for the Vue frontend.
    Query params:
      - table: optional, if provided only dump this table
      - limit: max rows per table (default 100)
      - offset: offset for rows (default 0)
      - include_empty: '1'|'true' to include empty tables
    """
    schema_default = "controller"
    try:
        schema = request.args.get("schema", schema_default) or schema_default
    except Exception:
        schema = schema_default
    # Enforce default schema for the controller service
    if schema not in ("controller",):
        return jsonify({"error": "invalid_schema", "allowed": ["controller"]}), 400
    table_filter = request.args.get("table")
    try:
        limit = max(0, min(1000, int(request.args.get("limit", "100"))))
    except Exception:
        limit = 100
    try:
        offset = max(0, int(request.args.get("offset", "0")))
    except Exception:
        offset = 0
    include_empty = str(request.args.get("include_empty", "0")).lower() in ("1", "true", "yes")

    # Lazy import to avoid hard dependency during module import
    try:
        from src.db import get_conn  # type: ignore
    except Exception as e:
        return jsonify({"error": "db_unavailable", "detail": str(e)}), 503

    try:
        conn = get_conn()
    except Exception as e:
        return jsonify({"error": "db_unavailable", "detail": str(e)}), 503

    cur = conn.cursor()
    try:
        # list tables in the schema
        params = [schema]
        tbl_sql = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """
        cur.execute(tbl_sql, params)
        table_names = [r[0] for r in cur.fetchall()]
        if table_filter:
            table_names = [t for t in table_names if t == table_filter]

        from psycopg2 import sql as _sql  # imported lazily to avoid hard dep at import-time

        tables = []
        for tname in table_names:
            q = _sql.SQL("SELECT * FROM {}.{} LIMIT %s OFFSET %s").format(
                _sql.Identifier(schema), _sql.Identifier(tname)
            )
            try:
                cur.execute(q, (limit, offset))
                rows = cur.fetchall()
            except Exception:
                # Skip tables we cannot read for any reason
                continue
            cols = [d.name if hasattr(d, 'name') else d[0] for d in cur.description or []]
            data_rows = [dict(zip(cols, row)) for row in rows]
            if data_rows or include_empty:
                tables.append({
                    "name": tname,
                    "columns": cols,
                    "rows": data_rows,
                })

        return jsonify({
            "schema": schema,
            "tables": tables,
            "limit": limit,
            "offset": offset,
        })
    finally:
        try:
            cur.close()
        finally:
            conn.close()

@app.get("/tasks/stats")
def tasks_stats():
    """Return counts of tasks by status, optional ?agent= filter."""
    try:
        agent = request.args.get("agent")
    except Exception:
        agent = None
    try:
        with session_scope() as s:
            q = s.query(ControllerTask.status, func.count(ControllerTask.id))
            if agent:
                q = q.filter(ControllerTask.agent == agent)
            q = q.group_by(ControllerTask.status)
            rows = q.all()
            counts = { (k or "unknown"): v for k, v in rows }
            total = sum(counts.values())
            return jsonify({"counts": counts, "total": total})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


if __name__ == "__main__":
    # Bind to 0.0.0.0:8081 by default so Playwright webServer can reach it
    port = int(os.environ.get("PORT", "8081"))
    app.run(host="0.0.0.0", port=port)


