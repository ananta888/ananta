from __future__ import annotations

from agent.config import settings
from agent.services.config_profile_service import LMSTUDIO_URL_PLACEHOLDER, ConfigProfileService


def test_lmstudio_profiles_use_the_configured_address(monkeypatch) -> None:
    monkeypatch.setattr(settings, "lmstudio_url", "http://lmstudio.example:1234/v1")
    service = ConfigProfileService()
    for profile_id in ("ananta_lmstudio_local", "opencode_lmstudio_local"):
        profile = service.get_profile(profile_id)
        assert profile["overrides"]["llm_config"]["base_url"] == "http://lmstudio.example:1234/v1"
    listed = {item["id"]: item for item in service.list_profiles()}
    hermes = listed["hermes_free_models_preconfigured"]["overrides"]["hermes_worker_adapter"]
    assert hermes["base_url"] == "http://lmstudio.example:1234/v1"
    assert LMSTUDIO_URL_PLACEHOLDER not in repr(listed)


def test_a_returned_profile_is_a_copy() -> None:
    service = ConfigProfileService()
    service.get_profile("ananta_lmstudio_local")["overrides"]["llm_config"]["base_url"] = "mutated"
    assert service.get_profile("ananta_lmstudio_local")["overrides"]["llm_config"]["base_url"] != "mutated"
