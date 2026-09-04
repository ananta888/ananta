from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_matrix_is_default_off_and_colibri_is_exactly_unsupported() -> None:
    matrix = json.loads((ROOT / "config/providers/local-qwen3.8-abliterated.v1.json").read_text())
    providers = {item["provider_id"]: item for item in matrix["providers"]}

    assert matrix["model_architecture"] == "qwen35"
    assert matrix["production_allowed"] is False
    assert providers["colibri"]["state"] == "unsupported"
    assert "qwen38_dense_engine_not_supported" in providers["colibri"]["reason_codes"]
    assert all(item["remote_code_allowed"] is False for item in providers.values())
