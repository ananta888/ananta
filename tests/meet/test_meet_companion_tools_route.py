"""Worker-key CodeCompass MCP tools for the companion: allowlist, policy, bounds, codes."""

import json
import time
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.routes import meet_companion_tools as route
from agent.routes.meet import meet_bp
from agent.services.mcp_registry_service import get_mcp_registry_service
from agent.services.meet_contract import MeetError
from agent.services.operation_registry_service import get_operation_registry_service, mcp_tool_operation_id
from worker.meet_media.contract import encode, signature


def page(records):
    """The ``search_records_page`` shape: records plus the total hit count."""
    return {"records": records, "total": len(records) + 30}


pytestmark = pytest.mark.timeout(30)
KEY = b"synthetic-companion-tool-key-000000"
PATH = "/api/meet/v1/internal/assist/tool"
PROJECT = "project-a"

VALID = {
    "codecompass.architecture_overview": {"query": "Meet Companion", "profile": "overview"},
    "codecompass.architecture_expand": {"handle": "hac:0123456789ab:worker.meet_media", "query": "tools"},
    "codecompass.architecture_intelligence": {"snapshot_ref": "snap-1"},
    "codecompass.layers_heads": {"profile_id": "default"},
    "codecompass.layers_plan": {"old_manifest": {"files": {}}, "new_manifest": {"files": {"a.py": "x"}}},
    "codecompass.analytics_query": {"template": "paths_for_kind", "kind": "python"},
    "codecompass.rlm_analyze": {"query": "Wie greift der Companion auf CodeCompass zu?", "enabled": True},
}


class Registry:
    """Stand-in for the MCP registry dispatch; records exactly what reached it."""

    def __init__(self, result=None, error=None, delay=0.0):
        self.result = {"status": "ok"} if result is None else result
        self.error = error
        self.delay = delay
        self.calls = []

    def call_tool(self, *, name, arguments, context):
        self.calls.append((name, arguments, context))
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return {"content": [{"type": "json", "json": self.result}]}


def _principal(project):
    if project != PROJECT:
        raise MeetError("meet_media_policy_denied", 403)
    return Mock(subject_id="ananta-companion", tenant_id="tenant-a", project_id=project)


@pytest.fixture
def registry(monkeypatch):
    fake = Registry()
    monkeypatch.setattr("agent.services.mcp_registry_service.get_mcp_registry_service", lambda: fake)
    return fake


@pytest.fixture
def app(monkeypatch, registry):
    monkeypatch.delenv("ANANTA_MEET_COMPANION_MCP_TOOLS", raising=False)
    monkeypatch.delenv("ANANTA_MEET_COMPANION_TOOL_TIMEOUT_SECONDS", raising=False)
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(meet_bp)
    app.extensions["meet_binding_service"] = Mock()
    app.extensions["meet_media_worker_key"] = KEY
    app.extensions["meet_turn_service"] = Mock(companion_principal=Mock(side_effect=_principal))
    retrieval = Mock()
    retrieval.search_records_page.return_value = page([
        {"path": "", "content": "pathless", "score": 9.0, "metadata": {}},
        {"path": "worker/meet_media/llm_tools.py", "content": "class ToolBox", "score": 0.8,
         "symbol": "ToolBox", "metadata": {"line_start": 12}},
    ])
    monkeypatch.setattr(
        "agent.services.knowledge_index_retrieval_service.KnowledgeIndexRetrievalService",
        lambda *args, **kwargs: retrieval,
    )
    app.retrieval = retrieval
    return app


def post(app, payload, *, key=KEY, signed=True, path=PATH):
    body = payload if isinstance(payload, bytes) else encode(payload)
    headers = {"Content-Type": "application/json"}
    if signed:
        headers["X-Ananta-Task-Signature"] = signature(key, body)
    return app.test_client().post(path, data=body, headers=headers)


