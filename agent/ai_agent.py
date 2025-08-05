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
    
    # Agentenname festlegen und Log-/Summary-Dateien bestimmen
    current_agent = "default"
    log_file, summary_file = _agent_files(current_agent)
    print(f"Starte AI-Agent für '{current_agent}'. Log: {log_file}, Summary: {summary_file}")
    
    step = 0
    while steps is None or step < steps:
        if os.path.exists(STOP_FLAG):
            print("STOP_FLAG gefunden, beende Agent-Schleife.")
            break

        try:
            cfg = _http_get(f"{controller}/next-config")
        except Exception as e:
            print(f"[Error] Verbindung zum Controller fehlgeschlagen: {e}")
            time.sleep(1)
            continue
        
        # Aktualisierung der Endpunkte aus der Controller-Konfiguration
        cfg_map: dict[str, str] = {}
        for ep in cfg.get("api_endpoints", []):
            typ = ep.get("type")
            url = ep.get("url")
            if typ and url and typ not in cfg_map:
                cfg_map[typ] = url
        endpoint_map.update(cfg_map)
        
        # === Hier erfolgt die Ergänzung der eigentlichen Logik ===
        # Überprüfen, ob in der Konfiguration eine Aufgabe vorhanden ist
        task = None
        if "tasks" in cfg and isinstance(cfg["tasks"], list) and cfg["tasks"]:
            # Angenommen, die Aufgabe ist als String oder dict mit "task"-Key gegeben
            task_entry = cfg["tasks"].pop(0)
            task = task_entry["task"] if isinstance(task_entry, dict) and "task" in task_entry else task_entry
        else:
            task = "Standardaufgabe: Keine spezifische Aufgabe definiert."

        print(f"[Step {step}] Bearbeite Aufgabe: {task}")

        # Erstellen des Prompts
        prompt = f"Bitte verarbeite folgende Aufgabe: {task}"
        data_payload = {"prompt": prompt}
        
        # Wähle einen Endpunkt – hier als Beispiel der "openai"-Endpunkt
        api_url = endpoint_map.get("openai")
        if api_url is None:
            print("Kein gültiger API-Endpunkt gefunden. Überspringe diesen Durchlauf.")
        else:
            try:
                response = _http_post(api_url, data_payload)
                print(f"Antwort des LLM von {api_url}: {response}")
                # Ergebnisse in die Logdatei anhängen
                with open(log_file, "a") as lf:
                    lf.write(f"[Step {step}] Aufgabe: {task}\nAntwort: {response}\n")
                # Zusammenfassungsdatei als einfache Zusammenfassung erweitern
                with open(summary_file, "a") as sf:
                    sf.write(f"[Step {step}] {response}\n")
            except Exception as e:
                print(f"[Error] Fehler beim Aufruf des API-Endpunkts {api_url}: {e}")
        
        # Wartezeit zwischen den Schritten
        time.sleep(step_delay)
        step += 1

    # Nach Ende der Schleife könnte der Logfileabschluss erfolgen
    if log_file:
        with open(log_file, "a") as f:
            f.write("Agent beendet.\n")

if __name__ == "__main__":
    run_agent()