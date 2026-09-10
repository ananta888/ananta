"""Hub queue -> owned snapshot -> active SQL policy -> Native Pi execution."""

import hashlib
import json
from dataclasses import replace

import pytest

from agent.db_models import TaskDB
from tests.test_native_context_policy import build_policy_setup
from tests.test_native_context_policy_composition import configured_app
from tests.test_native_context_preparation import preparation_setup
from tests.test_pi_native_node import native_setup
from worker.runtime.native_graph.contracts import NativeNodeCommand
from worker.runtime.native_graph.pi_context import HubPiTaskContextReader


@pytest.mark.parametrize("mutation", [None, "content", "parent", "revoked"])
def test_automatically_prepared_task_reaches_pi_only_through_active_hub_policy(tmp_path, monkeypatch, mutation):
    policy_setup = build_policy_setup(monkeypatch)
    destination_id = policy_setup[5].get.return_value.destination_id
    hub_task_id = "wfn-" + hashlib.sha256(b"pi-test-command").hexdigest()[:24]

    def with_context(command):
        return replace(command, node=replace(command.node, metadata={
            "context_bundle_mode": "control_task", "context_policy_id": "policy-1",
            "context_destination_id": destination_id,
        }))

    worker, initial_task, runner, client, credentials = native_setup(
        tmp_path, mutate=with_context, hub_task_id=hub_task_id, context_reader_factory=HubPiTaskContextReader,
    )
    command = NativeNodeCommand.from_mapping(initial_task["worker_execution_context"]["native_node_command"])
    preparation = preparation_setup()
    receipt = preparation.adapter.submit(command)
    assert receipt.accepted and receipt.hub_task_id == hub_task_id
    ingested = preparation.queue.ingested_values[0]
    stored = TaskDB(
        id=receipt.hub_task_id, status=ingested["status"], team_id=ingested["team_id"], **ingested["extra_fields"],
    )
    bundle = preparation.bundles.get_by_id(stored.context_bundle_id)
    task_payload, bundle_payload = stored.model_dump(), bundle.model_dump()
    if mutation == "content":
        bundle_payload["chunks"][0]["content"] = "mutated before execution"
    elif mutation == "parent":
        task_payload["parent_task_id"] = "foreign"
    app, reader, lifecycle, actor, active = configured_app(
        tmp_path, policy_setup, task=task_payload, bundle=bundle_payload,
    )
    client.service._context_bundles = reader
    content = json.dumps([
        {"source_ref": "docs/public/example.py", "content": "def example(): return 1"},
    ], ensure_ascii=True, separators=(",", ":"))
    runner.prompt = json.dumps({
        "task": "Explain this code.", "context_bundle": {"id": bundle.id, "content": content},
    }, ensure_ascii=True)
    with app.app_context():
        if mutation == "revoked":
            lifecycle.revoke(
                actor=actor, policy_id="policy-1", version=active.version,
                if_match=active.etag, idempotency_key="synthetic-revoked-before-execution",
            )
        result = worker.execute_task(stored.model_dump())
    if mutation is not None:
        assert result.status == "failed" and not runner.calls
        assert result.reason_code == (
            "native_context_active_policy_required" if mutation == "revoked"
            else "native_context_snapshot_binding_mismatch"
        )
        credentials.assert_not_called()
        assert "provider_budget_reserve" not in [name for name, _ in client.calls]
        assert result.artifact_refs == {} and result.output_data == {}
        return
    assert result.status == "completed", result.reason_code
    assert len(runner.calls) == credentials.call_count == 1
    assert [name for name, _ in client.calls].count("native_context_read") == 5
    assert [name for name, _ in client.calls].count("provider_budget_reserve") == 1
    assert "unapproved assembled" not in runner.calls[0][1]["input_text"]
    assert preparation.original.task_id == command.control_task_id
    assert result.artifact_refs == {} and not list(tmp_path.glob("ananta-pi-*"))