def call(app, tool, arguments=None, **kwargs):
    return post(app, {"project_id": PROJECT, "tool": tool, "arguments": arguments or {}}, **kwargs)


def code(response):
    return json.loads(response.data)["error"]["code"]


# --- authentication ---------------------------------------------------------


@pytest.mark.parametrize("key, signed", [(b"another-worker-key-000000000000000", True), (KEY, False)])
def test_a_wrong_or_missing_worker_key_is_401_and_nothing_runs(app, registry, key, signed):
    response = call(app, "codecompass.layers_heads", key=key, signed=signed)
    assert response.status_code == 401 and code(response) == "meet_tool_unauthorized"
    assert registry.calls == []


def test_a_user_bearer_token_is_not_a_worker_key(app, registry):
    body = encode({"project_id": PROJECT, "tool": "codecompass.layers_heads", "arguments": {}})
    response = app.test_client().post(
        PATH, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer abc.def.ghi"}
    )
    assert response.status_code == 401 and registry.calls == []


def test_without_media_wiring_the_route_is_disabled(app, registry):
    del app.extensions["meet_turn_service"]
    assert call(app, "codecompass.layers_heads").status_code == 404
    assert registry.calls == []


def test_a_project_outside_the_media_scopes_is_denied(app, registry):
    response = post(app, {"project_id": "project-b", "tool": "codecompass.layers_heads", "arguments": {}})
    assert response.status_code == 403 and code(response) == "meet_media_policy_denied"
    assert registry.calls == []


# --- allowlist and policy ---------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    [
        "classroom.transcript_event",
        "classroom.reanalyze",
        "evolution.analyze",
        "tasks.list",
        "tasks.get",
        "health.get",
        "artifacts.list",
        "knowledge.list_collections",
        "codecompass.unknown",
        "shell.exec",
    ],
)
def test_anything_outside_the_read_codecompass_family_is_denied(app, registry, tool):
    response = call(app, tool, {"task_id": "t"})
    assert response.status_code == 403 and code(response) == "meet_tool_denied"
    assert registry.calls == []


def test_the_allowlist_is_read_only_codecompass_and_registered_as_such():
    specs = {spec.name: spec for spec in get_mcp_registry_service().tool_specs()}
    operations = get_operation_registry_service()
    read_group = set(operations.group_members("mcp.read.v1"))
    write_group = set(operations.group_members("mcp.write.v1"))
    assert set(route.COMPANION_TOOLS) == {name for name in specs if name.startswith("codecompass.")}
    for name in route.COMPANION_TOOLS:
        descriptor = operations.get_for_target(transport="mcp.tool", target=name)
        assert specs[name].access_class == "read" and descriptor.access_class == "read"
        assert not descriptor.side_effecting
        assert mcp_tool_operation_id(name) in read_group and mcp_tool_operation_id(name) not in write_group


def test_the_operator_can_narrow_but_never_widen_the_allowlist(app, registry, monkeypatch):
    monkeypatch.setenv("ANANTA_MEET_COMPANION_MCP_TOOLS", "codecompass.layers_heads, evolution.analyze")
    assert call(app, "codecompass.layers_heads").status_code == 200
    assert call(app, "codecompass.architecture_overview", {"query": "x"}).status_code == 403
    assert call(app, "evolution.analyze", {"task_id": "t"}).status_code == 403
    monkeypatch.setenv("ANANTA_MEET_COMPANION_MCP_TOOLS", "none")
    assert call(app, "codecompass.layers_heads").status_code == 403
    assert [name for name, _args, _context in registry.calls] == ["codecompass.layers_heads"]


