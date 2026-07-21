from __future__ import annotations

import json
import re
from pathlib import Path

from agent.services.chat_setting_definitions import _DEFAULTS, _SCHEMA_KEYS
from agent.services.semantic_media_feature_flags import (
    SEMANTIC_MEDIA_FEATURE_CATALOG,
    SEMANTIC_MEDIA_FLAG_DEFAULTS,
    resolve_semantic_media_feature_flags,
)

ROOT = Path(__file__).resolve().parents[1]


def _angular_defaults() -> dict[str, bool]:
    source = (ROOT / "frontend-angular/src/app/services/network-profile.service.ts").read_text(encoding="utf-8")
    match = re.search(r"SEMANTIC_MEDIA_FEATURE_DEFAULTS[^=]*= Object\.freeze\(\{(?P<body>.*?)\}\);", source, re.S)
    assert match is not None
    pairs = re.findall(r"^\s*([a-z_]+):\s*(true|false),", match.group("body"), re.M)
    return {key: value == "true" for key, value in pairs}


def test_catalog_has_separate_owned_default_deny_capabilities() -> None:
    definitions = {item.key: item for item in SEMANTIC_MEDIA_FEATURE_CATALOG}
    assert set(definitions) == set(SEMANTIC_MEDIA_FLAG_DEFAULTS)
    assert len(definitions) == len(SEMANTIC_MEDIA_FEATURE_CATALOG)
    assert all(item.owner == "hub" for item in definitions.values())
    assert all(item.scope and item.default is False for item in definitions.values())
    assert "semantic_media_broadcast" in definitions
    assert "semantic_media_receiver_groups" in definitions
    assert "semantic_media_fleet_admission" in definitions
    assert "semantic_media_turn_cost_controls" in definitions
    assert all(key in _SCHEMA_KEYS and _DEFAULTS[key] is False for key in definitions)


def test_backend_and_angular_defaults_are_identical() -> None:
    assert _angular_defaults() == SEMANTIC_MEDIA_FLAG_DEFAULTS


def test_missing_unknown_and_malformed_values_fail_closed() -> None:
    assert resolve_semantic_media_feature_flags() == SEMANTIC_MEDIA_FLAG_DEFAULTS
    assert resolve_semantic_media_feature_flags({"unknown": True}) == SEMANTIC_MEDIA_FLAG_DEFAULTS
    for invalid in ("yes", "enabled", 2, 1, [], {}):
        resolved = resolve_semantic_media_feature_flags({"semantic_visual_capture": invalid})
        assert resolved["semantic_visual_capture"] is False


def test_dependencies_and_background_kill_switch_are_fail_closed() -> None:
    requested = {definition.key: True for definition in SEMANTIC_MEDIA_FEATURE_CATALOG}
    assert all(resolve_semantic_media_feature_flags(requested).values())

    killed = {**requested, "semantic_media_background_operations": False}
    resolved = resolve_semantic_media_feature_flags(killed)
    assert resolved["semantic_visual_capture"] is True
    assert resolved["semantic_speech_runtime"] is True
    assert resolved["semantic_media_sfu"] is True
    assert resolved["peer_evidence_sync"] is False
    assert resolved["speech_reconciliation"] is False
    assert resolved["speech_adaptation_training"] is False
    assert resolved["speech_adapter_routing"] is False


def test_broadcast_defaults_and_dependencies_are_fail_closed() -> None:
    requested = {"semantic_media_broadcast": True}
    assert resolve_semantic_media_feature_flags(requested)["semantic_media_broadcast"]
    assert resolve_semantic_media_feature_flags(requested)["semantic_media_receiver_groups"] is False
    assert resolve_semantic_media_feature_flags(requested)["semantic_media_fleet_admission"] is False
    assert resolve_semantic_media_feature_flags(requested)["semantic_media_turn_cost_controls"] is False

    requested = {
        "semantic_media_broadcast": True,
        "semantic_media_receiver_groups": True,
        "semantic_media_fleet_admission": True,
        "semantic_media_turn_cost_controls": True,
    }
    resolved = resolve_semantic_media_feature_flags(requested)
    assert resolved["semantic_media_broadcast"]
    assert resolved["semantic_media_receiver_groups"]
    assert resolved["semantic_media_fleet_admission"]
    assert resolved["semantic_media_turn_cost_controls"]


def test_network_profile_projects_only_effective_hub_flags(app, monkeypatch) -> None:
    from agent.routes import network_profiles

    profile = {
        "profile_id": "public-ananta",
        "label": "Test",
        "oidc": {},
        "rendezvous": {},
        "ice_servers": [],
    }
    monkeypatch.setattr(network_profiles, "_load_profiles", lambda: {"public-ananta": profile})
    monkeypatch.setattr(network_profiles, "oidc_is_configured", lambda: False)
    monkeypatch.setenv("ANANTA_SPEECH_ADAPTER_ROUTING_ENABLED", "true")
    monkeypatch.delenv("ANANTA_SEMANTIC_MEDIA_BACKGROUND_OPERATIONS_ENABLED", raising=False)

    with app.test_request_context("/api/network-profiles/public-ananta"):
        response = network_profiles.get_network_profile.__wrapped__("public-ananta")
    body = response.get_json()
    flags = body["profile"]["semantic_media_feature_flags"]
    assert flags == SEMANTIC_MEDIA_FLAG_DEFAULTS
    assert json.dumps(body["profile"]["semantic_media_feature_catalog"])
