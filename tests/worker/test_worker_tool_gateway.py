"""WCRB-008/011: path A (the Hub runs CodeCompass tools for a worker) and the configurable access path."""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.services import codecompass_hub_tool_client as client_module
from agent.services import codecompass_task_capability as tc
from agent.services import worker_tool_gateway as gw
from agent.services.access_roles import BUILTIN_ROLES

pytestmark = pytest.mark.timeout(30)
ROLES = {role_id: spec["grants"] for role_id, spec in BUILTIN_ROLES.items()}
WORKER = "http://ai-agent-alpha:5000"


class _Registry:
    def __init__(self):
        self.descriptors = {
            "ananta.tool.codecompass.search": SimpleNamespace(access_class="read", side_effecting=False),
            "ananta.tool.codecompass.write_notes": SimpleNamespace(access_class="write", side_effecting=True),
        }

    def get(self, operation_id):
        return self.descriptors.get(operation_id)

    def groups_for(self, operation_id):
        access = self.descriptors[operation_id].access_class
        return ("ananta.tool.read.v1",) if access == "read" else ("ananta.tool.write.v1",)


def _task(**overrides):
    fields = {"id": "t1", "assigned_agent_url": WORKER + "/", "status": "in_progress", "parent_task_id": None,
              "requested_by_subject": "anna", "requested_by_tenant": "t", "requested_roles": ["viewer"]}
    return SimpleNamespace(**{**fields, **overrides})


def _gateway(task, *, capability=True, executed=None, allowed=("codecompass.search", "codecompass.write_notes")):
    tasks = {"t1": task} if task is not None else {}
    issued = []

    def issue(task, **kwargs):
        issued.append(kwargs)
        return {"for": task.id, "aud": kwargs["audience"]} if capability else None

    def execute(**kwargs):
        (executed if executed is not None else []).append(kwargs)
        return {"status": "ok", "tool": kwargs["tool_name"]}

    gateway = gw.WorkerToolGateway(get_task=tasks.get, role_grants=lambda: ROLES, registry=_Registry(),
                                   allowed_tools=lambda: list(allowed), issue_capability=issue, execute_tool=execute)
    return gateway, issued


def test_an_admitted_call_gets_a_hub_capability_for_the_requester():
    gateway, issued = _gateway(_task())
    task, capability = gateway.admit("t1", "codecompass.search", worker_url=WORKER)
    assert task.id == "t1" and capability == {"for": "t1", "aud": "hub"}
    assert issued[0]["audience"] == "hub"


@pytest.mark.parametrize("task, tool, worker, reason", [
    (None, "codecompass.search", WORKER, "task_not_found"),
    (_task(), "codecompass.search", "http://ai-agent-beta:5000", "task_not_assigned_to_worker"),
    (_task(assigned_agent_url=None), "codecompass.search", WORKER, "task_not_assigned_to_worker"),
    (_task(status="completed"), "codecompass.search", WORKER, "task_not_active"),
    (_task(), "repo.grep", WORKER, "tool_not_allowed"),
    (_task(), "codecompass.retrieve", WORKER, "tool_not_allowed"),
    (_task(), "codecompass.write_notes", WORKER, "tool_not_read_only"),
    (_task(requested_roles=["nobody"]), "codecompass.search", WORKER, "role_denied"),
])
def test_everything_else_is_refused(task, tool, worker, reason):
    gateway, _issued = _gateway(task)
    with pytest.raises(gw.GatewayDenied, match=reason):
        gateway.admit("t1", tool, worker_url=worker)


def test_no_capability_means_no_call_and_system_work_is_unrestricted():
    with pytest.raises(gw.GatewayDenied, match="capability_unavailable"):
        _gateway(_task(), capability=False)[0].admit("t1", "codecompass.search", worker_url=WORKER)
    system = _task(requested_by_subject=None, requested_roles=None)
    assert _gateway(system)[0].admit("t1", "codecompass.search", worker_url=WORKER)[1]["for"] == "t1"


def test_the_hub_runs_the_tool_locally_under_its_capability(monkeypatch):
    executed = []
    gateway, _issued = _gateway(_task(), executed=executed)
    result = gateway.execute("codecompass.search", {"query": "x"}, tool_call_id="c1", capability={"cap": 1})
    assert result == {"status": "ok", "tool": "codecompass.search"}
    assert executed[0]["config"] == {"codecompass_capability": {"cap": 1}, "codecompass_access": "delegated"}
    monkeypatch.setattr(gw, "MAX_RESULT_BYTES", 10)
    assert gateway.execute("codecompass.search", {}, tool_call_id="c1", capability={})["error"] == \
        "hub_gateway_result_too_large"


# --- route --------------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    from flask import g

    import agent.auth as auth
    from agent.routes import worker_tool_gateway as route

    def registered(required_scope):
        assert required_scope == "codecompass.tools.execute"
        g.service_identity = {"worker_id": "w", "worker_url": WORKER}

    events = []
    monkeypatch.setattr(auth, "_strict_registered_worker_bearer_error", registered)
    monkeypatch.setattr(gw, "audit_tool_call", lambda event, name, **kwargs: events.append((name, kwargs["outcome"])))
    monkeypatch.setattr(gw, "get_worker_tool_gateway", lambda: _gateway(_task())[0])
    app = Flask(__name__)
    app.register_blueprint(route.worker_tool_gateway_bp)
    test_client = app.test_client()
    test_client.events = events
    return test_client


