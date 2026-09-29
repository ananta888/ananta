from __future__ import annotations

import os
from io import BytesIO

import pytest
import requests

_RUN_FLAG = "RUN_LIVE_VOXTRAL_TESTS"
_BASE_URL_ENV = "LIVE_VOXTRAL_RUNTIME_URL"


def _require_live_voxtral() -> str:
    if str(os.getenv(_RUN_FLAG) or "").strip() != "1":
        pytest.skip(f"set {_RUN_FLAG}=1 to run live Voxtral runtime tests")
    return str(os.getenv(_BASE_URL_ENV) or "http://localhost:8090").rstrip("/")


def test_live_voxtral_runtime_health_and_transcription() -> None:
    base_url = _require_live_voxtral()

    health = requests.get(f"{base_url}/health", timeout=5)
    assert health.status_code == 200
    health_payload = health.json()
    assert health_payload.get("ok") is True

    files = {"file": ("sample.webm", BytesIO(b"test-audio-bytes"), "audio/webm")}
    transcribe = requests.post(f"{base_url}/v1/audio/transcriptions", files=files, timeout=20)
    assert transcribe.status_code == 200
    payload = transcribe.json()
    assert isinstance(payload.get("text"), str)
    assert payload.get("text")
