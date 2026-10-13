"""Actual HTTP/SQL/Registry admission with explicit synthetic Task/MCP fixtures."""

import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from sqlalchemy import select, update
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.repositories.meet_companion_tool_sources import SqlCompanionToolAttempts, SqlCompanionToolSources, sources
from agent.routes.meet import meet_bp
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.meet_companion_task_tools import MeetCompanionTaskTools, digest
from agent.services.meet_companion_tool_execution import HubCompanionToolExecution, HubCompanionToolPolicy
from agent.services.meet_contract import MeetError
from agent.services.operation_policy_service import get_operation_policy_service
from agent.services.operation_registry_service import get_operation_registry_service
from ananta_contracts.meet_companion_task_tool import (
    REQUEST_SCHEMA,
    SIGNATURE_HEADER,
    request_signature,
    response_signature,
)
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from worker.meet_media.contract import encode

PATH = "/api/meet/v1/internal/companion/task-tool"
KEY = b"synthetic-task-tool-test-key-000000"
pytestmark = pytest.mark.timeout(15)


@pytest.fixture
def hub(assigned, monkeypatch):
    f, _grants, assignment, _key = assigned
    monkeypatch.delenv("ANANTA_MEET_COMPANION_MCP_TOOLS", raising=False)
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.register_blueprint(meet_bp)
    app.extensions["meet_media_worker_key"] = KEY
    database = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(database, tables=[HubRunEvidenceIdentityDB.__table__,
                                                 HubSourceEvidenceIdentityDB.__table__])
    store = SqlCompanionToolSources(database)
    store.initialize()
    attempts = SqlCompanionToolAttempts(database, max_calls=3)
    attempts.initialize()
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(database))
    capability = {"workspace_id": "workspace", "repository_id": "repo", "source_scope": "repository",
                  "revision": "1" * 40, "allowed_paths": ["worker/"], "allowed_index_ids": ["index"],
                  "allowed_signals": ["exact"]}
    resolver = Mock()
    resolver.resolve.side_effect = lambda **_kwargs: dict(capability)
    app.extensions["codecompass_retrieval_capability_resolver"] = resolver
    calls = []
    backend = Mock()

    def execute(**kwargs):
        with database.connect() as connection:
            run = connection.execute(select(HubRunEvidenceIdentityDB.__table__)).mappings().all()[-1]
            assert run["state"] == "reserved", "Hub reserves before MCP execution"
        assert kwargs["context"]["codecompass_capability"]["allowed_paths"] == ["worker/"]
        calls.append(kwargs)
        return {"content": [{"type": "json", "json": {"status": "ok", "answer": "synthetic exact scope"}}]}

    backend.call_tool.side_effect = execute
    policy = HubCompanionToolPolicy(app, get_operation_registry_service(), get_operation_policy_service())
    service = MeetCompanionTaskTools(f.authority, policy, HubCompanionToolExecution(app, backend), store,
                                    registry, attempts=attempts, synthetic=True)
    app.extensions["meet_companion_task_tools"] = service
    request = {"schema": REQUEST_SCHEMA, "nonce": "a" * 32, "sent_at": int(time.time()),
               "assignment": assignment, "tool": "codecompass.retrieve", "arguments": {"query": "Meet"}}
    yield SimpleNamespace(**locals())
    database.dispose()


def post(f, value=None, **headers):
    raw = encode(f.request if value is None else value)
    result = f.app.test_client().post(PATH, data=raw, headers={"Content-Type": "application/json",
                          SIGNATURE_HEADER: request_signature(KEY, raw), **headers})
    return raw, result


def test_signed_tool_response_has_actual_registered_sources_and_pre_reserved_run(hub):
    raw, response = post(hub)
    assert response.status_code == 200, response.get_json()
    assert response.headers[SIGNATURE_HEADER] == response_signature(KEY, raw, response.data)
    assert response.headers["Cache-Control"] == "no-store"
    value = response.get_json()
    source = hub.registry.require_source_identity(tenant_id="tenant", project_id="project",
        source_id=value["sources"][0]["source_id"], expected_binding_digest=value["sources"][0]["binding_digest"])
    assert source.synthetic and source.evidence_scope == "test"
    with hub.database.connect() as connection:
        run = connection.execute(select(HubRunEvidenceIdentityDB.__table__)).mappings().one()
    assert run["state"] == "succeeded" and run["result_digest"] == digest(value)
    assert value["run"]["run_id"] == run["run_id"] and value["run"]["dispatch_lease_id"] == "dispatch"
    assert hub.resolver.resolve.call_args.kwargs["principal"].project_id == "project"
    assert len(hub.calls) == 1
    assert post(hub)[1].status_code == 409 and len(hub.calls) == 1


