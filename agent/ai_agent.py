import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.error
import asyncio
import logging

from src.models import ModelPool
from src.agents.templates import PromptTemplates

# Allow overriding data directory for testing via the DATA_DIR environment variable
# Fall back to the project root so controller and agent share the same files
DATA_DIR = os.environ.get(
    "DATA_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
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


def _http_post(
    url: str,
    data: dict,
    form: bool = False,
    headers: dict | None = None,
    retries: int = 5,
    delay: float = 1.0,
):
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
    "lmstudio": "http://host.docker.internal:1234/v1/chat/completions"
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
    Hauptschleife des AI-Agenten.
    
    - Abfrage der nächsten Konfiguration und Aufgaben vom Controller via GET /next-config.
    - Rendern des Prompts via PromptTemplates (bei vorhandener Vorlage).
    - Nutzung eines ModelPools zur Begrenzung paralleler LLM-Anfragen.
    - Senden der generierten Antwort an den /approve-Endpoint des Controllers.
    - Protokollierung von Logs und Zusammenfassung.
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

    # Logger einrichten
    logger = logging.getLogger(current_agent)
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(log_file)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)

    step = 0
    while steps is None or step < steps:
        if os.path.exists(STOP_FLAG):
            print("STOP_FLAG gefunden, beende Agent-Schleife.")
            break

        try:
            cfg = _http_get(f"{controller}/next-config")
            logger.info("Task-Empfang: %s", cfg.get("tasks"))
        except Exception as e:
            logger.error("Verbindung zum Controller fehlgeschlagen: %s", e)
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

        # Überprüfen, ob in der Konfiguration eine Aufgabe vorhanden ist
        if "tasks" in cfg and isinstance(cfg["tasks"], list) and cfg["tasks"]:
            task_entry = cfg["tasks"].pop(0)
            task = task_entry["task"] if isinstance(task_entry, dict) and "task" in task_entry else task_entry
        else:
            task = "Standardaufgabe: Keine spezifische Aufgabe definiert."

        logger.info("Starte Verarbeitung des Tasks: %s", task)
        print(f"[Step {step}] Bearbeite Aufgabe: {task}")

        # Prompt anhand der übermittelten Templates erzeugen
        templates = PromptTemplates(cfg.get("prompt_templates", {}))
        template_name = cfg.get("template") or current_agent
        prompt = templates.render(template_name, task=task)
        if not prompt:
            prompt = f"Bitte verarbeite folgende Aufgabe: {task}"
        logger.info("Generierter Prompt: %s", prompt)
        data_payload = {"prompt": prompt}

        # Wähle einen Endpunkt – als Beispiel der "openai"-Endpunkt
        api_url = endpoint_map.get("openai")
        if api_url is None:
            print("Kein gültiger API-Endpunkt gefunden. Überspringe diesen Durchlauf.")
        else:
            try:
                # Nutze den ModelPool, um parallele Anfragen zu begrenzen
                pool.acquire(api_url)
                try:
                    response = _http_post(api_url, data_payload)
                finally:
                    pool.release(api_url)
                logger.info("LLM-Antwort: %s", response)
                print(f"Antwort des LLM von {api_url}: {response}")
                # Zusammenfassungsdatei als einfache Zusammenfassung erweitern
                with open(summary_file, "a") as sf:
                    sf.write(f"[Step {step}] {response}\n")
                
                # Sende das Ergebnis an den Controller über den /approve-Endpunkt
                approve_payload = {
                    "agent": current_agent,
                    "task": task,
                    "response": response
                }
                approve_url = f"{controller}/approve"
                try:
                    approve_resp = _http_post(approve_url, approve_payload)
                    logger.info("Controller-Antwort: %s", approve_resp)
                    print(f"Anerkennung vom Controller: {approve_resp}")
                except Exception as e:
                    logger.error("Fehler beim Senden an den Controller: %s", e)
                    print(f"[Warnung] Fehler beim Senden der Genehmigung an den Controller: {e}")
            except Exception as e:
                logger.error("Fehler beim Aufruf des API-Endpunkts %s: %s", api_url, e)
                print(f"[Error] Fehler beim Aufruf des API-Endpunkts {api_url}: {e}")
        
        # Wartezeit zwischen den Schritten
        time.sleep(step_delay)
        step += 1

    # Nach Ende der Schleife den Logfileabschluss schreiben
    logger.info("Agent beendet.")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


if __name__ == "__main__":
    run_agent()