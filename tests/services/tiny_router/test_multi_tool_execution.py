"""Headless Hub dispatch, graph, isolation, receipt and adversarial regressions."""

from __future__ import annotations

import contextvars
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.services.hub_multi_tool_execution import HubMultiToolExecutionService, ToolPlanScope
from ananta_contracts.multi_tool_plan import MultiToolPlan, ToolPlanStep
from ananta_contracts.tool_plan_graph import ResultCondition, ResultReference, ToolDependency, dependency_waves

pytestmark = pytest.mark.timeout(20)


def plan(*names):
    return MultiToolPlan(
        "plan", "ready", tuple(ToolPlanStep(n, "call", "ready", enabled=True, confidence=0.99) for n in names)
    )


class Registry:
    def get_tool(self, name):
        if name == "unknown":
            return None
        return SimpleNamespace(
            risk_class="write" if name == "write" else "read",
            category="controlled_write" if name == "write" else "read_only",
            argument_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"] if name == "c" else [],
            },
        )


class Policy:
    def evaluate(self, **kwargs):
        return SimpleNamespace(allowed=kwargs["tool_name"] != "denied", decision="policy_blocked", reason="role_denied")


class Receipts:
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()

    def claim(self, scope, request):
        with self.lock:
            if scope in self.rows:
                old, result = self.rows[scope]
                return False, result or {"status": "blocked", "reason": "in_progress"}
            self.rows[scope] = (request, None)
            return True, {}

    def finish(self, scope, request, response):
        self.rows[scope] = (request, response)


class Audit:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(dict(event))


class Gateway:
    def __init__(self, run=None):
        self.calls = []
        self.run = run or (lambda kw: {"status": "ok", "value": 7})

    def execute_direct(self, **kwargs):
        self.calls.append(kwargs)
        return {"kind": "direct_tool_result", "tool_result": self.run(kwargs)}


def service(gateway=None, **kwargs):
    return HubMultiToolExecutionService(
        registry=Registry(),
        policy=Policy(),
        gateway=gateway or Gateway(),
        receipts=kwargs.pop("receipts", Receipts()),
        audit=kwargs.pop("audit", Audit()),
        **kwargs,
    )


SCOPE = ToolPlanScope("tenant", "project", "task", "/explicit/workspace", ("a", "b", "c", "denied", "write"))


def test_independent_calls_are_concurrent_and_replay_is_exact():
    barrier = threading.Barrier(2, timeout=2)
    gateway = Gateway(lambda kw: (barrier.wait(), {"status": "ok"})[1])
    svc = service(gateway)
    first = svc.execute(plan("a", "b"), scope=SCOPE, agent_cfg={})
    assert first["status"] == "completed"
    assert svc.execute(plan("a", "b"), scope=SCOPE, agent_cfg={}) == first
    assert len(gateway.calls) == 2
    assert all(c["workspace_ref"] == SCOPE.workspace_ref and c["task_id"] == SCOPE.task_id for c in gateway.calls)


def test_result_binding_waits_for_parent_and_validates_arguments():
    gateway = Gateway()
    graph = {"c": ToolDependency(argument_sources={"value": ResultReference("a", "/value")})}
    result = service(gateway).execute(plan("c", "a"), scope=SCOPE, agent_cfg={}, dependencies=graph)
    assert result["status"] == "completed"
    assert [c["tool_name"] for c in gateway.calls] == ["a", "c"]
    assert gateway.calls[1]["arguments"] == {"value": 7}


@pytest.mark.parametrize("value,reason", [(False, "condition_false"), ("bad", "plan_arguments_failed_schema")])
def test_conditions_and_invalid_result_bound_arguments_do_not_dispatch(value, reason):
    gateway = Gateway(lambda kw: {"status": "ok", "value": value})
    dep = ToolDependency(
        argument_sources={"value": ResultReference("a", "/value")},
        when=(ResultCondition(ResultReference("a", "/value"), True),) if value is False else (),
    )
    result = service(gateway).execute(plan("a", "c"), scope=SCOPE, agent_cfg={}, dependencies={"c": dep})
    assert result["results"]["c"]["reason"] == reason
    assert len(gateway.calls) == 1


