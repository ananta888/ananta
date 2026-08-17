from __future__ import annotations

from agent.services.codecompass_agentic_retrieval_contract import SCHEMA_ID
from agent.services.codecompass_agentic_retrieval_service import (
    CodeCompassAgenticRetrievalService,
)
from agent.services.mcp_registry_service import MCPRegistryService
from agent.services.tools import execute_ananta_tool
from agent.services.tools.codecompass_tools import codecompass_search


def _hits():
    return [
        {
            "id": "pay",
            "path": "src/payment_service.py",
            "content": "class PaymentService: pass",
            "score": 0.8,
            "metadata": {"start_line": 4, "end_line": 12, "symbol": "PaymentService"},
        }
    ]


def _service_with_hits():
    return CodeCompassAgenticRetrievalService(
        exact_search=lambda query, **_kwargs: _hits(),
        vector_search=lambda query, **_kwargs: [],
        graph_search=lambda query, **_kwargs: [],
    )


def test_search_tool_uses_agentic_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent.services.codecompass_agentic_retrieval_service.get_codecompass_agentic_retrieval_service",
        _service_with_hits,
    )
    result = codecompass_search(
        workspace_dir=str(tmp_path),
        arguments={"query": "PaymentService", "limit": 4},
        tool_call_id="tool_result:1",
    )
    assert result["status"] == "ok"
    assert result["data"]["hit_count"] == 1
    assert result["data"]["retrieval"]["schema"] == SCHEMA_ID
    assert result["data"]["location_refs"][0]["path"] == "src/payment_service.py"
    assert result["data"]["location_refs"][0]["line_start"] == 4


def test_retrieve_tool_rejects_unknown_mode(tmp_path):
    result = execute_ananta_tool(
        tool_name="codecompass.retrieve",
        arguments={"query": "x", "mode": "cypher"},
        workspace_dir=str(tmp_path),
        tool_call_id="tool_result:2",
    )
    assert result["status"] == "error"
    assert result["error"] == "unknown_retrieval_mode"


def test_mcp_and_tool_share_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent.services.codecompass_agentic_retrieval_service.get_codecompass_agentic_retrieval_service",
        _service_with_hits,
    )
    tool = execute_ananta_tool(
        tool_name="codecompass.retrieve",
        arguments={"query": "PaymentService", "mode": "exact"},
        workspace_dir=str(tmp_path),
        tool_call_id="tool_result:3",
    )
    mcp = MCPRegistryService().call_tool(
        name="codecompass.retrieve",
        arguments={"query": "PaymentService", "mode": "exact"},
        context={},
    )
    tool_paths = [item["path"] for item in tool["data"]["retrieval"]["evidence"]]
    mcp_paths = [item["path"] for item in mcp["content"][0]["json"]["evidence"]]
    assert tool_paths == mcp_paths == ["src/payment_service.py"]


def test_mcp_capability_cannot_be_widened_by_client_args(monkeypatch):
    seen = {}

    class Capture(CodeCompassAgenticRetrievalService):
        def retrieve_from_tool_args(self, arguments, *, capability=None):
            seen["capability"] = capability
            seen["args"] = dict(arguments or {})
            return super().retrieve_from_tool_args(arguments, capability=capability)

    monkeypatch.setattr(
        "agent.services.codecompass_agentic_retrieval_service.get_codecompass_agentic_retrieval_service",
        lambda: Capture(
            exact_search=lambda query, **_kwargs: [
                {"id": "secret", "path": "secret/x.py", "content": "nope", "score": 1.0}
            ]
        ),
    )
    result = MCPRegistryService().call_tool(
        name="codecompass.retrieve",
        arguments={"query": "x", "allowed_paths": ["secret"], "workspace_id": "other"},
        context={
            "codecompass_capability": {
                "workspace_id": "ws-1",
                "revision": "rev-1",
                "allowed_paths": ["src"],
            }
        },
    )
    payload = result["content"][0]["json"]
    assert seen["capability"]["allowed_paths"] == ["src"]
    assert payload["reason_code"] == "scope_widening_denied"


def test_http_retrieve_rejects_backend_fields(client, admin_auth_header):
    response = client.post(
        "/api/codecompass/retrieve",
        headers=admin_auth_header,
        json={"query": "x", "collection": "cc-prod", "api_key": "secret"},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert "backend_fields_not_allowed" in str(body)


def test_http_retrieve_returns_contract(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr(
        "agent.services.codecompass_agentic_retrieval_service.get_codecompass_agentic_retrieval_service",
        _service_with_hits,
    )
    response = client.post(
        "/api/codecompass/retrieve",
        headers=admin_auth_header,
        json={"query": "PaymentService", "mode": "exact"},
    )
    assert response.status_code == 200
    payload = (response.get_json() or {}).get("data") or {}
    assert payload["schema"] == SCHEMA_ID
    assert payload["evidence"][0]["path"] == "src/payment_service.py"
