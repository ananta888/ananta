from flask import Blueprint, jsonify, current_app
import time

# Versuche die neue zentrale Konfiguration zu laden
try:
    from src.config.settings import settings
    from src.common.http import get_default_client
    from src.db.session import check_db_health
except ImportError:
    import os
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from src.config.settings import settings
    from src.common.http import get_default_client
    from src.db.session import check_db_health

health_bp = Blueprint('health', __name__)
http_client = get_default_client()

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
        # Wir nutzen den HttpClient Wrapper
        res = http_client.get(settings.controller_url, timeout=settings.http_timeout, return_response=True)
        if res:
            results["controller"] = {
                "status": "ok" if res.status_code < 500 else "unstable",
                "latency": round(time.time() - start, 3),
                "code": res.status_code
            }
        else:
            raise Exception("No response from controller")
    except Exception as e:
        results["controller"] = {"status": "error", "message": str(e)}
        is_ready = False

    # 2. LLM Check (Default Provider)
    provider = settings.default_provider
    url = getattr(settings, f"{provider}_url", None)
    if url:
        try:
            start = time.time()
            res = http_client.get(url, timeout=settings.http_timeout, return_response=True)
            if res:
                results["llm"] = {
                    "provider": provider,
                    "status": "ok" if res.status_code < 500 else "unstable",
                    "latency": round(time.time() - start, 3),
                    "code": res.status_code
                }
            else:
                raise Exception(f"No response from LLM provider {provider}")
        except Exception as e:
            results["llm"] = {"status": "error", "message": str(e)}
            is_ready = False

    # 3. Database Check
    try:
        start = time.time()
        db_ok = check_db_health()
        results["database"] = {
            "status": "ok" if db_ok else "error",
            "latency": round(time.time() - start, 3)
        }
        if not db_ok:
            is_ready = False
    except Exception as e:
        results["database"] = {"status": "error", "message": str(e)}
        is_ready = False

    return jsonify({
        "status": "ok" if is_ready else "error",
        "ready": is_ready,
        "checks": results
    }), 200 if is_ready else 503
