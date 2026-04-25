import concurrent.futures
import json
import os
import threading
import time
from queue import Empty, Queue
from urllib.parse import urlparse

import psutil
from flask import Blueprint, Response, current_app, g, jsonify, request

from agent.auth import admin_required, check_auth, rotate_token
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.common.http import get_default_client
from agent.config import settings
from agent.db_models import AgentInfoDB, AuditLogDB, StatsSnapshotDB
from agent.metrics import CONTENT_TYPE_LATEST, CPU_USAGE, RAM_USAGE, generate_latest
from agent.models import (
    AgentRegisterRequest,
    SystemHealthReadModel,
    TaskExecutionPolicyContract,
    TaskStepExecuteRequest,
    TaskStepProposeRequest,
)
from agent.services.repository_registry import get_repository_registry
from agent.routes.tasks.orchestration_policy import normalize_capabilities, normalize_worker_roles
from agent.services.reference_profile_service import get_reference_profile_service
from agent.services.service_registry import get_core_services
from agent.services.system_contract_service import get_system_contract_service
from agent.services.system_health_service import build_system_health_payload
from agent.utils import rate_limit, read_json, validate_request, write_json

# Historie fÃ¼r Statistiken (wird jetzt in DB gespeichert)
STATS_HISTORY = []  # Nur noch als Fallback oder temporÃ¤rer Cache


def _repos():
    return get_repository_registry()


agent_repo = get_repository_registry().agent_repo


def _load_history(app):
    """Migriert alte JSON-Historie in die Datenbank falls vorhanden."""
    path = app.config.get("STATS_HISTORY_PATH", "data/stats_history.json")
    if os.path.exists(path):
        try:
            old_data = read_json(path, [])
            if old_data:
                _log().info("Migriere %s Statistik-Snapshots in die Datenbank...", len(old_data))
                for item in old_data:
                    snapshot = StatsSnapshotDB(
                        timestamp=item.get("timestamp", time.time()),
                        agents=item.get("agents", {}),
                        tasks=item.get("tasks", {}),
                        shell_pool=item.get("shell_pool", {}),
                        resources=item.get("resources", {}),
                    )
                    _repos().stats_repo.save(snapshot)

                # Datei umbenennen um Doppelmigration zu verhindern
                os.rename(path, f"{path}.bak")
                _log().info("Migration abgeschlossen. Alte Datei in .bak umbenannt.")
        except Exception as e:
            _log().error("Fehler bei der Migration der Statistik-Historie: %s", e)


def _save_history(app):
    """Veraltet: Wird jetzt direkt in record_stats via DB erledigt."""
    pass


def _schedule_process_restart(delay_seconds: float = 0.4) -> None:
    def _restart_later() -> None:
        time.sleep(max(0.1, float(delay_seconds)))
        os._exit(0)

    threading.Thread(target=_restart_later, daemon=True).start()


system_bp = Blueprint("system", __name__)
http_client = get_default_client()

# Pub/Sub fÃ¼r System-Events
_system_subscribers = []
_system_subscribers_lock = threading.Lock()
_agent_health_failures: dict[str, int] = {}
_agent_health_lock = threading.Lock()
_agent_offline_failure_threshold = 3


def _services():
    return get_core_services()


def _log():
    return _services().log_service.bind(__name__)


def _runtime_default_provider() -> str:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    return str(cfg.get("default_provider") or settings.default_provider or "").strip().lower()


def _runtime_provider_urls() -> dict:
    return current_app.config.get("PROVIDER_URLS", {}) or {}


def _notify_system_event(event_type: str, data: dict):
    with _system_subscribers_lock:
        for q in _system_subscribers:
            q.put({"type": event_type, "data": data, "timestamp": time.time()})