@pytest.mark.parametrize("field,value", [("project_id", "foreign"), ("lease_id", "stale"),
                                        ("runtime_id", "replacement"), ("session_id", "foreign")])
def test_foreign_or_stale_assignment_never_executes(hub, field, value):
    packet = hub.request | {"assignment": hub.request["assignment"] | {field: value}}
    assert post(hub, packet)[1].status_code == 403
    assert not hub.calls


@pytest.mark.parametrize("withdrawal", ["cancel", "chat", "policy", "source"])
def test_withdrawal_during_tool_discards_output_and_fails_reserved_run(hub, withdrawal, monkeypatch):
    execute = hub.backend.call_tool.side_effect

    def withdrawn(**kwargs):
        result = execute(**kwargs)
        if withdrawal == "cancel":
            hub.f.task.status = "cancelled"
        elif withdrawal == "chat":
            hub.f.context["controls"]["chat"]["enabled"] = False
        elif withdrawal == "policy":
            monkeypatch.setenv("ANANTA_MEET_COMPANION_MCP_TOOLS", "none")
        else:
            hub.capability["allowed_paths"] = ["foreign/"]
        return result

    hub.backend.call_tool.side_effect = withdrawn
    assert post(hub)[1].status_code == 403
    with hub.database.connect() as connection:
        run = connection.execute(select(HubRunEvidenceIdentityDB.__table__)).mappings().one()
        admitted = connection.execute(select(HubSourceEvidenceIdentityDB.__table__)).mappings().all()
    assert run["state"] == "failed" and len(admitted) == 1


@pytest.mark.parametrize("patch", [{"source_id": "SRC_caller"}, {"run_id": "RUN_caller"},
                                 {"nonce": "x"}, {"sent_at": 1}, {"sent_at": True}])
def test_caller_evidence_and_invalid_envelopes_are_rejected(hub, patch):
    assert post(hub, hub.request | patch)[1].status_code == 403
    assert not hub.calls


@pytest.mark.parametrize("tool", ["codecompass.write"])
def test_write_handlers_cannot_be_reached(hub, tool):
    assert post(hub, hub.request | {"tool": tool, "arguments": {}})[1].status_code == 403
    assert not hub.calls


def test_missing_capability_and_paused_chat_fail_before_evidence_or_execution(hub):
    hub.app.extensions.pop("codecompass_retrieval_capability_resolver")
    assert post(hub)[1].status_code == 403
    hub.f.context["controls"]["chat"]["enabled"] = False
    assert post(hub)[1].status_code == 403
    assert not hub.calls
    with hub.database.connect() as connection:
        assert not connection.execute(select(HubRunEvidenceIdentityDB.__table__)).all()


def test_durable_budget_replay_and_immutable_admission_survive_store_recreation(hub):
    for nonce in "abc":
        assert post(hub, hub.request | {"nonce": nonce * 32})[1].status_code == 200
    hub.service.attempts = SqlCompanionToolAttempts(hub.database, max_calls=3)
    assert post(hub, hub.request | {"nonce": "d" * 32})[1].status_code == 429
    assert post(hub)[1].status_code == 409
    content = b"synthetic immutable source"
    admitted = hub.store.admit("tenant", "project", content)
    with hub.database.begin() as connection:
        connection.execute(update(sources).where(sources.c.digest == admitted).values(content=b"mutated"))
    with pytest.raises(MeetError, match="source_mutated"):
        hub.store.admit("tenant", "project", content)


def test_transport_direction_binding_and_default_disabled(hub):
    from ananta_contracts.meet_companion_task_grant import request_signature as legacy

    raw = encode(hub.request)
    assert post(hub, **{SIGNATURE_HEADER: legacy(KEY, raw)})[1].status_code == 401
    hub.app.config["ROLE"] = "worker"
    assert post(hub)[1].status_code == 403
    hub.app.config["ROLE"] = "hub"
    hub.app.extensions.pop("meet_companion_task_tools")
    assert post(hub)[1].status_code == 404


