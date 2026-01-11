from flask import Blueprint, jsonify, current_app
import time
import requests

# Versuche die neue zentrale Konfiguration zu laden
try:
    from src.config.settings import settings
except ImportError:
    import os
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from src.config.settings import settings

health_bp = Blueprint('health', __name__)

@health_bp.route('/health')
def health_check():
    return jsonify({'status': 'ok', 'timestamp': time.time()})

@health_bp.route('/ready')
def readiness_check():
    results = {}
    is_ready = True
    
    # 1. Controller check
    try:
        start = time.time()
        # Wir nutzen HEAD um Traffic zu sparen
        res = requests.head(settings.controller_url, timeout=settings.http_timeout)
        # Wenn der Controller 404 oder so zurückgibt, ist er trotzdem "erreichbar"
        # Aber wir wollen eigentlich ein 200er oder 4xx (unauthorized ist auch ok)
        results["controller"] = {
            "status": "ok" if res.status_code < 500 else "unstable",
            "latency": round(time.time() - start, 3),
            "code": res.status_code
        }
    except Exception as e:
        results["controller"] = {"status": "error", "message": str(e)}
        is_ready = False

    # 2. LLM Check (Default Provider)
    provider = settings.default_provider
    url = getattr(settings, f"{provider}_url", None)
    if url:
        try:
            start = time.time()
            # Bei Ollama/LMStudio ist HEAD oft nicht unterstützt, wir machen einen kleinen GET
            # oder wir prüfen nur die Erreichbarkeit des Ports
            res = requests.get(url, timeout=settings.http_timeout)
            results["llm"] = {
                "provider": provider,
                "status": "ok" if res.status_code < 500 else "unstable",
                "latency": round(time.time() - start, 3),
                "code": res.status_code
            }
        except Exception as e:
            results["llm"] = {"status": "error", "message": str(e)}
            # LLM Fehler machen wir optional "warning", da der Agent trotzdem laufen kann
            # Aber laut Todo: "/ready == ok nur wenn Abhängigkeiten erreichbar"
            is_ready = False

    return jsonify({
        "status": "ok" if is_ready else "error",
        "ready": is_ready,
        "checks": results
    }), 200 if is_ready else 503