@system_bp.route("/events", methods=["GET"])
@check_auth
def stream_system_events():
    def generate():
        q = Queue()
        with _system_subscribers_lock:
            _system_subscribers.append(q)

        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except Empty:
                    yield ": keep-alive\n\n"
        finally:
            with _system_subscribers_lock:
                if q in _system_subscribers:
                    _system_subscribers.remove(q)

    return Response(generate(), mimetype="text/event-stream")


@system_bp.route("/audit/analyze", methods=["POST"])
@admin_required
@rate_limit(limit=10, window=60, namespace="system_audit_analyze")
def analyze_audit_logs():
    """
    Audit-Logs mittels LLM auf verdÃ¤chtige Muster analysieren
    ---
    tags:
      - Security
    security:
      - Bearer: []
    responses:
      200:
        description: Analyse-Ergebnis
    """
    limit = request.args.get("limit", 50, type=int)
    logs = _repos().audit_repo.get_all(limit=limit)

    if not logs:
        return api_response(data={"analysis": "Keine Audit-Logs zur Analyse vorhanden."})

    log_text = "\n".join(
        [
            (
                f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(log_entry.timestamp))}] "
                f"User: {log_entry.username}, IP: {log_entry.ip}, Action: {log_entry.action}, "
                f"Details: {json.dumps(log_entry.details)}"
            )
            for log_entry in logs
        ]
    )

    prompt = f"""Analysiere die folgenden Audit-Logs auf verdÃ¤chtige Muster, Brute-Force-Angriffe,
unbefugte ZugriffsbemÃ¼hungen oder ungewÃ¶hnliches Verhalten.
Gib eine kurze EinschÃ¤tzung und weise auf kritische Punkte hin.

Audit-Logs:
{log_text}

Analyse:"""

    from agent.llm_integration import _call_llm

    cfg = current_app.config["AGENT_CONFIG"]

    try:
        analysis = _call_llm(
            provider=cfg.get("provider", "ollama"),
            model=cfg.get("model", "llama3"),
            prompt=prompt,
            urls=current_app.config["PROVIDER_URLS"],
            api_key=current_app.config["OPENAI_API_KEY"],
        )
        log_audit("audit_logs_analyzed", {"limit": limit})
        return api_response(data={"analysis": analysis})
    except Exception as e:
        return api_response(status="error", message=str(e), code=500)


@system_bp.route("/health", methods=["GET"])
def health():
    """
    Health Check des Agenten
    ---
    responses:
      200:
        description: Status des Agenten und der Subsysteme
        schema:
          properties:
            status:
              type: string
            agent:
              type: string
            checks:
              type: object
    """
    basic_mode = request.args.get("basic", "").strip().lower() in {"1", "true", "yes"}
    return api_response(data=build_system_health_payload(current_app, basic_mode=basic_mode))


@system_bp.route("/contracts", methods=["GET"])
@check_auth
def contract_catalog():
    return api_response(data=get_system_contract_service().build_contract_catalog())


@system_bp.route("/openapi.json", methods=["GET"])
@check_auth
def openapi_document():
    return api_response(data=get_system_contract_service().build_openapi_document())


@system_bp.route("/reference-profiles/catalog", methods=["GET"])
@check_auth
def reference_profile_catalog():
    return api_response(data=get_reference_profile_service().build_catalog_read_model())


