"""layers_heads without profile_id and analytics_query without a DuckDB snapshot (MCP registry)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.timeout(30)


def _call(name, arguments, context=None):
    from agent.services.mcp_registry_service import get_mcp_registry_service

    result = get_mcp_registry_service().call_tool(name=name, arguments=arguments, context=context or {})
    return result["content"][0]["json"]


@pytest.mark.parametrize("profiles, expected", [(["other", "default"], "default"), (["only"], "only"),
                                                ([{"profile_id": "b"}, {"profile_id": "a"}], "b"), ([], None)])
def test_layers_heads_defaults_to_a_profile(monkeypatch, profiles, expected):
    service = SimpleNamespace(list_profiles=lambda: profiles, show_head=lambda profile_id: {"profile": profile_id})
    monkeypatch.setattr("agent.services.codecompass_layer_service.get_codecompass_layer_service", lambda: service)
    payload = _call("codecompass.layers_heads", {})
    assert payload["profile_id"] == expected
    assert payload["head"] == ({"profile": expected} if expected else None)
    assert _call("codecompass.layers_heads", {"profile_id": "x"})["head"] == {"profile": "x"}


def test_analytics_without_a_snapshot_is_unavailable_not_an_error(monkeypatch):
    from worker.retrieval.vector_store_contract import VectorStoreError

    def query(name, **kwargs):
        raise VectorStoreError("duckdb_snapshot_missing")

    monkeypatch.setattr("agent.services.codecompass_duckdb_analytics_service.get_codecompass_duckdb_analytics_service",
                        lambda: SimpleNamespace(query=query))
    payload = _call("codecompass.analytics_query", {"template": "document_counts_by_kind"})
    assert payload["status"] == "unavailable" and payload["reason"] == "duckdb_snapshot_missing"

    def broken(name, **kwargs):
        raise VectorStoreError("duckdb_corrupt")

    monkeypatch.setattr("agent.services.codecompass_duckdb_analytics_service.get_codecompass_duckdb_analytics_service",
                        lambda: SimpleNamespace(query=broken))
    with pytest.raises(VectorStoreError):
        _call("codecompass.analytics_query", {"template": "document_counts_by_kind"})
