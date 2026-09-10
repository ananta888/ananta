"""Pi receives only Hub-projected context and discards revoked/changed runs."""

import json
from unittest.mock import Mock

import pytest

from agent.services.native_context_bundle_service import NativeContextBundleService
from agent.services.task_context_bundle_access_service import TaskContextBundleAccessService
from ananta_contracts.native_context_bundle import NativeApprovedContext
from tests.test_pi_coding_agent_provider import Runner
from tests.test_pi_native_node import native_setup
from worker.runtime.native_graph.pi_context import HubPiTaskContextReader, pi_context_bundle_reference


def context_setup(tmp_path):
    prompt = json.dumps({
        "task": "Explain this code.",
        "context_bundle": {"id": "bundle-1", "content": "approved code context"},
    }, ensure_ascii=True)
    runner = Runner(prompt=prompt)
    adapter, task, runner, client, credentials = native_setup(
        tmp_path, runner=runner, context_reader_factory=HubPiTaskContextReader,
        mutate_task=lambda task: task | {
            "tenant_id": "tenant-1", "project_id": "project-1",
            "task_kind": "pi_coding_agent", "derivation_reason": "native_graph_hub_delegation",
            "status": "running", "context_bundle_id": "bundle-1",
        },
    )
    bundle = {"id": "bundle-1", "task_id": "hub-task-1", "context_text": "unapproved original"}
    tasks, bundles, policy = Mock(), Mock(), Mock()
    tasks.get_by_id.return_value, bundles.get_by_id.return_value = task, bundle
    policy.project.return_value = NativeApprovedContext("approved code context", "b" * 64)
    client.service._context_bundles = NativeContextBundleService(
        tasks=tasks, bundles=TaskContextBundleAccessService(bundles), policy=policy,
    )
    return adapter, task, runner, client, credentials, policy


def test_real_native_pi_composition_uses_only_approved_context_with_revalidation(tmp_path):
    adapter, task, runner, client, _, policy = context_setup(tmp_path)
    result = adapter.execute_task(task)
    assert result.status == "completed" and len(runner.calls) == 1
    assert policy.project.call_count == 5
    assert "unapproved original" not in runner.calls[0][1]["input_text"]
    assert [name for name, _ in client.calls].count("provider_budget_reserve") == 1
    assert result.artifact_refs == {} and not list(tmp_path.glob("ananta-pi-*"))


@pytest.mark.parametrize("change_at", [2, 3, 4, 5])
@pytest.mark.parametrize("change", ["policy", "content", "revocation"])
def test_context_changes_or_revocation_block_before_execution_or_discard_output(tmp_path, change_at, change):
    adapter, task, runner, _, _, policy = context_setup(tmp_path)
    count = 0

    def project(**kwargs):
        nonlocal count
        count += 1
        if count < change_at:
            return NativeApprovedContext("approved code context", "b" * 64)
        if change == "revocation":
            raise ValueError("native_context_policy_revoked")
        return NativeApprovedContext(
            "changed content" if change == "content" else "approved code context",
            "c" * 64 if change == "policy" else "b" * 64,
        )

    policy.project.side_effect = project
    result = adapter.execute_task(task)
    assert result.status == "failed" and result.reason_code == "pi_execution_not_authorized"
    assert result.output_data["output"] == "" and result.artifact_refs == {}
    assert len(runner.calls) == int(change_at == 5)


@pytest.mark.parametrize("field,value", [
    ("hub_task_id", "foreign-task"), ("bundle_id", "foreign-bundle"), ("command_digest", "a" * 64),
])
def test_worker_rejects_well_formed_but_foreign_projection_before_credentials(tmp_path, field, value):
    adapter, task, runner, client, credentials, _ = context_setup(tmp_path)
    original = client.command

    def command(name, **values):
        result = original(name, **values)
        if name == "native_context_read":
            result[field] = value
        return result

    client.command = command
    result = adapter.execute_task(task)
    assert result.status == "failed" and result.reason_code == "pi_context_projection_binding_mismatch"
    assert not runner.calls
    credentials.assert_not_called()


@pytest.mark.parametrize("top,nested,context", [
    ("bundle-1", "bundle-2", None), (True, None, None), (None, {}, None),
    ("", None, None), (None, None, "unbound text"),
])
def test_unbound_or_ambiguous_context_references_are_not_accepted(top, nested, context):
    task = {"context_bundle_id": top, "worker_execution_context": {"context_bundle_id": nested, "context": context}}
    with pytest.raises(ValueError):
        pi_context_bundle_reference(task)
