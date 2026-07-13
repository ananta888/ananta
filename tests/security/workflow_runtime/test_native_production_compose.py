from __future__ import annotations

import json
from pathlib import Path

import yaml

from worker.runtime.workflow_adapter_worker_profile import (
    load_workflow_adapter_worker_profile,
)

ROOT = Path(__file__).resolve().parents[3]
OVERLAY = ROOT / "docker/compose-next/compose.native.production.yml"
PROFILE = ROOT / "config/workflow_runtime/native_worker_profile.v1.json"
AUTH_KEYRING = "workflow_runtime_auth_keyring"
HUB_TOKEN = "workflow_hub_service_token"


def _load(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _secret_sources(service: dict) -> set[str]:
    return {
        str(value["source"] if isinstance(value, dict) else value)
        for value in service.get("secrets", ())
    }


def test_native_production_overlay_uses_read_only_external_secrets() -> None:
    overlay = _load(OVERLAY)
    assert set(overlay["secrets"]) == {AUTH_KEYRING, HUB_TOKEN}
    rendered = OVERLAY.read_text(encoding="utf-8")
    assert "AGENT_TOKEN:" not in rendered
    assert "active_key_id:" not in rendered
    for service in overlay["services"].values():
        assert _secret_sources(service) == {AUTH_KEYRING, HUB_TOKEN}
        for binding in service["secrets"]:
            assert binding["source"] == binding["target"]
            assert int(binding["mode"]) & 0o222 == 0


def test_native_workers_share_verification_key_and_hub_service_token() -> None:
    services = _load(OVERLAY)["services"]
    hub = services["ai-agent-hub"]
    assert hub["environment"]["ANANTA_WORKFLOW_AUTH_KEYRING_FILE"] == (
        f"/run/secrets/{AUTH_KEYRING}"
    )
    assert hub["environment"]["AGENT_TOKEN_FILE"] == f"/run/secrets/{HUB_TOKEN}"
    for name in ("ai-agent-alpha", "ai-agent-beta"):
        environment = services[name]["environment"]
        assert environment["ANANTA_WORKFLOW_AUTH_KEYRING_FILE"] == (
            f"/run/secrets/{AUTH_KEYRING}"
        )
        assert environment["ANANTA_WORKFLOW_HUB_TOKEN_FILE"] == (
            f"/run/secrets/{HUB_TOKEN}"
        )
        assert environment["AGENT_TOKEN_FILE"] == f"/run/secrets/{HUB_TOKEN}"
        assert environment["ANANTA_WORKFLOW_HUB_URL"] == "http://ai-agent-hub:5000"


def test_native_worker_profile_is_typed_and_matches_runtime_capabilities() -> None:
    profile = load_workflow_adapter_worker_profile(str(PROFILE))
    native = profile.worker_runtime.native_graph
    assert native is not None and native.enabled
    assert {"coding", "testing", "verification"} <= set(native.allowed_task_types)
    assert {
        "approval",
        "bounded_parallel",
        "checkpoint",
        "deterministic_merge",
        "resume",
        "retrieval",
        "structured_output",
        "tool_calling",
    } <= set(native.capabilities)
    # The checked-in file must stay ordinary JSON without templated secret data.
    decoded = json.loads(PROFILE.read_text(encoding="utf-8"))
    assert "secret" not in json.dumps(decoded).lower()
