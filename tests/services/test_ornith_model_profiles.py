from __future__ import annotations

from pathlib import Path

from agent.services.local_model_resource_policy import LocalModelRuntimeProfileLoader
from agent.services.model_profile_loader import ModelProfileLoader
from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext, RoutingRules

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "config/models/local-ollama-phi-gemma-rtx3080.model_profiles.yaml"


def profiles():
    loaded = ModelProfileLoader().load_file(PROFILE_PATH)
    assert loaded.ok, loaded.errors
    return {item.profile_id: item for item in loaded.profiles}


def test_ornith_variants_and_provider_aliases_are_distinct_and_default_off() -> None:
    catalog = profiles()
    ornith = {key: value for key, value in catalog.items() if "ornith" in key}

    assert set(ornith) == {
        "local_ollama_ornith_1_5_9b_evaluation",
        "local_lmstudio_ornith_1_5_9b_evaluation",
        "local_llamacpp_ornith_1_5_35b_a3b_evaluation",
        "local_ornith_1_5_397b_unavailable",
    }
    assert all(item.enabled is False for item in ornith.values())
    assert len({(item.provider_id, item.model) for item in ornith.values()}) == 4
    assert ornith["local_ornith_1_5_397b_unavailable"].release_state == "unavailable"


def test_declared_capabilities_do_not_turn_static_routing_flags_on() -> None:
    catalog = profiles()
    for profile_id in (
        "local_ollama_ornith_1_5_9b_evaluation",
        "local_lmstudio_ornith_1_5_9b_evaluation",
        "local_llamacpp_ornith_1_5_35b_a3b_evaluation",
    ):
        model = catalog[profile_id]
        claims = {item["capability_id"]: item for item in model.capability_claims}
        assert claims["tools"]["value"] in {"supported", "unknown"}
        assert model.supports_tools is False
        assert model.supports_json is False
        assert model.tool_calling_mode == "none"
        assert model.verified_context_tokens is None


def test_disabled_ornith_request_cannot_override_hub_routing() -> None:
    catalog = list(profiles().values())
    resolver = ModelProfileResolver(
        catalog,
        routing_rules=RoutingRules(),
    )

    result = resolver.resolve(RoutingContext(
        request_profile_id="local_ollama_ornith_1_5_9b_evaluation",
        requires_tools=False,
    ))

    assert result.profile is not None
    assert result.profile.profile_id != "local_ollama_ornith_1_5_9b_evaluation"
    assert any(
        profile_id == "local_ollama_ornith_1_5_9b_evaluation"
        and reason == "profile_disabled"
        for profile_id, reason in result.blocked_candidates
    )


def test_runtime_profiles_bind_the_same_artifact_digests_as_model_profiles() -> None:
    catalog = profiles()
    runtime_paths = (
        "ornith-1.5-9b-rtx3080.v1.json",
        "ornith-1.5-35b-a3b-64gb-offload.v1.json",
    )
    for filename in runtime_paths:
        runtime = LocalModelRuntimeProfileLoader().load(ROOT / "config/runtime" / filename)
        configured = catalog[runtime.model_profile_id]
        assert configured.artifact_sha256 == runtime.artifact_sha256
        assert configured.hardware_class == runtime.hardware_class
        assert configured.nominal_context_tokens == 262144