def test_the_route_runs_admitted_calls_and_audits_every_one(client):
    ok = client.post("/api/worker/v1/tasks/t1/tools/codecompass.search",
                     json={"arguments": {"query": "x"}, "tool_call_id": "c1"})
    assert ok.status_code == 200 and ok.get_json() == {"status": "ok", "tool": "codecompass.search"}
    denied = client.post("/api/worker/v1/tasks/t1/tools/codecompass.write_notes", json={"arguments": {}})
    assert denied.status_code == 403 and denied.get_json()["data"]["reason_code"] == "tool_not_read_only"
    assert client.post("/api/worker/v1/tasks/t1/tools/codecompass.search",
                       json={"arguments": [], "x": 1}).status_code == 400
    assert client.events == [("codecompass.search", "success"), ("codecompass.write_notes", "blocked")]


# --- worker client ------------------------------------------------------------------------------


class _Opener:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.requests = body, error, []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return io.BytesIO(json.dumps(self.body).encode())


def _client(opener):
    return client_module.HubToolGatewayClient(hub_url="http://hub:5000", headers={"Authorization": "Bearer t"},
                                              opener=opener)


def test_the_worker_client_posts_to_the_task_bound_gateway():
    opener = _Opener(body={"status": "ok"})
    result = _client(opener).execute(task_id="t 1", tool_name="codecompass.search", arguments={"query": "x"},
                                     tool_call_id="c1")
    request = opener.requests[0]
    assert result == {"status": "ok"}
    assert request.full_url == "http://hub:5000/api/worker/v1/tasks/t%201/tools/codecompass.search"
    assert json.loads(request.data) == {"arguments": {"query": "x"}, "tool_call_id": "c1"}


@pytest.mark.parametrize("opener, task_id, error", [
    (_Opener(error=urllib.error.HTTPError("u", 403, "no", {}, io.BytesIO(
        b'{"data": {"reason_code": "role_denied:x"}}'))), "t1", "hub_gateway_role_denied:x"),
    (_Opener(error=urllib.error.HTTPError("u", 502, "no", {}, io.BytesIO(b"x"))), "t1", "hub_gateway_http_502"),
    (_Opener(error=OSError("down")), "t1", "hub_gateway_unreachable"),
    (_Opener(body=["x"]), "t1", "hub_gateway_result_invalid"),
    (_Opener(body={}), "", "hub_gateway_task_required")])
def test_gateway_failures_are_coded_tool_errors(opener, task_id, error):
    result = _client(opener).execute(task_id=task_id, tool_name="codecompass.search", arguments={}, tool_call_id="c")
    assert result["status"] == "error" and result["error"] == error


# --- configurable access path -------------------------------------------------------------------


def test_access_mode_per_tool_and_fallback():
    cfg = {"codecompass_access": "hub", "codecompass_access_overrides": {"codecompass.search": "delegated",
                                                                         "codecompass.x": "bogus"}}
    assert tc.access_mode(cfg) == "hub" and tc.access_mode(cfg, "codecompass.search") == "delegated"
    assert tc.access_mode(cfg, "codecompass.x") == "hub"
    assert tc.uses_delegation(cfg) and not tc.uses_delegation({"codecompass_access": "hub"})
    delegated = {"codecompass_access": "delegated"}
    assert tc.effective_access_mode(delegated, "codecompass.search", has_capability=False) == "delegated"
    fallback = {**delegated, "codecompass_access_fallback": "hub"}
    assert tc.effective_access_mode(fallback, "codecompass.search", has_capability=False) == "hub"
    assert tc.effective_access_mode(fallback, "codecompass.search", has_capability=True) == "delegated"


@pytest.fixture
def routed(monkeypatch):
    import agent.services.tools as tools

    calls = []
    monkeypatch.setattr(tools, "_dispatch_ananta_tool", lambda name, args, cfg, *rest: calls.append(
        ("local", name, dict(args))) or {"status": "ok"})
    monkeypatch.setattr(client_module, "hub_tool_gateway_client", lambda: SimpleNamespace(
        execute=lambda **kwargs: calls.append(("hub", kwargs["tool_name"], kwargs)) or {"status": "ok"}))
    monkeypatch.setattr("agent.config.settings.role", "worker")

    def run(name, cfg, args=None):
        tools.execute_ananta_tool(tool_name=name, arguments=args or {}, workspace_dir="/w", tool_call_id="c",
                                  config=cfg)
        return calls.pop()

    return run


def test_the_tool_executor_routes_by_the_configured_path(routed, monkeypatch):
    capability = {"allowed_index_ids": ["i"]}
    hub = routed("codecompass.architecture_overview", {"codecompass_access": "hub", "codecompass_task_id": "t1",
                                                        "codecompass_capability": capability})
    assert hub[0] == "hub" and hub[2]["task_id"] == "t1" and "capability" not in hub[2]["arguments"]
    local = routed("codecompass.architecture_overview", {"codecompass_access": "delegated",
                                                          "codecompass_capability": capability})
    assert local[0] == "local" and local[2]["capability"] == capability
    assert routed("repo.grep", {"codecompass_access": "hub"})[0] == "local"
    assert routed("codecompass.search", {"codecompass_access": "delegated", "codecompass_access_fallback": "hub",
                                         "codecompass_task_id": "t1"})[0] == "hub"
    monkeypatch.setattr("agent.config.settings.role", "hub")
    assert routed("codecompass.search", {"codecompass_access": "hub"})[0] == "local"  # the Hub is the Hub


def test_access_off_refuses_codecompass_tools():
    from agent.services.tools import execute_ananta_tool

    result = execute_ananta_tool(tool_name="codecompass.search", arguments={}, workspace_dir="/w", tool_call_id="c",
                                 config={"codecompass_access": "off"})
    assert result["status"] == "error" and result["error"] == "codecompass_access_off"
