"""Synthetic Native adversarial checks and real Hub Evidence Registry gates."""

from dataclasses import replace

import pytest
from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import EvidenceIdentityPersistenceError, SqlEvidenceIdentityRepository
from agent.services.hub_evidence_gate_service import (
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
    canonical_evidence_digest,
)
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.workflow_runtime.errors import WorkflowRuntimeError
from tests.bpmn.completion_helpers import harness as harness
from tests.bpmn.completion_helpers import linear_request
from tests.bpmn.test_execution_recovery import advance_bounded
from tests.test_native_graph_runtime import signed_control


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "foreign-tenant"),
        ("workflow_id", "foreign-workflow"),
        ("run_id", "foreign-run"),
        ("command_id", "foreign-command"),
        ("hub_task_id", "foreign-task"),
        ("attempt_id", "foreign-attempt"),
        ("fencing_token", 99),
    ],
)
def test_foreign_result_cannot_complete_or_authorize_successor(harness, field, value):
    request = linear_request()
    running = harness.dispatch_first(request)
    original = harness.queue.results["hub-task-1"]
    harness.queue.results["hub-task-1"] = replace(original, **{field: value})
    with pytest.raises((ValueError, WorkflowRuntimeError)):
        harness.hub.advance(request)
    assert harness.hub.checkpoint(request) == running.checkpoint
    assert harness.handler.calls == ["work"]
    harness.queue.results["hub-task-1"] = original
    assert harness.finish(request).status == "completed"


def test_duplicate_and_late_result_cannot_double_count_or_advance_cancelled_run(harness, monkeypatch):
    request = linear_request()
    running = harness.dispatch_first(request)
    first = harness.queue.results["hub-task-1"]
    monkeypatch.setattr(harness.queue, "poll", lambda **kwargs: (first, first))
    progressed = harness.hub.advance(request)
    assert progressed.checkpoint.state.runtime_metadata["budget_usage"] == {"tokens": 1, "cost_micros": 1}
    assert harness.handler.calls == ["work", "after"]
    command = signed_control(
        keys=harness.keys,
        checkpoint=progressed.checkpoint,
        command_type="cancel",
        step_id="after",
        command_id="synthetic-cancel-late-result",
    )
    cancelled = harness.hub.resume(request, command=command)
    harness.restart()
    unchanged = harness.hub.advance(request)
    assert unchanged.status == "cancelled"
    assert unchanged.checkpoint == cancelled.checkpoint
    assert "after" not in unchanged.completed_node_ids
    assert unchanged.checkpoint.revision > running.checkpoint.revision


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, -1])
def test_invalid_worker_usage_is_rejected_before_ownership_acknowledgement(harness, value):
    request = linear_request()
    running = harness.dispatch_first(request)
    original = harness.queue.results["hub-task-1"]
    harness.queue.results["hub-task-1"] = replace(original, budget_usage={"tokens": value})
    with pytest.raises((ValueError, WorkflowRuntimeError)):
        harness.hub.advance(request)
    owner = harness.stores["ownership"].get(tenant_id="tenant-a", run_id=request.run_id, step_id="work")
    assert owner.status == "active", "Invalid metering mutated durable ownership before rejection"
    assert harness.hub.checkpoint(request) == running.checkpoint
    assert harness.handler.calls == ["work"]


def test_exhausted_budget_is_a_persisted_failure_not_an_unrecoverable_ack(harness):
    request = linear_request()
    request = replace(request, plan=replace(request.plan, budget=replace(request.plan.budget, max_tokens=1)))
    harness.dispatch_first(request)
    original = harness.queue.results["hub-task-1"]
    harness.queue.results["hub-task-1"] = replace(original, budget_usage={"tokens": 2})
    result = harness.hub.advance(request)
    assert result.status == "failed"
    assert "budget" in result.reason_code
    harness.restart()
    assert harness.hub.advance(request).status == "failed"
    assert harness.handler.calls == ["work"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "foreign-tenant"),
        ("plan_hash", "0" * 64),
        ("policy_version", "foreign-policy"),
        ("expected_revision", 999),
        ("actor_roles", ("administrator",)),
        ("command_type", "approve"),
    ],
)
def test_tampered_signed_command_has_no_effect(harness, field, value):
    request = linear_request()
    running = harness.dispatch_first(request)
    original = signed_control(
        keys=harness.keys,
        checkpoint=running.checkpoint,
        command_type="cancel",
        step_id="work",
        command_id="synthetic-signed-control",
    )
    before = harness.hub.stream(request)
    with pytest.raises((ValueError, PermissionError, WorkflowRuntimeError)):
        harness.hub.resume(request, command=replace(original, **{field: value}))
    assert harness.hub.checkpoint(request) == running.checkpoint
    assert harness.hub.stream(request) == before
    assert harness.queue.cancelled == []