def test_the_operator_operation_policy_applies_to_the_companion(app, registry):
    app.config["AGENT_CONFIG"] = {
        "operation_policy": {
            "enabled": True,
            "allow_groups": ["mcp.read.v1"],
            "deny_operations": ["mcp.tool.codecompass.rlm_analyze"],
        }
    }
    denied = call(app, "codecompass.rlm_analyze", {"query": "x"})
    assert denied.status_code == 403 and code(denied) == "meet_tool_denied"
    assert call(app, "codecompass.layers_heads").status_code == 200
    app.config["AGENT_CONFIG"] = {"operation_policy": {"enabled": True, "allow_groups": ["api.read.v1"]}}
    assert call(app, "codecompass.layers_heads").status_code == 403
    assert [name for name, _args, _context in registry.calls] == ["codecompass.layers_heads"]


def test_an_invalid_operator_policy_fails_closed(app, registry):
    app.config["AGENT_CONFIG"] = {"operation_policy": "not-an-object"}
    assert call(app, "codecompass.layers_heads").status_code == 403
    assert registry.calls == []


# --- dispatch ---------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(VALID))
def test_each_tool_is_dispatched_through_the_mcp_registry(app, registry, tool):
    registry.result = {"status": "ok", "evidence": [{"path": "docs/meet.md", "excerpt": "Meet", "line": 3}]}
    response = call(app, tool, VALID[tool])
    assert response.status_code == 200, response.data
    payload = json.loads(response.data)
    assert payload["schema"] == route.SCHEMA and payload["tool"] == tool
    assert json.loads(payload["text"]) == registry.result and payload["truncated"] is False
    assert payload["sources"] == [
        {"path": "docs/meet.md", "line": 3, "symbol": "", "revision": "", "excerpt": "Meet"}
    ]
    (name, arguments, context), = registry.calls
    assert name == tool and arguments == VALID[tool]
    # Only the (fail-closed) capability reaches the tool; no repositories, no services.
    assert context == {"codecompass_capability": None}


def test_retrieve_uses_the_assist_retrieval_path_and_returns_citable_snippets(app, registry):
    response = call(app, "codecompass.retrieve", {"query": "ToolBox", "limit": 1, "mode": "hybrid"})
    assert response.status_code == 200
    payload = json.loads(response.data)
    assert [source["path"] for source in payload["sources"]] == ["worker/meet_media/llm_tools.py"]
    assert payload["sources"][0]["line"] == 12 and payload["sources"][0]["symbol"] == "ToolBox"
    assert app.retrieval.search_records_page.call_args.kwargs["limit"] == 2
    assert registry.calls == []


def test_the_legacy_assist_retrieve_route_is_unchanged(app):
    body = encode({"query": "ToolBox", "limit": 2})
    response = app.test_client().post(
        "/api/meet/v1/internal/assist/retrieve",
        data=body,
        headers={"Content-Type": "application/json", "X-Ananta-Task-Signature": signature(KEY, body)},
    )
    payload = json.loads(response.data)
    assert payload["schema"] == "ananta.meet-assist-retrieve.v1"
    assert [snippet["path"] for snippet in payload["snippets"]] == ["worker/meet_media/llm_tools.py", ""]


# --- bounds and error codes -------------------------------------------------


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("codecompass.architecture_overview", {}),
        ("codecompass.architecture_overview", {"query": "   "}),
        ("codecompass.architecture_overview", {"query": "x" * 1001}),
        ("codecompass.architecture_expand", {"query": "ohne handle"}),
        ("codecompass.layers_plan", {"old_manifest": {}}),
        ("codecompass.layers_plan", {"old_manifest": "{}", "new_manifest": {}}),
        ("codecompass.analytics_query", {}),
        ("codecompass.analytics_query", {"template": "SELECT * FROM documents"}),
        ("codecompass.rlm_analyze", {"query": "x", "enabled": "yes"}),
        ("codecompass.layers_heads", {"collection": "qdrant-raw"}),
        ("codecompass.retrieve", {"query": "x", "allowed_paths": ["/"]}),
        ("codecompass.retrieve", {"query": "x", "capability": {"tenant_id": "t"}}),
    ],
)
def test_missing_unknown_or_out_of_bound_arguments_are_invalid(app, registry, tool, arguments):
    response = call(app, tool, arguments)
    assert response.status_code == 400 and code(response) == "meet_tool_arguments_invalid"
    assert registry.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"tool": "codecompass.layers_heads", "arguments": {}},
        {"project_id": PROJECT, "arguments": {}},
        {"project_id": PROJECT, "tool": "codecompass.layers_heads", "arguments": [], "extra": 1},
        {"project_id": PROJECT, "tool": ["codecompass.layers_heads"]},
        {"project_id": PROJECT, "tool": "codecompass.layers_heads", "arguments": "{}"},
    ],
)
def test_the_payload_is_closed(app, registry, payload):
    response = post(app, payload)
    assert response.status_code in (400, 403)
    assert code(response) in ("meet_tool_payload_invalid", "meet_tool_arguments_invalid")
    assert registry.calls == []