@system_bp.route("/ready", methods=["GET"])
def readiness_check():
    """
    Readiness Check des Agenten
    ---
    responses:
      200:
        description: Agent ist bereit
      503:
        description: Agent oder AbhÃ¤ngigkeiten nicht bereit
    """
    results = {}
    is_ready = True
    agent_name = current_app.config.get("AGENT_NAME")
    runtime_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    runtime_provider = str(runtime_cfg.get("default_provider") or settings.default_provider or "").strip().lower()
    provider_urls = dict(current_app.config.get("PROVIDER_URLS", {}) or {})

    def _check_hub():
        base = (settings.hub_url or "http://localhost:5000").rstrip("/")
        candidates = [f"{base}/health"]

        # Fallbacks: robust gegen fehlerhafte/hostseitige HUB_URL Werte in Containern.
        if agent_name == "hub":
            candidates.append("http://localhost:5000/health")
        if "localhost" in base:
            candidates.append("http://ai-agent-hub:5000/health")

        seen = set()
        checked = []
        for url in candidates:
            if url in seen:
                continue
            seen.add(url)
            checked.append(url)
            try:
                start = time.time()
                res = http_client.get(url, timeout=settings.http_timeout, return_response=True, silent=True)
                if res is not None:
                    return "hub", {
                        "status": "ok" if res.status_code < 500 else "unstable",
                        "latency": round(time.time() - start, 3),
                        "code": res.status_code,
                        "url": url,
                    }
            except Exception:
                continue

        return "hub", {"status": "error", "message": "No response from hub", "attempted_urls": checked}

    def _check_llm():
        provider = runtime_provider
        url = provider_urls.get(provider)
        if not url:
            return "llm", None

        if provider == "lmstudio":
            from agent.llm_integration import probe_lmstudio_runtime

            probe = probe_lmstudio_runtime(url, timeout=settings.http_timeout)
            if probe["ok"]:
                return "llm", {
                    "provider": provider,
                    "status": "ok" if probe["candidate_count"] > 0 else "unstable",
                    "latency": None,
                    "code": 200,
                    "base_url": probe.get("base_url") or url,
                    "models_url": probe["models_url"],
                    "candidate_count": probe["candidate_count"],
                    "probe_status": probe.get("status"),
                }
            message = f"No response from LLM provider {provider}"
            if probe.get("status") == "invalid_url":
                message = f"Invalid LM Studio base URL: {url}"
            elif probe.get("models_url"):
                message = f"No response from LM Studio models endpoint {probe.get('models_url')}"
            return "llm", {
                "provider": provider,
                "status": "error",
                "message": message,
                "base_url": probe.get("base_url") or url,
                "models_url": probe.get("models_url"),
                "candidate_count": int(probe.get("candidate_count") or 0),
                "probe_status": probe.get("status"),
            }

        try:
            start = time.time()
            res = http_client.get(url, timeout=settings.http_timeout, return_response=True, silent=True)
            if res:
                return "llm", {
                    "provider": provider,
                    "status": "ok" if res.status_code < 500 else "unstable",
                    "latency": round(time.time() - start, 3),
                    "code": res.status_code,
                }
            else:
                return "llm", {"status": "error", "message": f"No response from LLM provider {provider}"}
        except Exception as e:
            return "llm", {"status": "error", "message": str(e)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_check_hub), executor.submit(_check_llm)]
        for future in concurrent.futures.as_completed(futures):
            key, result = future.result()
            if result:
                results[key] = result
                if result.get("status") == "error":
                    is_ready = False

    return api_response(
        data={"ready": is_ready, "checks": results},
        status="success" if is_ready else "error",
        code=200 if is_ready else 503,
    )


@system_bp.route("/metrics", methods=["GET"])
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


@system_bp.route("/register", methods=["POST"])
@rate_limit(limit=20, window=60, namespace="system_register")
@validate_request(AgentRegisterRequest)
def register_agent():
    data = g.validated_data.model_dump()
    normalized, error, code = _services().agent_registry_service.validate_registration_payload(
        data,
        registration_token=settings.registration_token,
    )
    if error:
        return api_response(status="error", message=error, code=code)

    assert normalized is not None
    reachable, validation_error = _services().agent_registry_service.validate_agent_endpoint(
        url=str(normalized.get("url") or ""),
        http_client=http_client,
        timeout=min(settings.http_timeout, 5.0),
    )
    if not reachable:
        return api_response(status="error", message=validation_error or "agent_validation_failed", code=400)

    agent = _services().agent_registry_service.build_registered_agent(normalized)
    agent_repo.save(agent)
    _log().info("Agent registriert: %s (%s)", agent.name, agent.url)
    return api_response(
        data={
            "status": "registered",
            "agent": _services().agent_registry_service.build_directory_entry(
                agent=agent,
                timeout=getattr(settings, "agent_offline_timeout", 300),
                now=time.time(),
            ),
        }
    )