@pytest.mark.parametrize("supply_stale_checkpoint", [False, True])
def test_stale_approval_cannot_resurrect_a_cancelled_gate(harness, supply_stale_checkpoint):
    request = linear_request(user_task=True)
    waiting = advance_bounded(harness.hub, request, harness.hub.start(request))
    approval = signed_control(
        keys=harness.keys,
        checkpoint=waiting.checkpoint,
        command_type="approve",
        step_id="work",
        command_id="synthetic-old-approval",
    )
    cancel = signed_control(
        keys=harness.keys,
        checkpoint=waiting.checkpoint,
        command_type="cancel",
        step_id="work",
        command_id="synthetic-new-cancel",
    )
    cancelled = harness.hub.resume(request, command=cancel)
    before = harness.hub.stream(request)
    with pytest.raises((ValueError, PermissionError, WorkflowRuntimeError)):
        harness.hub.resume(
            request,
            command=approval,
            **({"checkpoint": waiting.checkpoint} if supply_stale_checkpoint else {}),
        )
    assert harness.hub.checkpoint(request) == cancelled.checkpoint
    assert harness.hub.stream(request) == before
    assert harness.queue.submissions == []


def test_business_output_cannot_grant_tools_policy_or_approval(harness):
    request = linear_request()
    harness.dispatch_first(request)
    original = harness.queue.results["hub-task-1"]
    harness.queue.results["hub-task-1"] = replace(
        original,
        output_data={
            "allowed_tools": ["shell.execute"],
            "policy_version": "attacker-policy",
            "approved_gates": ["all"],
            "tenant_id": "foreign",
            "fencing_token": 1000,
        },
    )
    harness.hub.advance(request)
    successor = harness.queue.submissions[-1]
    assert successor.node.node_id == "after"
    assert successor.node.allowed_tools == ()
    assert successor.authorization.allowed_tools == ()
    assert successor.policy_version == request.plan.policy_version
    assert successor.tenant_id == request.plan.tenant_id
    assert harness.hub.checkpoint(request).state.runtime_metadata["approved_gates"] == []


@pytest.mark.parametrize("delivery", ["native", "task-adapter"])
@pytest.mark.parametrize("resource", ["tools", "input-artifacts", "output-artifacts", "undeclared-input"])
def test_signed_hub_delegation_cannot_broaden_tools_at_worker(harness, monkeypatch, delivery, resource):
    from types import SimpleNamespace

    from worker.runtime.native_graph.task_adapter import NativeGraphWorkerTaskAdapter

    request = linear_request()
    submit = harness.queue.submit
    if delivery == "task-adapter":
        adapter = NativeGraphWorkerTaskAdapter(harness.queue.runtime)

        def deliver(command, *, hub_task_id):
            return adapter.execute_task(
                {
                    "id": hub_task_id,
                    "worker_execution_context": {
                        "schema": "ananta.native_graph_worker_context.v1",
                        "runtime_path": "native_graph_node",
                        "native_node_command": command.to_dict(),
                    },
                }
            )

        harness.queue.runtime = SimpleNamespace(execute=deliver)

    def tampered_submit(command):
        if resource == "tools":
            command = replace(command, node=replace(command.node, allowed_tools=("shell.execute",)))
        elif resource == "input-artifacts":
            command = replace(command, node=replace(command.node, input_artifacts=("foreign-input",)))
        elif resource == "output-artifacts":
            command = replace(command, node=replace(command.node, output_artifacts=("foreign-output",)))
        else:
            command = replace(command, artifact_refs={"foreign-input": "artifact://synthetic/foreign"})
        return submit(command)

    monkeypatch.setattr(harness.queue, "submit", tampered_submit)
    result = advance_bounded(harness.hub, request, harness.hub.start(request))
    assert result.status == "failed"
    assert harness.handler.calls == []
    assert result.reason_code in {
        "native_authorization_tool_scope_mismatch",
        "native_authorization_artifact_scope_mismatch",
        "native_input_artifact_undeclared",
    }


