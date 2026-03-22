import concurrent.futures
import json
import logging
import os
import threading
import time
from queue import Empty, Queue

import psutil
from flask import Blueprint, Response, current_app, g, jsonify, request

from agent.auth import admin_required, check_auth, rotate_token
from agent.common.errors import api_response
from agent.common.http import get_default_client
from agent.config import settings
from agent.db_models import AgentInfoDB, AuditLogDB, StatsSnapshotDB
from agent.metrics import CONTENT_TYPE_LATEST, CPU_USAGE, RAM_USAGE, generate_latest
from agent.models import AgentRegisterRequest
from agent.repository import agent_repo, audit_repo, banned_ip_repo, login_attempt_repo, stats_repo, task_repo
from agent.utils import rate_limit, read_json, validate_request, write_json

# Historie fÃ¼r Statistiken (wird jetzt in DB gespeichert)
STATS_HISTORY = []  # Nur noch als Fallback oder temporÃ¤rer Cache


def _load_history(app):
    """Migriert alte JSON-Historie in die Datenbank falls vorhanden."""
    path = app.config.get("STATS_HISTORY_PATH", "data/stats_history.json")
    if os.path.exists(path):
        try:
            old_data = read_json(path, [])
            if old_data:
                logging.info(f"Migriere {len(old_data)} Statistik-Snapshots in die Datenbank...")
                for item in old_data:
                    snapshot = StatsSnapshotDB(
                        timestamp=item.get("timestamp", time.time()),
                        agents=item.get("agents", {}),
                        tasks=item.get("tasks", {}),
                        shell_pool=item.get("shell_pool", {}),
                        resources=item.get("resources", {}),
                    )
                    stats_repo.save(snapshot)

                # Datei umbenennen um Doppelmigration zu verhindern
                os.rename(path, f"{path}.bak")
                logging.info("Migration abgeschlossen. Alte Datei in .bak umbenannt.")
        except Exception as e:
            logging.error(f"Fehler bei der Migration der Statistik-Historie: {e}")


def _save_history(app):
    """Veraltet: Wird jetzt direkt in record_stats via DB erledigt."""
    pass


system_bp = Blueprint("system", __name__)
http_client = get_default_client()

