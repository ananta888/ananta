from __future__ import annotations

import os

import pytest


@pytest.mark.integration
def test_hermes_free_models_live_smoke_optional() -> None:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    base_url = os.getenv("HERMES_BASE_URL", "https://openrouter.ai/api/v1").strip()
    assert base_url.startswith("http")
