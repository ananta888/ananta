from __future__ import annotations

from agent.config import settings
from agent.config_defaults import build_default_agent_config
from agent.services.codecompass_retrieval_flag_service import evaluate_codecompass_retrieval_flags


def test_codecompass_flags_are_independent(monkeypatch):
    monkeypatch.setattr(settings, "codecompass_fts_enabled", True)
    monkeypatch.setattr(settings, "codecompass_vector_enabled", False)
    monkeypatch.setattr(settings, "codecompass_graph_enabled", True)
    monkeypatch.setattr(settings, "codecompass_relation_expansion_enabled", False)

    diagnostics = evaluate_codecompass_retrieval_flags(settings=settings)

    assert diagnostics["codecompass_vector"]["status"] == "disabled"
    assert diagnostics["codecompass_graph"]["status"] in {"ready", "missing_dependency"}
    assert diagnostics["codecompass_fts"]["status"] in {"ready", "missing_dependency"}
    assert diagnostics["codecompass_relation_expansion"]["status"] == "disabled"


def test_codecompass_relation_expansion_degrades_without_graph(monkeypatch):
    monkeypatch.setattr(settings, "codecompass_fts_enabled", False)
    monkeypatch.setattr(settings, "codecompass_vector_enabled", False)
    monkeypatch.setattr(settings, "codecompass_graph_enabled", False)
    monkeypatch.setattr(settings, "codecompass_relation_expansion_enabled", True)

    diagnostics = evaluate_codecompass_retrieval_flags(settings=settings)

    assert diagnostics["codecompass_relation_expansion"]["status"] == "degraded"
    assert diagnostics["codecompass_relation_expansion"]["reason"] == "requires_graph_channel"


def test_worker_config_starts_with_all_codecompass_flags_disabled(monkeypatch):
    monkeypatch.setattr(settings, "codecompass_fts_enabled", False)
    monkeypatch.setattr(settings, "codecompass_vector_enabled", False)
    monkeypatch.setattr(settings, "codecompass_graph_enabled", False)
    monkeypatch.setattr(settings, "codecompass_relation_expansion_enabled", False)

    config = build_default_agent_config()
    diagnostics = evaluate_codecompass_retrieval_flags(settings=settings)

    assert config["worker_runtime"]["codecompass_retrieval"] == {
        "codecompass_fts": False,
        "codecompass_vector": False,
        "codecompass_graph": False,
        "codecompass_relation_expansion": False,
    }
    assert all(item["status"] == "disabled" for item in diagnostics.values())


def test_codecompass_flag_diagnostics_can_report_missing_dependency(monkeypatch):
    from agent.services import codecompass_retrieval_flag_service as service

    monkeypatch.setattr(settings, "codecompass_fts_enabled", True)
    monkeypatch.setattr(settings, "codecompass_vector_enabled", True)
    monkeypatch.setattr(settings, "codecompass_graph_enabled", True)
    monkeypatch.setattr(settings, "codecompass_relation_expansion_enabled", True)
    monkeypatch.setattr(service, "_sqlite_fts5_available", lambda: False)
    monkeypatch.setattr(service, "_vector_dependency_ready", lambda: False)
    diagnostics = service.evaluate_codecompass_retrieval_flags(settings=settings)
    assert diagnostics["codecompass_fts"]["status"] == "missing_dependency"
    assert diagnostics["codecompass_vector"]["status"] == "missing_dependency"
    assert diagnostics["codecompass_graph"]["status"] == "ready"
    assert diagnostics["codecompass_relation_expansion"]["status"] == "ready"
