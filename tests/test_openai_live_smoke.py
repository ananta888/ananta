import os

import pytest

from agent.config import settings
from agent.llm_integration import generate_text


LIVE_LLM_FLAG = "RUN_LIVE_LLM_TESTS"
LIVE_LLM_PROVIDER_ENV = "LIVE_LLM_PROVIDER"
LIVE_LLM_MODEL_ENV = "LIVE_LLM_MODEL"
LIVE_LLM_TIMEOUT_ENV = "LIVE_LLM_TIMEOUT_SEC"
LIVE_LLM_RETRY_ATTEMPTS_ENV = "LIVE_LLM_RETRY_ATTEMPTS"
LIVE_LLM_MAX_OUTPUT_TOKENS_ENV = "LIVE_LLM_MAX_OUTPUT_TOKENS"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

DEFAULT_OPENAI_SMOKE_MODEL = "gpt-4o-mini"
EXPECTED_SMOKE_TEXT = "ANANTA_LIVE_SMOKE_OK"


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(str(os.environ.get(name) or default).strip()))
    except (TypeError, ValueError):
        return default


def _require_openai_live_smoke() -> dict[str, str | int]:
    if str(os.environ.get(LIVE_LLM_FLAG) or "").strip() != "1":
        pytest.skip(f"Requires {LIVE_LLM_FLAG}=1.")

    provider = str(os.environ.get(LIVE_LLM_PROVIDER_ENV) or "").strip().lower()
    if provider != "openai":
        pytest.skip(f"Hosted CI smoke requires {LIVE_LLM_PROVIDER_ENV}=openai.")

    api_key = str(os.environ.get(OPENAI_API_KEY_ENV) or "").strip()
    if not api_key:
        pytest.skip(f"Hosted CI smoke requires {OPENAI_API_KEY_ENV}.")

    return {
        "provider": provider,
        "model": str(os.environ.get(LIVE_LLM_MODEL_ENV) or DEFAULT_OPENAI_SMOKE_MODEL).strip(),
        "api_key": api_key,
        "timeout": _env_int(LIVE_LLM_TIMEOUT_ENV, 20, 5),
        "retry_attempts": _env_int(LIVE_LLM_RETRY_ATTEMPTS_ENV, 1, 1),
        "max_output_tokens": _env_int(LIVE_LLM_MAX_OUTPUT_TOKENS_ENV, 16, 1),
    }


def test_openai_live_smoke_returns_bounded_expected_marker(monkeypatch):
    runtime = _require_openai_live_smoke()
    monkeypatch.setattr(settings, "retry_count", int(runtime["retry_attempts"]) - 1)
    monkeypatch.setattr(settings, "retry_backoff", 0.5)

    response = generate_text(
        prompt=f"Reply with exactly {EXPECTED_SMOKE_TEXT} and no other text.",
        provider="openai",
        model=str(runtime["model"]),
        api_key=str(runtime["api_key"]),
        timeout=int(runtime["timeout"]),
        temperature=0,
        max_output_tokens=int(runtime["max_output_tokens"]),
    )

    assert str(response or "").strip() == EXPECTED_SMOKE_TEXT
