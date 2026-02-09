import time
import logging
import concurrent.futures
import os
import psutil
import threading
import json
from queue import Queue, Empty
from flask import Blueprint, jsonify, current_app, request, g, Response
from agent.common.errors import api_response
from agent.metrics import generate_latest, CONTENT_TYPE_LATEST, CPU_USAGE, RAM_USAGE
from agent.utils import rate_limit, validate_request, read_json, write_json, _http_get
from agent.models import AgentRegisterRequest
from agent.auth import check_auth, rotate_token, admin_required
from agent.config import settings
from agent.common.http import get_default_client
from agent.repository import agent_repo, task_repo, stats_repo, audit_repo, login_attempt_repo, banned_ip_repo
from agent.db_models import AgentInfoDB, StatsSnapshotDB, AuditLogDB

# Historie für Statistiken (wird jetzt in DB gespeichert)
STATS_HISTORY = [] # Nur noch als Fallback oder temporärer Cache

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
                        resources=item.get("resources", {})
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

# Pub/Sub für System-Events
_system_subscribers = []
_system_subscribers_lock = threading.Lock()

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
    Audit-Logs mittels LLM auf verdächtige Muster analysieren
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
    
    log_text = "\n".join([
        f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(l.timestamp))}] User: {l.username}, IP: {l.ip}, Action: {l.action}, Details: {json.dumps(l.details)}"
        for l in logs
    ])
    
    prompt = f"""Analysiere die folgenden Audit-Logs auf verdächtige Muster, Brute-Force-Angriffe, unbefugte Zugriffsbemühungen oder ungewöhnliches Verhalten.
Gib eine kurze Einschätzung und weise auf kritische Punkte hin.

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
            api_key=current_app.config["OPENAI_API_KEY"]
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
    
    # Nur Provider prüfen, die entweder Default sind oder bei denen eine URL/Key gesetzt ist
    active_providers = set([settings.default_provider])
    if settings.openai_api_key: active_providers.add("openai")
    if settings.anthropic_api_key: active_providers.add("anthropic")
    
    # Wenn URLs vom Standard abweichen, auch prüfen
    if settings.ollama_url != "http://localhost:11434/api/generate": active_providers.add("ollama")
    if settings.lmstudio_url != "http://192.168.56.1:1234/v1/completions": active_providers.add("lmstudio")
    
    def _check_provider(p):
        url = getattr(settings, f"{p}_url", None)
        if not url:
            return p, None
        
        # Spezielle URL für Healthchecks bei bestimmten Providern
        check_url = url
        if p == "lmstudio":
            from agent.llm_integration import _lmstudio_models_url
            models_url = _lmstudio_models_url(url)
            if models_url:
                check_url = models_url

        try:
            # Schneller Check ob der Service erreichbar ist. 
            # Timeout etwas höher als 1.0s für stabilere Checks in Docker.
            check_timeout = min(settings.http_timeout, 3.0)
            res = http_client.get(check_url, timeout=check_timeout, return_response=True, silent=True)
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

    return api_response(data={
        "agent": current_app.config.get("AGENT_NAME"),
        "checks": checks
    })

@system_bp.route("/ready", methods=["GET"])
def readiness_check():
    """
    Readiness Check des Agenten
    ---
    responses:
      200:
        description: Agent ist bereit
      503:
        description: Agent oder Abhängigkeiten nicht bereit
    """
    results = {}
    is_ready = True
    
    def _check_hub():
        try:
            start = time.time()
            res = http_client.get(settings.hub_url, timeout=settings.http_timeout, return_response=True, silent=True)
            if res:
                return "hub", {
                    "status": "ok" if res.status_code < 500 else "unstable",
                    "latency": round(time.time() - start, 3),
                    "code": res.status_code
                }
            else:
                return "hub", {"status": "error", "message": "No response from hub"}
        except Exception as e:
            return "hub", {"status": "error", "message": str(e)}

    def _check_llm():
        provider = settings.default_provider
        url = getattr(settings, f"{provider}_url", None)
        if not url:
            return "llm", None
        
        check_url = url
        if provider == "lmstudio":
            from agent.llm_integration import _lmstudio_models_url
            models_url = _lmstudio_models_url(url)
            if models_url:
                check_url = models_url

        try:
            start = time.time()
            res = http_client.get(check_url, timeout=settings.http_timeout, return_response=True, silent=True)
            if res:
                return "llm", {
                    "provider": provider,
                    "status": "ok" if res.status_code < 500 else "unstable",
                    "latency": round(time.time() - start, 3),
                    "code": res.status_code
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
        code=200 if is_ready else 503
    )

@system_bp.route("/metrics", methods=["GET"])
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

@system_bp.route("/register", methods=["POST"])
@rate_limit(limit=20, window=60)
@validate_request(AgentRegisterRequest)
def register_agent():
    data = g.validated_data.model_dump()
    
    # Registrierungs-Token prüfen, falls konfiguriert
    if settings.registration_token:
        provided_token = data.get("registration_token")
        if provided_token != settings.registration_token:
            logging.warning(f"Abgelehnte Registrierung für {data.get('name')}: Ungültiger Registrierungs-Token")
            return api_response(status="error", message="Invalid or missing registration token", code=401)

    name = data.get("name")
    url = data.get("url")
    
    # URL Validierung: Prüfen ob der Agent erreichbar ist
    try:
        # Wir versuchen den /health Endpunkt des Agenten oder die Basis-URL zu erreichen
        check_url = f"{url.rstrip('/')}/health"
        res = http_client.get(check_url, timeout=2.0, return_response=True)
        if not res or res.status_code >= 500:
            # Fallback auf Basis-URL falls /health nicht existiert
            res = http_client.get(url, timeout=2.0, return_response=True)
            
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
        status="online"
    )
    agent_repo.save(agent)
    logging.info(f"Agent registriert: {name} ({url})")
    return api_response(data={"status": "registered"})

@system_bp.route("/agents", methods=["GET"])
@check_auth
def list_agents():
    agents = agent_repo.get_all()
    now = time.time()
    
    timeout = getattr(settings, "agent_offline_timeout", 300)
    
    for agent in agents:
        if agent.status == "online":
            if now - agent.last_seen > timeout:
                agent.status = "offline"
                agent_repo.save(agent)
                logging.info(f"Agent {agent.name} ist jetzt offline (letzte Meldung vor {round(now - agent.last_seen)}s)")
    
    return api_response(data=[a.model_dump() for a in agents])

@system_bp.route("/rotate-token", methods=["POST"])
@admin_required
def do_rotate_token():
    new_token = rotate_token()
    _notify_system_event("token_rotated", {"new_token": new_token})
    return api_response(data={"status": "rotated", "new_token": new_token})

def _get_resource_usage():
    """Gibt CPU und RAM Verbrauch des aktuellen Prozesses zurück."""
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
    Aggregierte Statistiken für das Dashboard
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
    shell_stats = {
        "total": pool.size,
        "free": free_shells,
        "busy": len(pool.shells) - free_shells
    }

    # 4. Ressourcen Statistik
    resources = _get_resource_usage()

    return api_response(data={
        "agents": agent_counts,
        "tasks": task_counts,
        "shell_pool": shell_stats,
        "resources": resources,
        "timestamp": time.time(),
        "agent_name": current_app.config.get("AGENT_NAME")
    })

@system_bp.route("/stats/history", methods=["GET"])
@check_auth
def get_stats_history():
    """
    Gibt die Historie der Statistiken zurück.
    Unterstützt Paginierung via limit und offset.
    """
    limit = request.args.get("limit", type=int)
    offset = request.args.get("offset", default=0, type=int)
    
    history = stats_repo.get_all(limit=limit, offset=offset)
    
    # In dict umwandeln für JSON-Response
    result = []
    for h in history:
        result.append({
            "timestamp": h.timestamp,
            "agents": h.agents,
            "tasks": h.tasks,
            "shell_pool": h.shell_pool,
            "resources": h.resources
        })
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
                if status not in agent_counts: agent_counts[status] = 0
                agent_counts[status] += 1

            # 2. Task Statistik
            tasks = task_repo.get_all()
            task_counts = {"total": len(tasks), "completed": 0, "failed": 0, "todo": 0, "in_progress": 0}
            for t in tasks:
                status = t.status or "unknown"
                if status not in task_counts: task_counts[status] = 0
                task_counts[status] += 1

            # 3. Shell Pool Statistik
            from agent.shell import get_shell_pool
            pool = get_shell_pool()
            free_shells = pool.pool.qsize()
            shell_stats = {
                "total": pool.size,
                "free": free_shells,
                "busy": len(pool.shells) - free_shells
            }

            # 4. Ressourcen Statistik
            resources = _get_resource_usage()

            snapshot = StatsSnapshotDB(
                agents=agent_counts,
                tasks=task_counts,
                shell_pool=shell_stats,
                resources=resources,
                timestamp=time.time()
            )
            
            stats_repo.save(snapshot)
            
            # Alte Snapshots löschen (begrenzen auf konfigurierte Größe)
            stats_repo.delete_old(settings.stats_history_size)

            # Alte Login-Versuche löschen (älter als 24h)
            login_attempt_repo.delete_old(max_age_seconds=86400)

            # Abgelaufene IP-Sperren löschen
            banned_ip_repo.delete_expired()
                
        except Exception as e:
            is_db_err = "OperationalError" in str(e) or "psycopg2" in str(e)
            if is_db_err:
                logging.info(f"Statistik-Aufzeichnung übersprungen: Datenbank nicht erreichbar.")
            else:
                logging.error(f"Fehler beim Aufzeichnen der Statistik-Historie: {e}")

def check_all_agents_health(app):
    """Prüft den Status aller registrierten Agenten parallel."""
    with app.app_context():
        try:
            agents = agent_repo.get_all()
            if not agents:
                return
                
            now = time.time()

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
                    
                    # Fallback auf /health falls /stats fehlschlägt
                    check_url = f"{url.rstrip('/')}/health"
                    res = http_client.get(check_url, timeout=5.0, return_response=True, silent=True)
                    return agent_obj, ("online" if res and res.status_code < 500 else "offline", None)
                except Exception:
                    return agent_obj, ("offline", None)

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(_check_agent, a) for a in agents]
                for future in concurrent.futures.as_completed(futures):
                    agent_obj, res_tuple = future.result()
                    if not res_tuple: continue
                    
                    new_status, resources = res_tuple
                    
                    # Status-Update
                    changed = False
                    if agent_obj.status != new_status:
                        logging.info(f"Agent {agent_obj.name} Statusänderung: {agent_obj.status} -> {new_status}")
                        agent_obj.status = new_status
                        changed = True
                    
                    if new_status == "online":
                        agent_obj.last_seen = now
                        changed = True
                        
                    if changed:
                        agent_repo.save(agent_obj)
        except Exception as e:
            is_db_err = "OperationalError" in str(e) or "psycopg2" in str(e)
            if is_db_err:
                logging.info(f"Agent-Health-Check übersprungen: Datenbank nicht erreichbar.")
            else:
                logging.error(f"Fehler beim Agent-Health-Check: {e}")

@system_bp.route("/csp-report", methods=["POST"])
@rate_limit(limit=10, window=60)
def csp_report():
    """
    Empfängt CSP-Verletzungsberichte (Content Security Policy)
    ---
    tags:
      - Security
    responses:
      204:
        description: Bericht empfangen
    """
    try:
        # CSP Berichte können als 'application/csp-report' oder 'application/json' kommen
        data = request.get_json(silent=True, force=True)
        if not data:
            return api_response(status="error", message="Ungültiger CSP-Bericht", code=400)

        # Meistens ist der Bericht in einem Top-Level Key 'csp-report' verschachtelt
        report = data.get("csp-report", data)
        
        # Details extrahieren für Logging
        blocked_uri = report.get("blocked-uri", "unknown")
        violated_directive = report.get("violated-directive", "unknown")
        document_uri = report.get("document-uri", "unknown")
        
        msg = f"CSP-Verletzung: {blocked_uri} (Directive: {violated_directive}) in {document_uri}"
        logging.warning(msg)
        
        # In Audit-Logs speichern
        audit_repo.save(AuditLogDB(
            username="system",
            ip=request.remote_addr,
            action="CSP_VIOLATION",
            details={
                "report": report,
                "user_agent": request.headers.get("User-Agent")
            }
        ))
        
        return "", 204
    except Exception as e:
        logging.error(f"Fehler beim Verarbeiten des CSP-Berichts: {e}")
        return "", 204 # Wir geben immer 204 zurück, um keine Infos zu leaken

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
    return api_response(data=[l.model_dump() for l in logs])