# Pub/Sub fÃ¼r System-Events
_system_subscribers = []
_system_subscribers_lock = threading.Lock()
_agent_health_failures: dict[str, int] = {}
_agent_health_lock = threading.Lock()
_agent_offline_failure_threshold = 3


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
    logs = audit_repo.get_all(limit=limit)

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
    checks = {}

    # 1. Shell Check
    from agent.shell import get_shell

    try:
        shell = get_shell()
        checks["shell"] = {"status": "ok" if shell.is_healthy() else "down"}
    except Exception as e:
        checks["shell"] = {"status": "error", "message": str(e)}

    # 2. LLM Providers Check
    llm_checks = {}

    # Nur Provider prÃ¼fen, die entweder Default sind oder bei denen eine URL/Key gesetzt ist
    provider_urls = _runtime_provider_urls()
    active_providers = {_runtime_default_provider()}
    if current_app.config.get("OPENAI_API_KEY") or settings.openai_api_key:
        active_providers.add("openai")
    if current_app.config.get("ANTHROPIC_API_KEY") or settings.anthropic_api_key:
        active_providers.add("anthropic")

    # Wenn URLs vom Standard abweichen, auch prÃ¼fen
    if provider_urls.get("ollama") and provider_urls.get("ollama") != "http://localhost:11434/api/generate":
        active_providers.add("ollama")
    if provider_urls.get("lmstudio") and provider_urls.get("lmstudio") != "http://192.168.56.1:1234/v1/completions":
        active_providers.add("lmstudio")

    def _check_provider(p):
        url = provider_urls.get(p)
        if not url:
            return p, None

        if p == "lmstudio":
            from agent.llm_integration import probe_lmstudio_runtime

            check_timeout = min(settings.http_timeout, 3.0)
            probe = probe_lmstudio_runtime(url, timeout=check_timeout)
            if probe["ok"]:
                return p, ("ok" if probe["candidate_count"] > 0 else "unstable")
            return p, "unreachable" if probe["status"] != "invalid_url" else "error"

        try:
            # Schneller Check ob der Service erreichbar ist.
            # Timeout etwas hÃ¶her als 1.0s fÃ¼r stabilere Checks in Docker.
            check_timeout = min(settings.http_timeout, 3.0)
            res = http_client.get(url, timeout=check_timeout, return_response=True, silent=True)
            if res:
                return p, ("ok" if res.status_code < 500 else "unstable")
            else:
                return p, "unreachable"
        except Exception:
            return p, "error"

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_providers)) as executor:
        futures = [executor.submit(_check_provider, p) for p in active_providers]
        for future in concurrent.futures.as_completed(futures):
            p, status = future.result()
            if status:
                llm_checks[p] = status

    if llm_checks:
        checks["llm_providers"] = llm_checks

    return api_response(data={"agent": current_app.config.get("AGENT_NAME"), "checks": checks})


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
        provider = _runtime_default_provider()
        url = _runtime_provider_urls().get(provider)
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
                    "models_url": probe["models_url"],
                    "candidate_count": probe["candidate_count"],
                }
            return "llm", {"status": "error", "message": f"No response from LLM provider {provider}"}

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
@rate_limit(limit=20, window=60)
@validate_request(AgentRegisterRequest)
def register_agent():
    data = g.validated_data.model_dump()

    # Registrierungs-Token prÃ¼fen, falls konfiguriert
    if settings.registration_token:
        provided_token = data.get("registration_token")
        if provided_token != settings.registration_token:
            logging.warning(f"Abgelehnte Registrierung fÃ¼r {data.get('name')}: UngÃ¼ltiger Registrierungs-Token")
            return api_response(status="error", message="Invalid or missing registration token", code=401)

    name = data.get("name")
    url = data.get("url")

    # URL Validierung: PrÃ¼fen ob der Agent erreichbar ist
    try:
        check_timeout = min(settings.http_timeout, 5.0)
        # Wir versuchen den /health Endpunkt des Agenten oder die Basis-URL zu erreichen
        check_url = f"{url.rstrip('/')}/health"
        res = http_client.get(check_url, timeout=check_timeout, return_response=True, silent=True)
        if not res or res.status_code >= 500:
            # Fallback auf Basis-URL falls /health nicht existiert
            res = http_client.get(url, timeout=check_timeout, return_response=True, silent=True)

        if not res:
            return api_response(status="error", message=f"Agent URL {url} is unreachable", code=400)
    except Exception as e:
        return api_response(status="error", message=f"Validation failed: {str(e)}", code=400)

    agent = AgentInfoDB(
        url=url,
        name=name,
        role=data.get("role", "worker"),
        token=data.get("token"),
        last_seen=time.time(),
        status="online",
    )
    agent_repo.save(agent)
    logging.info(f"Agent registriert: {name} ({url})")
    return api_response(data={"status": "registered"})


@system_bp.route("/agents", methods=["GET"])
@check_auth
def list_agents():
    """Liste der Agenten. Fallback auf Datei-basierten Speicher im Testmodus."""
    agents = agent_repo.get_all()
    now = time.time()
    timeout = getattr(settings, "agent_offline_timeout", 300)

    if agents:
        for agent in agents:
            if agent.status == "online" and (now - agent.last_seen > timeout):
                agent.status = "offline"
                agent_repo.save(agent)
                logging.info(
                    f"Agent {agent.name} ist jetzt offline (letzte Meldung vor {round(now - agent.last_seen)}s)"
                )
        return api_response(data=[a.model_dump() for a in agents])

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
        logging.error(f"Fehler beim Laden der Agenten (Fallback): {e}")
        return api_response(status="error", message="could not load agents", code=500)


@system_bp.route("/rotate-token", methods=["POST"])
@admin_required
def do_rotate_token():
    new_token = rotate_token()
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
        logging.error(f"Error getting resource usage: {e}")
        return {"cpu_percent": 0, "ram_bytes": 0}