def test_an_oversized_body_or_a_query_string_is_rejected_before_authentication(app, registry):
    big = {"old_manifest": {"blob": "x" * route.MAX_BODY_BYTES}, "new_manifest": {}}
    assert call(app, "codecompass.layers_plan", big).status_code == 400
    body = encode({"project_id": PROJECT, "tool": "codecompass.layers_heads", "arguments": {}})
    response = app.test_client().post(
        PATH + "?debug=1", data=body,
        headers={"Content-Type": "application/json", "X-Ananta-Task-Signature": signature(KEY, body)},
    )
    assert response.status_code == 400 and registry.calls == []


def test_recursion_bounds_are_clamped_before_dispatch(app, registry):
    call(app, "codecompass.rlm_analyze", {"query": "x", "max_depth": 99, "max_fanout": 50})
    assert registry.calls[0][1] == {"query": "x", "max_depth": 2, "max_fanout": 3}


def test_a_large_result_is_pruned_and_serialized_within_the_bound(app, registry):
    registry.result = {"nodes": [{"id": str(index), "summary": "y" * 5000} for index in range(500)]}
    payload = json.loads(call(app, "codecompass.layers_heads").data)
    assert len(payload["text"]) <= route.MAX_RESULT_CHARS and payload["truncated"] is True


@pytest.mark.parametrize(
    "error, status, expected",
    [
        (KeyError("unknown_tool"), 404, "meet_tool_not_found"),
        (ValueError("client_authority_forbidden"), 400, "meet_tool_arguments_invalid"),
        (RuntimeError("SYNTHETIC_QDRANT_SECRET at /srv/private"), 502, "meet_tool_error"),
    ],
)
def test_tool_failures_become_coded_errors_without_reasons(app, registry, error, status, expected):
    registry.error = error
    response = call(app, "codecompass.layers_heads")
    assert response.status_code == status and code(response) == expected
    assert b"SYNTHETIC" not in response.data and b"/srv" not in response.data


def test_a_slow_tool_times_out(app, registry, monkeypatch):
    monkeypatch.setenv("ANANTA_MEET_COMPANION_TOOL_TIMEOUT_SECONDS", "1")
    registry.delay = 2.0
    response = call(app, "codecompass.layers_heads")
    assert response.status_code == 504 and code(response) == "meet_tool_timeout"


def test_every_call_is_audited(app, registry, monkeypatch):
    events = []
    monkeypatch.setattr("agent.common.audit.log_audit", lambda action, details=None: events.append((action, details)))
    call(app, "codecompass.layers_heads")
    call(app, "evolution.analyze", {"task_id": "t"})
    outcomes = [details["outcome"] for action, details in events if action == "meet_companion_tool_called"]
    assert outcomes == ["success", "blocked"]


def test_bounded_text_and_evidence_helpers_stay_bounded():
    deep = current = {}
    for _ in range(50):
        current["next"] = {}
        current = current["next"]
    text, truncated = route.bounded_text(deep)
    assert "…" in text and truncated is False
    assert len(route.evidence({"items": [{"path": "p%d" % index} for index in range(100)]})) == 6
