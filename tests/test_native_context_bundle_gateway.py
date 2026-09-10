"""Automatic exact-task context delivery through actual Hub authorization."""

import copy
from unittest.mock import Mock

import pytest

from agent.db_models import TaskDB
from agent.services.native_context_bundle_service import NativeContextBundleService
from agent.services.task_context_bundle_access_service import TaskContextBundleAccessService
from ananta_contracts.native_context_bundle import NativeApprovedContext, NativeContextBundleProjection
from tests.test_pi_hub_budget_composition import composition
from tests.test_pi_native_node import task_command
from worker.runtime.workflow_hub_gateway import WorkflowHubDecisionError


def setup_context():
    client, context, events = composition()
    command = task_command(context)
    task = {
        "id": "hub-task-1", "tenant_id": context.tenant_id, "project_id": "project-1",
        "task_kind": "pi_coding_agent", "derivation_reason": "native_graph_hub_delegation",
        "status": "running", "context_bundle_id": "bundle-1",
        "worker_execution_context": {
            "schema": "ananta.native_graph_worker_context.v1", "runtime_path": "native_graph_node",
            "native_node_command": command.to_dict(),
        },
    }
    bundle = {"id": "bundle-1", "task_id": task["id"], "context_text": "private original"}
    tasks, bundles, policy = Mock(), Mock(), Mock()
    tasks.get_by_id.side_effect = lambda task_id: task if task_id == task["id"] else None
    bundles.get_by_id.side_effect = lambda bundle_id: bundle if bundle_id == bundle["id"] else None
    policy.project.return_value = NativeApprovedContext("approved context only", "b" * 64)
    client.service._context_bundles = NativeContextBundleService(
        tasks=tasks, bundles=TaskContextBundleAccessService(bundles), policy=policy,
    )
    request = {
        "binding": {
            "tenant_id": context.tenant_id, "workflow_id": context.workflow_id, "run_id": context.run_id,
            "step_id": context.step_id, "plan_hash": context.plan_hash, "policy_version": context.policy_version,
            "authorization_envelope": context.authorization_envelope,
        },
        "hub_task_id": task["id"], "command_id": command.command_id,
        "attempt_id": context.attempt_id, "fencing_token": context.fencing_token,
    }
    return client, request, task, bundle, policy, events


def test_hub_returns_only_task_bound_policy_projection_and_content_free_audit():
    client, request, task, _, policy, events = setup_context()
    result = NativeContextBundleProjection.from_mapping(client.command("native_context_read", **request))
    assert result.content == "approved context only" and "private original" not in str(result.to_dict())
    assert result.hub_task_id == task["id"] and result.bundle_id == "bundle-1"
    assert policy.project.call_args.kwargs["worker_id"] == "worker-1"
    audit = events.list_events(tenant_id=request["binding"]["tenant_id"], run_id=request["binding"]["run_id"])
    assert [e.event_type for e in audit] == ["workflow.context.bundle_read"]
    assert "approved context only" not in str(audit) and "private original" not in str(audit)


def test_actual_task_model_uses_persisted_native_markers_not_an_invented_source_column():
    client, request, task, _, policy, _ = setup_context()
    task.update(task_kind="pi_coding_agent", derivation_reason="native_graph_hub_delegation")
    task["worker_execution_context"].update(
        schema="ananta.native_graph_worker_context.v1", runtime_path="native_graph_node",
    )
    stored = TaskDB(**task)
    assert "source" not in stored.model_dump()
    client.service._context_bundles._tasks.get_by_id.side_effect = lambda value: stored if value == stored.id else None
    response = client.command("native_context_read", **request)
    assert response["content"] == "approved context only"
    policy.project.assert_called_once()


