import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.error
import asyncio

from src.models import ModelPool
from src.agents.templates import PromptTemplates

# Allow overriding data directory for testing via the DATA_DIR environment variable
DATA_DIR = os.environ.get("DATA_DIR", "/data")
STOP_FLAG = os.path.join(DATA_DIR, "stop.flag")


def _agent_files(agent: str) -> tuple[str, str]:
    return (
        os.path.join(DATA_DIR, f"ai_log_{agent}.json"),
        os.path.join(DATA_DIR, f"summary_{agent}.txt"),
    )


def _http_get(url: str, retries: int = 5, delay: float = 1.0):
    """
    Robustes HTTP-GET mit Retry, falls der Controller noch nicht erreichbar ist.
    - retries: Anzahl der Versuche
    - delay: Wartezeit (in Sekunden) zwischen den Versuchen
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url) as r:
                return json.loads(r.read().decode())
        except urllib.error.URLError as e:
            last_err = e
            if attempt < retries:
                print(f"[_http_get] Versuch {attempt}/{retries} gescheitert, warte {delay}s…")
                time.sleep(delay)
            else:
                raise last_err


def _http_post(url: str, data: dict, form: bool = False, headers: dict | None = None, retries: int = 5, delay: float = 1.0):
    """
    HTTP-POST mit eingebauter Retry-Logik, um Netzwerkprobleme abzufangen.
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            if form:
                body = urllib.parse.urlencode(data).encode()
                hdrs = headers or {}
            else:
                body = json.dumps(data).encode()
                hdrs = {"Content-Type": "application/json"}
                if headers:
                    hdrs.update(headers)
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req) as r:
                resp = r.read().decode()
                try:
                    return json.loads(resp)
                except Exception:
                    return resp
        except urllib.error.URLError as e:
            last_err = e
            if attempt < retries:
                print(f"[_http_post] Versuch {attempt}/{retries} gescheitert, warte {delay}s…")
                time.sleep(delay)
            else:
                raise last_err


# Angenommene Default-Endpunkte als Fallback
DEFAULT_ENDPOINTS = {
    "ollama": "http://localhost:11434/api/generate",
    "lmstudio": "http://localhost:1234/v1/completions",
    "openai": "https://api.openai.com/v1/chat/completions"
}

def run_agent(
    controller: str = None,
    endpoints: dict[str, str] | None = None,
    openai_api_key: str | None = None,
    steps: int | None = None,
    step_delay: int = 0,
    pool: object | None = None,
):
    """
    Replicate the shell-based ai-agent loop for testing purposes.
    """
    # Verwende als Standard den Wert der Umgebungsvariable oder localhost, falls nicht gesetzt
    if controller is None:
        controller = os.environ.get("CONTROLLER_URL", "http://localhost:8081")
    
    pool = pool or ModelPool()
    endpoint_map = {**DEFAULT_ENDPOINTS, **(endpoints or {})}
    
    os.makedirs(DATA_DIR, exist_ok=True)
    current_agent = None
    log_file = summary_file = None
    step = 0
    while steps is None or step < steps:
        if os.path.exists(STOP_FLAG):
            break
        try:
            cfg = _http_get(f"{controller}/next-config")
        except Exception as e:
            print(f"[Error] Verbindung zum Controller fehlgeschlagen: {e}")
            time.sleep(1)
            continue
        
        # Aktualisieren der Endpunkte aus der Controller-Konfiguration
        cfg_map: dict[str, str] = {}
        for ep in cfg.get("api_endpoints", []):
            typ = ep.get("type")
            url = ep.get("url")
            if typ and url and typ not in cfg_map:
                cfg_map[typ] = url
        endpoint_map.update(cfg_map)
        
        # … Rest der Logik des Agenten …
        
        time.sleep(step_delay)
    if log_file:
        with open(log_file, "a") as f:
            f.write("]")

if __name__ == "__main__":
    run_agent()