@system_bp.route("/agents", methods=["GET"])
@check_auth
def list_agents():
    """Liste der Agenten. Fallback auf Datei-basierten Speicher im Testmodus."""
    agents = agent_repo.get_all()
    now = time.time()
    timeout = getattr(settings, "agent_offline_timeout", 300)

    if agents:
        stale = _services().agent_registry_service.mark_stale_agents_offline(agents=agents, timeout=timeout, now=now)
        for agent in stale:
            agent_repo.save(agent)
            _log().info("Agent %s ist jetzt offline (letzte Meldung vor %ss)", agent.name, round(now - agent.last_seen))
        return api_response(
            data=[
                _services().agent_registry_service.build_directory_entry(agent=a, timeout=timeout, now=now)
                for a in agents
            ]
        )

    # Fallback: Datei-basiert (fÃ¼r Tests, die read_json/write_json mocken)
    try:
        agents_path = current_app.config.get("AGENTS_PATH", "data/agents.json")
        file_agents = read_json(agents_path, {}) or {}
        changed = False
        # Struktur: {name: {url, status, last_seen, token?}}
        for name, info in file_agents.items():
            status = info.get("status", "offline")
            last_seen = info.get("last_seen", 0)
            if status == "online" and (now - last_seen > timeout):
                info["status"] = "offline"
                changed = True
        if changed:
            write_json(agents_path, file_agents)
        return jsonify(file_agents), 200
    except Exception as e:
        _log().error("Fehler beim Laden der Agenten (Fallback): %s", e)
        return api_response(status="error", message="could not load agents", code=500)


@system_bp.route("/terminal/restart-session", methods=["POST"])
@check_auth
def restart_terminal_session():
    payload = request.get_json(silent=True) or {}
    forward_param = str(payload.get("forward_param") or payload.get("session_id") or "").strip()
    if not forward_param:
        return api_response(status="error", message="missing_forward_param", code=400)
    result = get_core_services().live_terminal_session_service.restart(forward_param)
    if not result.get("ok"):
        return api_response(status="error", message=str(result.get("message") or "terminal_restart_failed"), data=result, code=404)
    log_audit("terminal_session_restarted", {"session_id": forward_param, "agent_name": current_app.config.get("AGENT_NAME")})
    return api_response(data=result)


@system_bp.route("/restart-process", methods=["POST"])
@check_auth
def restart_process():
    if not os.path.exists("/.dockerenv"):
        return api_response(status="error", message="worker_restart_not_supported", code=409)
    log_audit("worker_process_restart_requested", {"agent_name": current_app.config.get("AGENT_NAME")})
    _schedule_process_restart()
    return api_response(data={"scheduled": True, "restart_mode": "process_exit", "agent_name": current_app.config.get("AGENT_NAME")})


@system_bp.route("/rotate-token", methods=["POST"])
@admin_required
def do_rotate_token():
    new_token = rotate_token()
    log_audit("agent_token_rotated", {"agent_name": current_app.config.get("AGENT_NAME")})
    _notify_system_event("token_rotated", {"new_token": new_token})
    return api_response(data={"status": "rotated", "new_token": new_token})


def _get_resource_usage():
    """Gibt CPU und RAM Verbrauch des aktuellen Prozesses zurÃ¼ck."""
    try:
        process = psutil.Process(os.getpid())
        cpu = process.cpu_percent(interval=None)
        ram = process.memory_info().rss
        # Update Prometheus Metrics
        CPU_USAGE.set(cpu)
        RAM_USAGE.set(ram)
        return {"cpu_percent": cpu, "ram_bytes": ram}
    except Exception as e:
        _log().error("Error getting resource usage: %s", e)
        return {"cpu_percent": 0, "ram_bytes": 0}


