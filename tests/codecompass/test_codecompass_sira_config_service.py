from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.codecompass_sira_config_service import CodeCompassSiraConfigService


class Catalog:
    def __init__(self, *, local: bool = True):
        self.local = local

    def get(self, model_id: str):
        if model_id != "query-model":
            return None
        return {
            "provider_id": "ollama" if self.local else "external",
            "digest": "sha256:model",
            "local": self.local,
            "capabilities": ["chat", "code"],
        }


def _settings(**overrides):
    values = {
        "codecompass_sira_mode": "preferred",
        "codecompass_sira_online_expansion_enabled": True,
        "codecompass_sira_enrichment_model": "",
        "codecompass_sira_query_model": "query-model",
        "codecompass_sira_rerank_model": "",
        "codecompass_sira_reranker_enabled": False,
        "codecompass_sira_local_models_only": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_hub_resolves_capability_checked_local_model():
    resolved = CodeCompassSiraConfigService().resolve(settings=_settings(), model_catalog=Catalog())
    assert resolved["mode"] == "preferred"
    assert resolved["resolved_models"]["query"]["model_digest"] == "sha256:model"
    assert resolved["resolved_models"]["query"]["local"] is True


def test_hub_rejects_external_model_under_local_only_policy():
    with pytest.raises(ValueError, match="sira_query_model_external_denied"):
        CodeCompassSiraConfigService().resolve(settings=_settings(), model_catalog=Catalog(local=False))


def test_off_mode_needs_no_model_catalog():
    resolved = CodeCompassSiraConfigService().resolve(
        settings=_settings(codecompass_sira_mode="off", codecompass_sira_query_model="")
    )
    assert resolved["mode"] == "off"


def test_online_kill_switch_forces_off_without_needing_model_catalog():
    resolved = CodeCompassSiraConfigService().resolve(
        settings=_settings(codecompass_sira_online_expansion_enabled=False)
    )

    assert resolved["mode"] == "off"
