from __future__ import annotations

import json
from pathlib import Path

from agent.services.local_model_resource_policy import LocalModelRuntimeProfileLoader
from scripts.benchmark.ornith_benchmark_support import require_loopback_endpoint

ROOT = Path(__file__).resolve().parents[2]


def test_cpu_contract_is_default_off_bounded_and_loopback_only() -> None:
    profile = LocalModelRuntimeProfileLoader().load(ROOT / "config/runtime/ornith-1.5-9b-rtx3080.v1.json")
    environment = (ROOT / "deploy/examples/ornith-ollama/.env.example").read_text()

    assert profile.production_default_allowed is False
    assert profile.default_context_tokens == 8192
    assert profile.maximum_parallel_requests == 1
    assert profile.requires_no_swap_growth is True
    assert "ANANTA_MODEL_IMPORT_NETWORK=disabled" in environment
    assert require_loopback_endpoint("http://127.0.0.1:11434") == "http://127.0.0.1:11434"


def test_live_hardware_result_starts_not_run_not_verified() -> None:
    result = json.loads((ROOT / "benchmarks/models/ornith-1.5-hardware-results.v1.json").read_text())
    assert result["state"] == "not_run"
    assert result["measurements"] == []
    assert result["production_claim"] is False
