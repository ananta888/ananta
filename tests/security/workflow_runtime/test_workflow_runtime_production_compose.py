from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_OVERLAY = ROOT / "docker" / "compose-next" / "compose.temporal.production.yml"
TEMPORAL_OVERLAY = ROOT / "docker" / "compose-next" / "compose.temporal.yml"
PROBE_OVERLAY = ROOT / "docker" / "compose-next" / "compose.tests.temporal.yml"

AUTH_KEYRING = "workflow_runtime_auth_keyring"
DISPATCH_KEYRING = "workflow_runtime_dispatch_keyring"
HUB_TOKEN = "workflow_hub_service_token"


def _load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _secret_sources(service: dict) -> set[str]:
    return {str(binding["source"] if isinstance(binding, dict) else binding) for binding in service.get("secrets", ())}


def _assert_read_only_secret_bindings(service: dict) -> None:
    for binding in service.get("secrets", ()):
        assert isinstance(binding, dict)
        assert binding["target"] == binding["source"]
        assert int(binding["mode"]) & 0o222 == 0


def test_production_overlay_uses_external_file_secrets_only() -> None:
    overlay = _load(PRODUCTION_OVERLAY)

    assert set(overlay["secrets"]) == {AUTH_KEYRING, DISPATCH_KEYRING, HUB_TOKEN}
    for secret in overlay["secrets"].values():
        reference = str(secret["file"])
        assert reference.startswith("${ANANTA_WORKFLOW_")
        assert ":?Error:" in reference
    rendered = PRODUCTION_OVERLAY.read_text(encoding="utf-8")
    assert "AGENT_TOKEN:" not in rendered
    assert "keys:" not in rendered
    assert "active_key_id:" not in rendered


def test_hub_owns_dispatch_key_and_temporal_control_connection() -> None:
    hub = _load(PRODUCTION_OVERLAY)["services"]["ai-agent-hub"]
    environment = hub["environment"]

    assert _secret_sources(hub) == {AUTH_KEYRING, DISPATCH_KEYRING, HUB_TOKEN}
    _assert_read_only_secret_bindings(hub)
    assert environment["AGENT_TOKEN_FILE"] == f"/run/secrets/{HUB_TOKEN}"
    assert environment["ANANTA_WORKFLOW_AUTH_KEYRING_FILE"] == f"/run/secrets/{AUTH_KEYRING}"
    assert environment["ANANTA_WORKFLOW_DISPATCH_KEYRING_FILE"] == f"/run/secrets/{DISPATCH_KEYRING}"
    assert environment["ANANTA_ORCHESTRATION_BACKEND"] == "temporal"
    assert environment["ANANTA_TEMPORAL_ADDRESS"] == "temporal:7233"
    assert set(hub["networks"]) == {"default", "temporal-runtime"}


def test_temporal_worker_gets_verification_and_hub_credentials_but_not_dispatch_key() -> None:
    worker = _load(PRODUCTION_OVERLAY)["services"]["ananta-temporal-worker"]
    environment = worker["environment"]

    assert _secret_sources(worker) == {AUTH_KEYRING, HUB_TOKEN}
    _assert_read_only_secret_bindings(worker)
    assert DISPATCH_KEYRING not in _secret_sources(worker)
    assert environment["ANANTA_WORKFLOW_AUTH_KEYRING_FILE"] == f"/run/secrets/{AUTH_KEYRING}"
    assert environment["ANANTA_TEMPORAL_HUB_TOKEN_FILE"] == f"/run/secrets/{HUB_TOKEN}"
    assert environment["ANANTA_TEMPORAL_HUB_URL"] == "http://ai-agent-hub:5000"
    assert worker["depends_on"]["ai-agent-hub"]["condition"] == "service_healthy"


def test_probe_overlays_remain_secret_free_and_side_effect_free() -> None:
    temporal = _load(TEMPORAL_OVERLAY)
    probe = _load(PROBE_OVERLAY)

    assert "secrets" not in temporal
    assert "secrets" not in probe
    smoke = probe["services"]["temporal-smoke"]
    assert "ANANTA_TEMPORAL_HUB_URL" not in smoke.get("environment", {})
    assert "ANANTA_TEMPORAL_HUB_TOKEN_FILE" not in smoke.get("environment", {})
    assert smoke["entrypoint"] == ["python", "-m", "worker.temporal.smoke"]


def test_temporal_control_and_ui_ports_are_never_public_by_default() -> None:
    temporal = _load(TEMPORAL_OVERLAY)["services"]
    production = _load(PRODUCTION_OVERLAY)["services"]

    assert temporal["temporal"]["ports"] == [
        "${TEMPORAL_BIND_ADDRESS:-127.0.0.1}:${TEMPORAL_GRPC_PORT:-7233}:7233"
    ]
    assert temporal["temporal-ui"]["ports"] == [
        "${TEMPORAL_UI_BIND_ADDRESS:-127.0.0.1}:${TEMPORAL_UI_PORT:-8233}:8080"
    ]
    assert production["temporal"]["ports"] == [
        "127.0.0.1:${TEMPORAL_GRPC_PORT:-7233}:7233"
    ]
    assert production["temporal-ui"]["ports"] == [
        "127.0.0.1:${TEMPORAL_UI_PORT:-8233}:8080"
    ]
