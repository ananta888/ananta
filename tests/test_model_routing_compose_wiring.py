from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_base_compose_passes_release_control_settings_to_hub():
    environment = _load("docker/compose-next/compose.base.yml")["services"]["ai-agent-hub-base"]["environment"]

    assert environment["FEATURE_MODEL_CATALOG_V2_ENABLED"] == "${FEATURE_MODEL_CATALOG_V2_ENABLED:-0}"
    assert environment["FEATURE_MODEL_ROUTING_EDITOR_ENABLED"] == "${FEATURE_MODEL_ROUTING_EDITOR_ENABLED:-0}"
    assert (
        environment["FEATURE_LEGACY_MODEL_PICKER_DEPRECATION_ENABLED"]
        == "${FEATURE_LEGACY_MODEL_PICKER_DEPRECATION_ENABLED:-0}"
    )
    assert environment["MODEL_ROUTING_RELEASE_EVIDENCE_PATH"] == (
        "${MODEL_ROUTING_RELEASE_EVIDENCE_PATH:-/app/artifacts/test-gates/model-routing-release-gate.json}"
    )


def test_local_model_overlay_activates_released_hub_editor_only():
    services = _load("docker/compose-next/compose.local-kat-lfm-needle.yml")["services"]
    hub = services["ai-agent-hub"]["environment"]

    assert hub["FEATURE_MODEL_CATALOG_V2_ENABLED"] == "1"
    assert hub["FEATURE_MODEL_ROUTING_EDITOR_ENABLED"] == "1"
    assert hub["FEATURE_LEGACY_MODEL_PICKER_DEPRECATION_ENABLED"] == "1"
    assert "ANANTA_LOCAL_MODEL_CONTROL_TOKEN" in hub
    for worker_id in ("ai-agent-alpha", "ai-agent-beta"):
        worker = services[worker_id]["environment"]
        assert "MODEL_PROFILES_PATH" in worker
        assert worker["DEFAULT_PROVIDER"] == "llamacpp"
        assert worker["DEFAULT_MODEL"] == "lfm2.5-2.6b-agentic-q8_0"
        assert "FEATURE_MODEL_ROUTING_EDITOR_ENABLED" not in worker
        assert "ANANTA_LOCAL_MODEL_CONTROL_URL" not in worker
        assert "ANANTA_LOCAL_MODEL_CONTROL_TOKEN" not in worker