@pytest.mark.parametrize("mutation", ["worker", "anonymous", "attempt", "fence", "signature", "task_id"])
def test_gateway_denies_foreign_stale_or_unregistered_context_requests_before_policy(mutation):
    client, request, _, _, policy, _ = setup_context()
    if mutation == "worker":
        client.worker_id, client.worker_url = "foreign-worker", "http://foreign-worker:5000"
    elif mutation == "anonymous":
        client.worker_id = client.worker_url = ""
    elif mutation == "attempt":
        request["attempt_id"] = "foreign-attempt"
    elif mutation == "fence":
        request["fencing_token"] += 1
    elif mutation == "signature":
        request["binding"]["authorization_envelope"]["signature"] = "forged"
    else:
        request["hub_task_id"] = "other-task"
    with pytest.raises(WorkflowHubDecisionError):
        client.command("native_context_read", **request)
    policy.project.assert_not_called()


@pytest.mark.parametrize("mutation", [
    "tenant", "project", "derivation", "task_kind", "schema", "runtime_path", "terminal",
    "command_id", "node", "envelope",
    "bundle_owner", "bundle_unbound", "bundle_missing", "bundle_mismatch",
])
def test_hub_rejects_persisted_task_or_bundle_mismatch(mutation):
    client, request, task, bundle, policy, _ = setup_context()
    command = task["worker_execution_context"]["native_node_command"]
    if mutation == "tenant":
        task["tenant_id"] = "foreign-tenant"
    elif mutation == "project":
        task["project_id"] = None
    elif mutation == "derivation":
        task["derivation_reason"] = "api"
    elif mutation == "task_kind":
        task["task_kind"] = "shell"
    elif mutation in {"schema", "runtime_path"}:
        task["worker_execution_context"][mutation] = "foreign"
    elif mutation == "terminal":
        task["status"] = "cancelled"
    elif mutation == "command_id":
        request["command_id"] = "other-command"
    elif mutation == "node":
        command["node"]["node_id"] = "foreign-step"
    elif mutation == "envelope":
        command["authorization"]["signature"] = "forged"
    elif mutation == "bundle_owner":
        bundle["task_id"] = "foreign-task"
    elif mutation == "bundle_unbound":
        bundle["task_id"] = None
    elif mutation == "bundle_missing":
        task["context_bundle_id"] = "absent-bundle"
    else:
        task["worker_execution_context"]["context_bundle_id"] = "other-bundle"
    with pytest.raises(WorkflowHubDecisionError):
        client.command("native_context_read", **request)
    policy.project.assert_not_called()


def test_unavailable_context_service_fails_closed():
    client, request, _, _, _, _ = setup_context()
    client.service._context_bundles = None
    with pytest.raises(WorkflowHubDecisionError, match="native_context_service_unavailable"):
        client.command("native_context_read", **request)


@pytest.mark.parametrize("approved", [None, {}, NativeApprovedContext("x" * 24_001, "b" * 64)])
def test_invalid_or_oversized_policy_result_never_leaves_hub(approved):
    client, request, _, _, policy, _ = setup_context()
    policy.project.return_value = approved
    with pytest.raises(WorkflowHubDecisionError):
        client.command("native_context_read", **request)


def test_projection_changes_when_policy_changes_and_never_caches_a_grant():
    client, request, _, _, policy, _ = setup_context()
    first = client.command("native_context_read", **request)
    policy.project.return_value = NativeApprovedContext("approved context only", "c" * 64)
    second = client.command("native_context_read", **request)
    assert first["policy_digest"] != second["policy_digest"] and policy.project.call_count == 2
    policy.project.side_effect = ValueError("native_context_policy_revoked")
    with pytest.raises(WorkflowHubDecisionError, match="native_context_policy_revoked"):
        client.command("native_context_read", **request)


@pytest.mark.parametrize("field,value", [
    ("schema", "future"), ("content", "mutated"), ("command_digest", "invalid"),
    ("policy_digest", "invalid"), ("hub_task_id", ""), ("bundle_id", "a\nb"), ("extra", "unknown"),
])
def test_worker_contract_refuses_malformed_or_mutated_projection(field, value):
    client, request, _, _, _, _ = setup_context()
    raw = copy.deepcopy(client.command("native_context_read", **request))
    raw[field] = value
    with pytest.raises(ValueError):
        NativeContextBundleProjection.from_mapping(raw)
