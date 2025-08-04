import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

# Allow overriding data directory for testing via the DATA_DIR environment variable
DATA_DIR = os.environ.get("DATA_DIR", "/data")
LOG_FILE = os.path.join(DATA_DIR, "ai_log.json")
SUMMARY_FILE = os.path.join(DATA_DIR, "summary.txt")
STOP_FLAG = os.path.join(DATA_DIR, "stop.flag")


def _http_get(url: str):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read().decode())


def _http_post(url: str, data: dict, form: bool = False, headers: dict | None = None):
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


def run_agent(
    controller: str = "http://localhost:8081",
    ollama: str = "http://localhost:11434/api/generate",
    lmstudio: str = "http://localhost:1234/v1/completions",
    openai: str = "https://api.openai.com/v1/chat/completions",
    openai_api_key: str | None = None,
    steps: int | None = None,
    step_delay: int = 0,
):
    """Replicate the shell-based ai-agent loop for testing purposes.

    Parameters
    ----------
    controller: str
        Base URL of the controller service.
    ollama: str
        URL of the Ollama generate endpoint.
    lmstudio: str
        URL of the LM Studio completion endpoint.
    openai: str
        URL of the OpenAI API endpoint.
    openai_api_key: str | None
        Optional API key when using the OpenAI provider.
    steps: int | None
        Number of iterations to execute. ``None`` runs indefinitely until a
        stop flag is found.
    step_delay: int
        Seconds to sleep between steps.
    """

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w") as f:
        f.write("[")
    step = 0
    while steps is None or step < steps:
        if os.path.exists(STOP_FLAG):
            break
        cfg = _http_get(f"{controller}/next-config")
        model = cfg.get("model", "")
        provider = cfg.get("provider", "ollama")
        max_len = cfg.get("max_summary_length", 300)

        # Build prompt from config or previous log output
        prompt = cfg.get("prompt", "")
        with open(SUMMARY_FILE, "w") as f:
            f.write(prompt)

        if provider == "ollama":
            resp = _http_post(ollama, {"model": model, "prompt": prompt})
            cmd = resp.get("response", "") if isinstance(resp, dict) else ""
        elif provider == "lmstudio":
            resp = _http_post(lmstudio, {"model": model, "prompt": prompt})
            cmd = resp.get("response", "") if isinstance(resp, dict) else ""
        elif provider == "openai":
            resp = _http_post(
                openai,
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
        with open(LOG_FILE, "a") as f:
            if step:
                f.write(",")
            json.dump(entry, f)
        step += 1
        time.sleep(step_delay)
    with open(LOG_FILE, "a") as f:
        f.write("]")


if __name__ == "__main__":
    run_agent()
