from __future__ import annotations

from pathlib import Path

from agent.services.model_profile_loader import ModelProfileLoader
from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext, RoutingRules

ROOT = Path(__file__).resolve().parents[2]
PROFILE_ID = "local_llamacpp_qwen38_27b_abliterated_ud_iq3_research"


def test_disabled_profile_alias_and_direct_id_cannot_enter_normal_routing() -> None:
    loaded = ModelProfileLoader().load_file(ROOT / "config/models/local-ollama-phi-gemma-rtx3080.model_profiles.yaml")
    assert loaded.ok, loaded.errors
    profile = next(item for item in loaded.profiles if item.profile_id == PROFILE_ID)
    assert profile.trust_class == "unsafe_research"
    assert profile.safety_modified is True
    assert profile.enabled is False
    resolver = ModelProfileResolver(loaded.profiles, routing_rules=RoutingRules())

    decision = resolver.resolve(RoutingContext(request_profile_id=PROFILE_ID))

    assert decision.profile is None or decision.profile.profile_id != PROFILE_ID
    assert (PROFILE_ID, "profile_disabled") in decision.blocked_candidates


def test_security_checker_blocks_accidentally_enabled_unsafe_profile() -> None:
    loaded = ModelProfileLoader().load_file(ROOT / "config/models/local-ollama-phi-gemma-rtx3080.model_profiles.yaml")
    profile = next(item for item in loaded.profiles if item.profile_id == PROFILE_ID)
    profile.enabled = True
    resolver = ModelProfileResolver([profile], routing_rules=RoutingRules())

    decision = resolver.resolve(RoutingContext(request_profile_id=PROFILE_ID))

    assert decision.profile is None
    assert (PROFILE_ID, "security_policy:unsafe_research_normal_routing_forbidden") in decision.blocked_candidates