def test_failed_parent_blocks_descendant():
    gateway = Gateway(lambda kw: {"status": "error"})
    result = service(gateway).execute(
        plan("a", "b"), scope=SCOPE, agent_cfg={}, dependencies={"b": ToolDependency(depends_on=("a",))}
    )
    assert result["results"]["b"]["status"] == "skipped"
    assert len(gateway.calls) == 1


@pytest.mark.parametrize(
    "names,graph",
    [
        (("a", "a"), {}),
        (("a",), {"a": ToolDependency(depends_on=("b",))}),
        (("a", "b"), {"a": ToolDependency(depends_on=("b",)), "b": ToolDependency(depends_on=("a",))}),
    ],
)
def test_invalid_graphs_fail_before_dispatch(names, graph):
    with pytest.raises(ValueError):
        dependency_waves(names, graph)
    gateway = Gateway()
    assert service(gateway).execute(plan(*names), scope=SCOPE, agent_cfg={}, dependencies=graph)["status"] == "blocked"
    assert not gateway.calls


@pytest.mark.parametrize("names", [("unknown",), ("write",)])
def test_prompt_cannot_grant_capabilities_or_writes(names):
    gateway = Gateway()
    result = service(gateway).execute(plan(*names), scope=SCOPE, agent_cfg={})
    assert result["status"] == "blocked" and not gateway.calls


def test_actual_policy_denial_and_empty_tool_scope_cannot_be_bypassed():
    gateway = Gateway()
    assert service(gateway).execute(plan("denied"), scope=SCOPE, agent_cfg={})["status"] == "blocked"
    assert (
        service(gateway).execute(plan("a"), scope=replace(SCOPE, allowed_tools=()), agent_cfg={})["status"] == "blocked"
    )
    assert not gateway.calls


def test_tenants_have_separate_receipts_and_contexts():
    principal = contextvars.ContextVar("principal")
    gateway = Gateway(lambda kw: {"status": "ok", "principal": principal.get()})
    svc = service(gateway)
    for tenant in ("one", "two"):
        principal.set(tenant)
        result = svc.execute(plan("a"), scope=replace(SCOPE, tenant_id=tenant), agent_cfg={})
        assert result["results"]["a"]["principal"] == tenant
    assert len(gateway.calls) == 2


def test_deadline_is_bounded_and_late_work_is_not_retried():
    release = threading.Event()
    gateway = Gateway(lambda kw: (release.wait(2), {"status": "ok"})[1])
    svc = service(gateway, max_inflight=1)
    try:
        result = svc.execute(plan("a"), scope=SCOPE, agent_cfg={}, timeout_seconds=0.05)
        assert result["status"] == "blocked" and result["results"]["a"]["status"] == "timeout"
        assert svc.execute(plan("a"), scope=SCOPE, agent_cfg={}, timeout_seconds=0.05) == result
        assert len(gateway.calls) == 1
    finally:
        release.set()


def test_audit_failure_does_not_dispatch_or_retry():
    class FailingAudit:
        def record(self, event):
            raise OSError("test")

    gateway = Gateway()
    svc = service(gateway, audit=FailingAudit())
    assert svc.execute(plan("a"), scope=SCOPE, agent_cfg={})["status"] == "blocked"
    assert svc.execute(plan("a"), scope=SCOPE, agent_cfg={})["status"] == "blocked"
    assert not gateway.calls


def test_durable_receipts_isolate_scope_and_reject_different_requests(tmp_path):
    from sqlmodel import SQLModel, create_engine

    from agent.db_models.tool_plan_receipt import ToolPlanReceiptDB
    from agent.repositories.tool_plan_receipts import ToolPlanReceiptStore

    engine = create_engine("sqlite:///" + str(tmp_path / "receipts.db"))
    SQLModel.metadata.create_all(engine, tables=[ToolPlanReceiptDB.__table__])
    one = ToolPlanReceiptStore(lambda: engine)
    assert one.claim("tenant-task", "digest")[0]
    assert not ToolPlanReceiptStore(lambda: engine).claim("tenant-task", "digest")[0]
    one.finish("tenant-task", "digest", {"status": "completed"})
    assert one.claim("tenant-task", "digest") == (False, {"status": "completed"})
    assert one.claim("tenant-task", "different")[1]["reason"] == "task_plan_rate_limit"
    assert one.claim("other-tenant-task", "digest")[0]


