from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.e2e.record_tui_demo import record_tui_demo

ROOT = Path(__file__).resolve().parents[2]
TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LiveTuiLlm:
    provider: str
    api_base: str
    model: str
    api_token: str = ""


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUE_VALUES


def _model_ids(api_base: str, token: str) -> list[str]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url=f"{api_base}/models", headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=4.0) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(data, list):
        return []
    return [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]


def _lmstudio_candidates() -> list[str]:
    raw_candidates = [
        os.environ.get("ANANTA_TUI_LLM_API_BASE"),
        os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL"),
        os.environ.get("LMSTUDIO_URL"),
        "http://192.168.178.100:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://localhost:1234/v1",
    ]
    candidates: list[str] = []
    for raw in raw_candidates:
        if not raw:
            continue
        candidate = str(raw).rstrip("/")
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _select_live_tui_llm() -> LiveTuiLlm:
    if not (_env_flag("ANANTA_E2E_LIVE_TUI_LLM") or _env_flag("ANANTA_E2E_LIVE_LMSTUDIO")):
        pytest.skip(
            "Set ANANTA_E2E_LIVE_TUI_LLM=1 or ANANTA_E2E_LIVE_LMSTUDIO=1 "
            "to run the live TUI AI-Snake cast E2E."
        )

    provider = os.environ.get("ANANTA_E2E_TUI_LIVE_PROVIDER", "").strip().lower()
    if not provider:
        provider = "openai" if _env_flag("ANANTA_E2E_LIVE_TUI_LLM") else "lmstudio"

    if provider == "openai":
        token = str(
            os.environ.get("ANANTA_TUI_LLM_API_TOKEN")
            or os.environ.get("ANANTA_TUI_SNAKE_AI_API_TOKEN")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        if not token:
            pytest.skip("OPENAI_API_KEY or ANANTA_TUI_LLM_API_TOKEN is required for OpenAI live TUI E2E.")
        return LiveTuiLlm(
            provider="openai",
            api_base=str(os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
            model=str(os.environ.get("LIVE_LLM_MODEL") or "gpt-4o-mini"),
            api_token=token,
        )

    if provider != "lmstudio":
        pytest.skip(f"Unsupported ANANTA_E2E_TUI_LIVE_PROVIDER={provider!r}")

    last_error = ""
    for api_base in _lmstudio_candidates():
        try:
            models = _model_ids(api_base, token="")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = f"{api_base}: {exc}"
            continue
        configured_model = os.environ.get("ANANTA_TUI_LLM_MODEL") or os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL")
        model = str(configured_model or (models[0] if models else "meta-llama_-_llama-3.2-1b-instruct"))
        return LiveTuiLlm(provider="lmstudio", api_base=api_base, model=model)

    pytest.skip(f"LM Studio API not reachable. Last probe error: {last_error}")


def _probe_chat(llm: LiveTuiLlm) -> str:
    payload = {
        "model": llm.model,
        "messages": [{"role": "user", "content": "Sag kurz: online"}],
        "temperature": 0.0,
        "max_tokens": 24,
    }
    headers = {"Content-Type": "application/json"}
    if llm.api_token:
        headers["Authorization"] = f"Bearer {llm.api_token}"
    request = urllib.request.Request(
        url=f"{llm.api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=8.0) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    choices = parsed.get("choices") if isinstance(parsed, dict) else None
    assert isinstance(choices, list) and choices, "LM Studio returned no choices"
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    assert isinstance(message, dict), "LM Studio response has no message object"
    content = str(message.get("content") or "").strip()
    assert content, f"{llm.provider} response content is empty"
    return content


def test_snake_ai_live_llm_tui_cast_uses_configured_provider(monkeypatch) -> None:
    llm = _select_live_tui_llm()
    _probe_chat(llm)
    monkeypatch.setenv("ANANTA_TUI_LLM_API_BASE", llm.api_base)
    monkeypatch.setenv("ANANTA_TUI_LLM_MODEL", llm.model)
    monkeypatch.setenv("ANANTA_TUI_LLM_API_TOKEN", llm.api_token)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", llm.api_base)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_MODEL", llm.model)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_TOKEN", llm.api_token)

    payload = record_tui_demo(
        run_id=f"video-enable-snake-mode-live-{llm.provider}",
        flow_id=f"tui-snake-mode-live-{llm.provider}-video",
        enabled=True,
        scene="snake-mode-live-e2e",
        sync_targets=[],
    )

    assert payload["status"] == "recorded"
    video_path = _resolve_ref(payload["video_ref"])
    assert video_path.exists()
    assert video_path.name == "video-tui-snake-mode-live-e2e.cast"

    lines = [line for line in video_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert header["width"] >= 100
    assert header["height"] >= 28

    frame_text = "\n".join(json.loads(line)[2] for line in lines[1:])
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", frame_text)
    assert "ARTIFACTS" in plain
    assert "[Ctrl+S] Snake" in plain
    assert "backend=ananta-worker" in plain or "Chat: ananta-worker/" in plain
    assert "/snake/ask" in plain
    assert "CodeCompass" in plain or "codecompass" in plain
    assert "ANANTA-WORKER-CODECOMPASS-LMSTUDIO-CAST" in plain
    assert "Chat-Nachricht" in plain
    assert (
        "worker_v2" in plain
        or "last_chat_backend_path" in plain
        or "ANANTA-WORKER-CODECOMPASS-LMSTUDIO-CAST" in plain
        or "Tutorial-AI propose flow" in plain
        or "[user->artifacts]" in plain
        or "[openai-compatible->" in plain
        or "snake tutorial-ai: an" in plain
    ), plain[-2500:]

    synced_targets = list(payload.get("synced_cast_targets") or [])
    assert synced_targets == []
