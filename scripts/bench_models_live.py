#!/usr/bin/env python3
import json
import os
import time
import urllib.request


def req(base: str, method: str, path: str, body=None, token: str | None = None, timeout: int = 120) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request_obj = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request_obj, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    base = os.getenv("HUB_BASE_URL", "http://127.0.0.1:5000")
    username = os.getenv("INITIAL_ADMIN_USER", "admin")
    password = os.getenv("INITIAL_ADMIN_PASSWORD", "")
    models = [
        "mradermacher-lfm2.5-1.2b-glm-4.7-flash-thinking-i1-gguf-lfm2.5-1.2b-c7d4a41ae661:latest",
        "bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s:latest",
        "lmstudio-community-phi-4-mini-reasoning-gguf-phi-4-mini-reasoning-q4_k_m:latest",
        "matrixportalx-glm-4-9b-0414-q4_k_m-gguf-glm-4-9b-0414-q4_k_m:latest",
        "irmma-glm-z1-9b-0414-q4_k_s-gguf-glm-z1-9b-0414-q4_k_s-imat:latest",
    ]

    login = req(base, "POST", "/login", {"username": username, "password": password}, timeout=45)
    token = str((login.get("data") or {}).get("access_token") or "")
    if not token:
        print("login_failed_or_no_token")
        return 2

    for model in models:
        payload = {
            "prompt": 'Gib exakt JSON: {"ok":true,"kind":"smoke"}',
            "stream": False,
            "config": {
                "provider": "ollama",
                "model": model,
                "base_url": "http://ollama:11434/api/generate",
            },
        }
        started = time.time()
        try:
            result = req(base, "POST", "/llm/generate", payload, token=token, timeout=120)
            latency_ms = int((time.time() - started) * 1000)
            text = str((result.get("data") or {}).get("response") or "")
            preview = text.replace("\n", " ")[:100]
            print(f"{model}\tlatency_ms={latency_ms}\tnon_empty={bool(text.strip())}\tpreview={preview}")
        except Exception as exc:
            latency_ms = int((time.time() - started) * 1000)
            print(f"{model}\tlatency_ms={latency_ms}\terror={exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