def test_conditional_parameters_are_validated_after_result_binding():
    from agent.services.tiny_router.validation import validate_json_value

    schema = {
        "type": "object",
        "properties": {"detailed": {"type": "boolean"}, "query": {"type": "string"}},
        "if": {"properties": {"detailed": {"const": True}}},
        "then": {"required": ["query"]},
        "dependentRequired": {"query": ["detailed"]},
    }
    assert validate_json_value({"detailed": True}, schema)
    assert not validate_json_value({"detailed": True, "query": "bounded"}, schema)
    assert not validate_json_value({"detailed": False}, schema)
    assert validate_json_value({"query": "bounded"}, schema)


def test_receipt_migration_matches_model_and_can_rollback(tmp_path):
    import importlib.util

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = "migrations/versions/e0a2c4d6f8b1_add_tool_plan_receipts.py"
    spec = importlib.util.spec_from_file_location("receipt_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///" + str(tmp_path / "migration.db"))
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert set(inspector.get_table_names()) == {"tool_plan_receipts", "tool_plan_events"}
        migration.downgrade()
        assert not sa.inspect(connection).get_table_names()


def test_audit_events_are_durable_without_argument_content(tmp_path):
    import json

    from sqlmodel import Session, SQLModel, create_engine, select

    from agent.db_models.tool_plan_receipt import ToolPlanEventDB
    from agent.repositories.tool_plan_receipts import ToolPlanAuditStore

    engine = create_engine("sqlite:///" + str(tmp_path / "audit.db"))
    SQLModel.metadata.create_all(engine, tables=[ToolPlanEventDB.__table__])
    event = {
        "tenant_id": "tenant",
        "project_id": "project",
        "task_id": "task",
        "event": "tool_dispatch",
        "digest": "a" * 64,
    }
    ToolPlanAuditStore(lambda: engine).record(event)
    with Session(engine) as session:
        row = session.exec(select(ToolPlanEventDB)).one()
        assert json.loads(row.event_json) == event
        assert row.tenant_id == "tenant"


def test_two_calls_cross_real_hub_policy_and_runtime_adapter(tmp_path):
    from flask import Flask

    from agent.services.ananta_tool_policy_service import AnantaToolPolicyService
    from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service
    from agent.services.hub_tool_execution_adapter import HubToolExecutionAdapter
    from agent.services.worker_runtime_execution_adapter import WorkerRuntimeExecutionAdapter

    barrier = threading.Barrier(2, timeout=2)

    class Runtime:
        runtime_kind = "test"

        def execute_tool(self, **kw):
            barrier.wait()
            assert kw["workspace_dir"] == str(tmp_path)
            return {
                "schema": "ananta_tool_result.v1",
                "status": "ok",
                "tool_name": kw["tool_name"],
                "tool_call_id": kw["tool_call_id"],
                "evidence": [],
                "warnings": [],
                "risk_class": "read",
            }

    class Approvals:
        def resolve_grant_for_call(self, **kw):
            return None

    policy = AnantaToolPolicyService(approval_service=Approvals())
    gateway = HubToolExecutionAdapter(
        runtime_adapter=WorkerRuntimeExecutionAdapter(Runtime()), policy_service=policy, approval_service=Approvals()
    )
    svc = HubMultiToolExecutionService(
        registry=get_ananta_tool_registry_service(), policy=policy, gateway=gateway, receipts=Receipts(), audit=Audit()
    )
    scope = replace(SCOPE, workspace_ref=str(tmp_path), allowed_tools=("git.status", "git.diff_readonly"))
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    with app.app_context():
        outcome = svc.execute(
            plan("git.status", "git.diff_readonly"),
            scope=scope,
            agent_cfg={"hub_direct_execution": {"audit_enabled": False}},
        )
    assert outcome["status"] == "completed", outcome
    assert set(outcome["results"]) == {"git.status", "git.diff_readonly"}
