from __future__ import annotations

from pathlib import Path

from agent.services.model_profile_loader import ModelProfileLoader
from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext, RoutingRules

ROOT = Path(__file__).resolve().parents[2]


def test_security_policy_blocks_every_ornith_assignment_until_enabled() -> None:
    result = ModelProfileLoader().load_file(ROOT / "config/models/local-ollama-phi-gemma-rtx3080.model_profiles.yaml")
    assert result.ok
    resolver = ModelProfileResolver(result.profiles, routing_rules=RoutingRules())

    for profile in result.profiles:
        if "ornith" not in profile.profile_id:
            continue
        decision = resolver.resolve(RoutingContext(request_profile_id=profile.profile_id))
        assert decision.profile is None or decision.profile.profile_id != profile.profile_id
        assert (profile.profile_id, "profile_disabled") in decision.blocked_candidates


def test_existing_local_fallbacks_stay_enabled_and_ordered() -> None:
    result = ModelProfileLoader().load_file(ROOT / "config/models/local-ollama-phi-gemma-rtx3080.model_profiles.yaml")
    enabled = [item for item in result.profiles if item.enabled]
    assert [item.profile_id for item in enabled] == [
        "local_ollama_phi4_mini", "local_ollama_gemma4_e4b_reasoning"
    ]