@system_bp.route("/stats", methods=["GET"])
@check_auth
def system_stats():
    """
    Aggregierte Statistiken fÃ¼r das Dashboard
    """
    # 1. Agenten Statistik
    agents = agent_repo.get_all()
    agent_counts = {"total": len(agents), "online": 0, "offline": 0}
    for a in agents:
        status = a.status or "offline"
        if status not in agent_counts:
            agent_counts[status] = 0
        agent_counts[status] += 1

    # 2. Task Statistik
    tasks = task_repo.get_all()
    task_counts = {"total": len(tasks), "completed": 0, "failed": 0, "todo": 0, "in_progress": 0}
    for t in tasks:
        status = t.status or "unknown"
        if status not in task_counts:
            task_counts[status] = 0
        task_counts[status] += 1

    # 3. Shell Pool Statistik
    from agent.shell import get_shell_pool

    pool = get_shell_pool()
    free_shells = pool.pool.qsize()
    shell_stats = {"total": pool.size, "free": free_shells, "busy": len(pool.shells) - free_shells}

    # 4. Ressourcen Statistik
    resources = _get_resource_usage()

    return api_response(
        data={
            "agents": agent_counts,
            "tasks": task_counts,
            "shell_pool": shell_stats,
            "resources": resources,
            "timestamp": time.time(),
            "agent_name": current_app.config.get("AGENT_NAME"),
        }
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

    history = stats_repo.get_all(limit=limit, offset=offset)

    # In dict umwandeln fÃ¼r JSON-Response
    result = []
    for h in history:
        result.append(
            {
                "timestamp": h.timestamp,
                "agents": h.agents,
                "tasks": h.tasks,
                "shell_pool": h.shell_pool,
                "resources": h.resources,
            }
        )
    return api_response(data=result)


def record_stats(app):
    """Speichert einen Schnappschuss der Statistiken in der Historie."""
    with app.app_context():
        try:
            # 1. Agenten Statistik
            agents = agent_repo.get_all()
            agent_counts = {"total": len(agents), "online": 0, "offline": 0}
            for a in agents:
                status = a.status or "offline"
                if status not in agent_counts:
                    agent_counts[status] = 0
                agent_counts[status] += 1

            # 2. Task Statistik
            tasks = task_repo.get_all()
            task_counts = {"total": len(tasks), "completed": 0, "failed": 0, "todo": 0, "in_progress": 0}
            for t in tasks:
                status = t.status or "unknown"
                if status not in task_counts:
                    task_counts[status] = 0
                task_counts[status] += 1

            # 3. Shell Pool Statistik
            from agent.shell import get_shell_pool

            pool = get_shell_pool()
            free_shells = pool.pool.qsize()
            shell_stats = {"total": pool.size, "free": free_shells, "busy": len(pool.shells) - free_shells}

            # 4. Ressourcen Statistik
            resources = _get_resource_usage()

            snapshot = StatsSnapshotDB(
                agents=agent_counts,
                tasks=task_counts,
                shell_pool=shell_stats,
                resources=resources,
                timestamp=time.time(),
            )

            stats_repo.save(snapshot)

            # Alte Snapshots lÃ¶schen (begrenzen auf konfigurierte GrÃ¶ÃŸe)
            stats_repo.delete_old(settings.stats_history_size)

            # Alte Login-Versuche lÃ¶schen (Ã¤lter als 24h)
            login_attempt_repo.delete_old(max_age_seconds=86400)

            # Abgelaufene IP-Sperren lÃ¶schen
            banned_ip_repo.delete_expired()

        except Exception as e:
            is_db_err = "OperationalError" in str(e) or "psycopg2" in str(e)
            if is_db_err:
                logging.info("Statistik-Aufzeichnung Ã¼bersprungen: Datenbank nicht erreichbar.")
            else:
                logging.error(f"Fehler beim Aufzeichnen der Statistik-Historie: {e}")


def check_all_agents_health(app):
    """PrÃ¼ft den Status aller registrierten Agenten parallel.
    Fallback: Wenn keine Agenten im Repo, verwende Datei-basierten Speicher (AGENTS_PATH)."""
    with app.app_context():
        try:
            agents = agent_repo.get_all()
            now = time.time()

            if not agents:
                # Datei-basierter Fallback fÃ¼r Tests
                agents_path = app.config.get("AGENTS_PATH", "data/agents.json")
                file_agents = read_json(agents_path, {}) or {}

                def _check_name(name, info):
                    url = info.get("url")
                    token = info.get("token")
                    if not url:
                        return None
                    try:
                        stats_url = f"{url.rstrip('/')}/stats"
                        headers = {"Authorization": f"Bearer {token}"} if token else {}
                        from agent.common.http import get_default_client

                        http = get_default_client()
                        res = http.get(stats_url, headers=headers, timeout=5.0, return_response=True, silent=True)
                        if res and res.status_code == 200:
                            info["status"] = "online"
                            info["last_seen"] = now
                            return True
                        # Fallback: /health
                        health_url = f"{url.rstrip('/')}/health"
                        res = http.get(health_url, timeout=5.0, return_response=True, silent=True)
                        if res and res.status_code < 500:
                            info["status"] = "online"
                            info["last_seen"] = now
                            return True
                        info["status"] = "offline"
                        return False
                    except Exception:
                        info["status"] = "offline"
                        return False

                changed = False
                for name, info in file_agents.items():
                    prev = info.get("status")
                    _check_name(name, info)
                    if info.get("status") != prev:
                        changed = True
                if changed:
                    write_json(agents_path, file_agents)
                return

            def _check_agent(agent_obj):
                url = agent_obj.url
                token = agent_obj.token
                if not url:
                    return agent_obj, None
                try:
                    # Wir versuchen /stats abzufragen, da es mehr Infos (CPU/RAM) liefert
                    stats_url = f"{url.rstrip('/')}/stats"
                    headers = {"Authorization": f"Bearer {token}"} if token else {}
                    from agent.common.http import get_default_client

                    http_client = get_default_client()
                    res = http_client.get(stats_url, headers=headers, timeout=5.0, return_response=True, silent=True)

                    if res and res.status_code == 200:
                        try:
                            data = res.json()
                            return agent_obj, ("online", data.get("resources"))
                        except Exception:
                            return agent_obj, ("online", None)

                    # Fallback auf /health falls /stats fehlschlÃ¤gt
                    check_url = f"{url.rstrip('/')}/health"
                    res = http_client.get(check_url, timeout=5.0, return_response=True, silent=True)
                    return agent_obj, ("online" if res and res.status_code < 500 else "offline", None)
                except Exception:
                    return agent_obj, ("offline", None)

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(_check_agent, a) for a in agents]
                for future in concurrent.futures.as_completed(futures):
                    agent_obj, res_tuple = future.result()
                    if not res_tuple:
                        continue

                    new_status, resources = res_tuple
                    agent_key = (agent_obj.url or agent_obj.name or "").strip() or agent_obj.name
                    effective_status = new_status

                    with _agent_health_lock:
                        if new_status == "online":
                            _agent_health_failures[agent_key] = 0
                        else:
                            failures = int(_agent_health_failures.get(agent_key, 0)) + 1
                            _agent_health_failures[agent_key] = failures
                            if failures < _agent_offline_failure_threshold and agent_obj.status == "online":
                                effective_status = "online"

                    # Status-Update
                    changed = False
                    if agent_obj.status != effective_status:
                        logging.info(
                            f"Agent {agent_obj.name} Statusänderung: {agent_obj.status} -> {effective_status}"
                        )
                        agent_obj.status = effective_status
                        changed = True

                    if effective_status == "online":
                        agent_obj.last_seen = now
                        changed = True

                    if changed:
                        agent_repo.save(agent_obj)
        except Exception as e:
            is_db_err = "OperationalError" in str(e) or "psycopg2" in str(e)
            if is_db_err:
                logging.info("Agent-Health-Check Ã¼bersprungen: Datenbank nicht erreichbar.")
            else:
                logging.error(f"Fehler beim Agent-Health-Check: {e}")


@system_bp.route("/csp-report", methods=["POST"])
@rate_limit(limit=10, window=60)
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
        logging.warning(msg)

        # In Audit-Logs speichern
        audit_repo.save(
            AuditLogDB(
                username="system",
                ip=request.remote_addr,
                action="CSP_VIOLATION",
                details={"report": report, "user_agent": request.headers.get("User-Agent")},
            )
        )

        return "", 204
    except Exception as e:
        logging.error(f"Fehler beim Verarbeiten des CSP-Berichts: {e}")
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
    logs = audit_repo.get_all(limit=limit, offset=offset)
    return api_response(data=[log_entry.model_dump() for log_entry in logs])
