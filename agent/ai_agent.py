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


DEFAULT_ENDPOINTS = {
    "ollama": "http://localhost:11434/api/generate",
    "lmstudio": "http://localhost:1234/v1/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}


def run_agent(
    controller: str = "http://controller:8081",
    endpoints: dict[str, str] | None = None,
    openai_api_key: str | None = None,
    steps: int | None = None,
    step_delay: int = 0,
    pool: ModelPool | None = None,
):
    """Replicate the shell-based ai-agent loop for testing purposes.

    The function now accepts a mapping of ``endpoints`` which can be used to
    specify custom API URLs for each provider. If not supplied, the endpoints
    from the controller configuration are used or fall back to
    ``DEFAULT_ENDPOINTS``.
    """
    pool = pool or ModelPool()
    endpoint_map = {**DEFAULT_ENDPOINTS, **(endpoints or {})}

    os.makedirs(DATA_DIR, exist_ok=True)
    current_agent = None
    log_file = summary_file = None
    step = 0
    while steps is None or step < steps:
        if os.path.exists(STOP_FLAG):
            break
        cfg = _http_get(f"{controller}/next-config")
        # Aktualisieren der Endpunkte aus der Controller-Konfiguration
        cfg_map: dict[str, str] = {}
        for ep in cfg.get("api_endpoints", []):
            typ = ep.get("type")
            url = ep.get("url")
            if typ and url and typ not in cfg_map:
                cfg_map[typ] = url
        endpoint_map.update(cfg_map)
        templates = PromptTemplates(cfg.get("prompt_templates", {}))
        agent = cfg.get("agent", "default")
        if agent != current_agent:
            if log_file:
                with open(log_file, "a") as f:
                    f.write("]")
            current_agent = agent
            log_file, summary_file = _agent_files(agent)
            with open(log_file, "w") as f:
                f.write("[")
            step = 0

        raw_model = cfg.get("model", "")
        model = raw_model if isinstance(raw_model, str) else json.dumps(raw_model)
        provider = cfg.get("provider", "ollama")
        limit = (
            cfg.get("model_limit")
            or cfg.get("limit")
            or cfg.get("concurrency_limit")
            or 1
        )
        pool.register(provider, model, limit)
        max_len = cfg.get("max_summary_length", 300)

        tasks = cfg.get("tasks", [])
        template_name = cfg.get("template")
        if tasks and template_name:
            prompt = templates.render(template_name, task=tasks[0])
        else:
            prompt = cfg.get("prompt", "")
        with open(summary_file, "w") as f:
            f.write(prompt[:max_len])

        asyncio.run(pool.acquire(provider, model))
        try:
            # Hier wird der Aufruf des LLM-Endpunkts in einen zusätzlichen Try/Except-Block
            # eingebettet, sodass bei Verbindungsproblemen nur eine Warnung ausgegeben wird.
            try:
                if provider == "ollama":
                    url = endpoint_map.get("ollama", DEFAULT_ENDPOINTS["ollama"])
                    resp = _http_post(url, {"model": model, "prompt": prompt})
                    cmd = resp.get("response", "") if isinstance(resp, dict) else ""
                elif provider == "lmstudio":
                    url = endpoint_map.get("lmstudio", DEFAULT_ENDPOINTS["lmstudio"])
                    resp = _http_post(url, {"model": model, "prompt": prompt})
                    cmd = resp.get("response", "") if isinstance(resp, dict) else ""
                elif provider == "openai":
                    url = endpoint_map.get("openai", DEFAULT_ENDPOINTS["openai"])
                    resp = _http_post(
                        url,
                        {
                            "model": model,
                            "messages": [{"role": "user", "content": prompt}],
                        },
                        headers={"Authorization": f"Bearer {openai_api_key}"} if openai_api_key else None,
                    )
                    if isinstance(resp, dict):
                        cmd = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                    else:
                        cmd = ""
                else:
                    cmd = ""
            except Exception as e:
                print(f"[Warning] LLM-Endpoint '{provider}' nicht erreichbar: {e}")
                cmd = ""
        finally:
            pool.release(provider, model)

        cmd = _http_post(
            f"{controller}/approve", {"cmd": cmd, "summary": prompt}, form=True
        )
        if cmd == "SKIP":
            step += 1
            continue
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        entry = {
            "step": step,
            "command": cmd,
            "output": (proc.stdout or "") + (proc.stderr or ""),
        }
        with open(log_file, "a") as f:
            if step:
                f.write(",")
            json.dump(entry, f)
        step += 1
        time.sleep(step_delay)
    if log_file:
        with open(log_file, "a") as f:
            f.write("]")

if __name__ == "__main__":
    run_agent()