@pytest.mark.parametrize("required_scope", ["local", "external", "production"])
def test_registry_reserved_synthetic_bpmn_execution_never_satisfies_release(harness, tmp_path, required_scope):
    """Actual SQL registry APIs reserve before this simulated Native execution."""
    database = create_engine(f"sqlite:///{tmp_path / 'registry.sqlite'}")
    SQLModel.metadata.create_all(
        database,
        tables=[
            HubSourceEvidenceIdentityDB.__table__,
            HubRunEvidenceIdentityDB.__table__,
        ],
    )
    repository = SqlEvidenceIdentityRepository(database)
    registry = HubEvidenceRegistryService(repository, clock=lambda: 100.0)
    request = linear_request()
    digest = canonical_evidence_digest
    gate_request = EvidenceGateRequest(
        tenant_id=request.plan.tenant_id,
        project_id="synthetic-bpmn-review",
        task_id=request.control_task_id,
        assignment_id="synthetic-review-assignment",
        dispatch_lease_id="synthetic-review-lease",
        repository_revision=digest(request.plan.to_dict()),
        input_digest=digest(request.input_data),
        execution_profile_digest=digest({"mode": "synthetic"}),
        environment_digest=digest({"network": "none", "worker": "deterministic-test-adapter"}),
        evidence_scope="test",
        required_scope=required_scope,
        synthetic=True,
        idempotency_key="synthetic-bpmn-completion-review",
        sources=(
            EvidenceGateSourceAdmission(
                origin_type="synthetic-bpmn",
                origin_digest=digest({"fixture": "linear_request"}),
                content_digest=digest(request.plan.to_dict()),
                policy_digest=digest(request.plan.policy_version),
                synthetic=True,
            ),
        ),
    )

    def execute(assignment):
        reserved = repository.get_run(
            tenant_id=gate_request.tenant_id,
            project_id=gate_request.project_id,
            run_id=assignment["run_id"],
        )
        assert reserved is not None and reserved.state == "reserved"
        assert reserved.synthetic and reserved.evidence_scope == "test"
        assert reserved.assignment_id == assignment["assignment_id"]
        assert reserved.dispatch_lease_id == assignment["dispatch_lease_id"]
        assert harness.queue.submissions == []
        for changed in ({"assignment_id": "foreign-assignment"}, {"dispatch_lease_id": "foreign-lease"}):
            with pytest.raises(EvidenceIdentityPersistenceError, match="binding"):
                registry.record_result(
                    **{
                        "tenant_id": reserved.tenant_id,
                        "project_id": reserved.project_id,
                        "run_id": reserved.run_id,
                        "assignment_id": reserved.assignment_id,
                        "dispatch_lease_id": reserved.dispatch_lease_id,
                        "terminal_state": "succeeded",
                        "result_digest": digest({"passed": True}),
                        **changed,
                    }
                )
        assert (
            repository.get_run(
                tenant_id=reserved.tenant_id,
                project_id=reserved.project_id,
                run_id=reserved.run_id,
            ).state
            == "reserved"
        )
        for source_id in reserved.source_ids:
            source = repository.get_source(
                tenant_id=gate_request.tenant_id,
                project_id=gate_request.project_id,
                source_id=source_id,
            )
            assert source.synthetic and source.evidence_scope == "test"
        result = advance_bounded(harness.hub, request, harness.hub.start(request))
        return {"passed": result.status == "completed", "classification": "synthetic", "status": result.status}

    try:
        outcome = HubEvidenceGateService(registry).execute(gate_request, execute)
        assert outcome.passed is True
        assert outcome.verified is False
        assert outcome.reason_code == "evidence_run_test_scope_forbidden"
        assert harness.handler.calls == ["work", "after"]
        recorded = repository.get_run(
            tenant_id=gate_request.tenant_id,
            project_id=gate_request.project_id,
            run_id=outcome.run_id,
        )
        assert recorded.state == "succeeded"
        assert recorded.result_digest == digest(outcome.execution)
        foreign = registry.verify_release_binding(
            tenant_id="foreign-tenant",
            project_id=gate_request.project_id,
            run_id=outcome.run_id,
            required_scope=required_scope,
            task_id=gate_request.task_id,
            repository_revision=gate_request.repository_revision,
            source_ids=outcome.source_ids,
        )
        assert not foreign.verified and foreign.reason_code == "evidence_run_identity_not_found"
    finally:
        database.dispose()