@system_bp.route("/stats", methods=["GET"])
@check_auth
def system_stats():
    """
    Aggregierte Statistiken fÃ¼r das Dashboard
    """
    return api_response(
        data=_services().system_stats_service.build_system_stats_read_model(
            agent_name=current_app.config.get("AGENT_NAME")
        )
    )


@system_bp.route("/stats/history", methods=["GET"])
@check_auth
def get_stats_history():
    """
    Gibt die Historie der Statistiken zurÃ¼ck.
    UnterstÃ¼tzt Paginierung via limit und offset.
    """
    limit = request.args.get("limit", type=int)
    offset = request.args.get("offset", default=0, type=int)

    return api_response(data=_services().system_stats_service.get_stats_history(limit=limit, offset=offset))


def record_stats(app):
    """Speichert einen Schnappschuss der Statistiken in der Historie."""
    with app.app_context():
        _services().system_stats_service.record_stats_snapshot(agent_name=app.config.get("AGENT_NAME"))


def check_all_agents_health(app):
    """PrÃ¼ft den Status aller registrierten Agenten parallel.
    Fallback: Wenn keine Agenten im Repo, verwende Datei-basierten Speicher (AGENTS_PATH)."""
    _services().agent_health_monitor_service.check_all_agents_health(
        app=app,
        failure_state=_agent_health_failures,
        failure_lock=_agent_health_lock,
        offline_failure_threshold=_agent_offline_failure_threshold,
    )


@system_bp.route("/csp-report", methods=["POST"])
@rate_limit(limit=10, window=60, namespace="system_csp_report")
def csp_report():
    """
    EmpfÃ¤ngt CSP-Verletzungsberichte (Content Security Policy)
    ---
    tags:
      - Security
    responses:
      204:
        description: Bericht empfangen
    """
    try:
        # CSP Berichte kÃ¶nnen als 'application/csp-report' oder 'application/json' kommen
        data = request.get_json(silent=True, force=True)
        if not data:
            return api_response(status="error", message="UngÃ¼ltiger CSP-Bericht", code=400)

        # Meistens ist der Bericht in einem Top-Level Key 'csp-report' verschachtelt
        report = data.get("csp-report", data)

        # Details extrahieren fÃ¼r Logging
        blocked_uri = report.get("blocked-uri", "unknown")
        violated_directive = report.get("violated-directive", "unknown")
        document_uri = report.get("document-uri", "unknown")

        msg = f"CSP-Verletzung: {blocked_uri} (Directive: {violated_directive}) in {document_uri}"
        _log().warning(msg)

        # In Audit-Logs speichern
        _repos().audit_repo.save(
            AuditLogDB(
                username="system",
                ip=request.remote_addr,
                action="CSP_VIOLATION",
                details={"report": report, "user_agent": request.headers.get("User-Agent")},
            )
        )

        return "", 204
    except Exception as e:
        _log().error("Fehler beim Verarbeiten des CSP-Berichts: %s", e)
        return "", 204  # Wir geben immer 204 zurÃ¼ck, um keine Infos zu leaken


@system_bp.route("/audit-logs", methods=["GET"])
@admin_required
def get_audit_logs():
    """
    Audit-Logs abrufen
    ---
    tags:
      - Admin
    security:
      - Bearer: []
    parameters:
      - name: limit
        in: query
        type: integer
        default: 100
      - name: offset
        in: query
        type: integer
        default: 0
    responses:
      200:
        description: Liste der Audit-Logs
      403:
        description: Administratorrechte erforderlich
    """
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    log_audit("audit_logs_viewed", {"limit": limit, "offset": offset})
    logs = _repos().audit_repo.get_all(limit=limit, offset=offset)
    return api_response(data=[log_entry.model_dump() for log_entry in logs])