def tool_client(f, endpoint):
    from tests.meet.test_meet_companion_task_grant_client import assignment
    from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
    from worker.meet_media.companion_task_tool_client import AssignedCompanionToolClient

    value = assignment() | {"session_id": "hub-session", "deadline": f.f.context["deadline"]}
    return AssignedCompanionToolClient(AssignedCompanionGrantClient(value, endpoint, KEY))


def test_actual_worker_client_consumes_loopback_hub_registry_and_rejects_cancelled_task(hub):
    from tests.meet.test_meet_companion_task_control_http import running_hub

    with running_hub(hub.app) as endpoint:
        client = tool_client(hub, endpoint)
        response = client.call("codecompass.retrieve", {"query": "Meet"})
        assert response["run"]["evidence_scope"] == "test" and response["sources"]
        hub.f.task.status = "cancelled"
        with pytest.raises(ValueError, match="unavailable_or_invalid"):
            client.call("codecompass.retrieve", {"query": "Meet"})
        assert len(hub.calls) == 1


@pytest.mark.parametrize("failure", ["unsigned", "foreign_run", "caller_source", "replay_response"])
def test_worker_rejects_missing_auth_foreign_evidence_and_request_replay(hub, failure):
    from tests.meet.test_meet_companion_task_control_http import running_hub

    @hub.app.after_request
    def corrupt(response):
        if response.status_code == 200:
            value = response.get_json()
            if failure == "foreign_run":
                value["run"]["assignment_id"] = "foreign"
            elif failure == "caller_source":
                value["sources"][0]["source_id"] = "SRC_caller"
            response.set_data(encode(value))
            raw = request_body[0]
            response.headers[SIGNATURE_HEADER] = ("" if failure == "unsigned" else
                response_signature(KEY, raw + (b" " if failure == "replay_response" else b""), response.data))
        return response

    request_body = []

    @hub.app.before_request
    def observe_request():
        from flask import request

        request_body[:] = [request.get_data()]

    with running_hub(hub.app) as endpoint:
        with pytest.raises(ValueError, match="unavailable_or_invalid"):
            tool_client(hub, endpoint).call("codecompass.retrieve", {"query": "Meet"})
    assert len(hub.calls) == 1


def test_queued_read_revalidates_assignment_when_actual_execution_begins(hub, monkeypatch):
    def queued(_app, work, _timeout):
        hub.f.task.status = "cancelled"
        return work()

    monkeypatch.setattr("agent.services.meet_companion_tool_execution.run_bounded", queued)
    assert post(hub)[1].status_code == 403 and not hub.calls
    with hub.database.connect() as connection:
        assert connection.execute(select(HubRunEvidenceIdentityDB.__table__)).mappings().one()["state"] == "failed"


def test_sql_budget_atomically_admits_only_three_concurrent_calls(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    database = create_engine("sqlite:///" + str(tmp_path / "synthetic-budget.sqlite"))
    store = SqlCompanionToolAttempts(database, max_calls=3)
    store.initialize()

    def claim(index):
        try:
            store.claim("a" * 64, f"{index:032x}")
            return "admitted"
        except MeetError as error:
            return error.code

    try:
        with ThreadPoolExecutor(max_workers=8) as workers:
            outcomes = list(workers.map(claim, range(12)))
        assert outcomes.count("admitted") == 3
        assert outcomes.count("meet_companion_tool_budget_exhausted") == 9
    finally:
        database.dispose()


@pytest.mark.parametrize("policy", [
    {"enabled": True, "allow_groups": ["api.read.v1"]},
    {"enabled": True, "deny_operations": ["mcp.tool.codecompass.retrieve"]},
    {"enabled": True, "require_approval_for_risks": ["low", "medium", "high", "critical"]},
])
def test_operation_denial_or_mandatory_approval_is_a_bounded_refusal(hub, policy):
    hub.app.config["AGENT_CONFIG"] = {"operation_policy": policy}
    assert post(hub)[1].status_code == 403 and not hub.calls
    with hub.database.connect() as connection:
        assert not connection.execute(select(HubRunEvidenceIdentityDB.__table__)).all()
