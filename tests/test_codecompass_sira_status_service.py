from __future__ import annotations

from types import SimpleNamespace

from agent.services.codecompass_sira_status_service import CodeCompassSiraStatusService


class WorkerStatus:
    def read(self):
        return {
            "status": "ready",
            "reason": "sira_layers_current",
            "base_layer_id": "base-1",
            "delta_layer_ids": ["delta-1"],
            "artifact_count": 12,
            "sensitive_query": "must not cross the boundary",
            "path": "/private/repository",
        }


class Catalog:
    def get(self, model_id: str):
        return {
            "provider_id": "ollama",
            "digest": "sha256:model",
            "local": True,
            "capabilities": ["chat", "code"],
        }


def _settings(mode: str = "preferred"):
    return SimpleNamespace(
        codecompass_fts_enabled=True,
        codecompass_vector_enabled=False,
        codecompass_graph_enabled=False,
        codecompass_relation_expansion_enabled=False,
        codecompass_sira_mode=mode,
        codecompass_sira_enrichment_model="",
        codecompass_sira_query_model="query-model" if mode != "off" else "",
        codecompass_sira_rerank_model="",
        codecompass_sira_reranker_enabled=False,
        codecompass_sira_local_models_only=True,
    )


def test_status_read_model_is_redacted_and_explainable():
    status = CodeCompassSiraStatusService().build(
        settings=_settings(),
        model_catalog=Catalog(),
        worker_status=WorkerStatus(),
    )
    assert status["status"] == "ready"
    assert status["rollout"]["result_affecting"] is True
    assert status["index"]["base_layer_id"] == "base-1"
    assert "sensitive_query" not in status["index"]
    assert "path" not in status["index"]


def test_off_status_needs_no_worker_or_model_catalog():
    status = CodeCompassSiraStatusService().build(settings=_settings("off"))
    assert status["status"] == "disabled"
    assert status["rollout"]["kill_switches"]["online_expansion"] is True
