"""Exact, read-only adoption of previously persisted Hub delegations."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.services.native_graph_task_queue_adapter import AnantaHubTaskQueueAdapter
from agent.services.native_submission_recovery import recover_submission
from agent.services.workflow_runtime.native_graph_ports import HubTaskSubmission
from tests.test_native_graph_task_adapters import FakeQueue, FakeRepository, FakeTaskRuntime, command


def setup_submission():
    repository = FakeRepository()
    queue = FakeQueue(repository)
    adapter = AnantaHubTaskQueueAdapter(
        task_queue=queue, task_repository=repository, task_runtime=FakeTaskRuntime(repository)
    )
    value = command()
    value = replace(value, command_id=f"ncmd:{value.run_id}:{value.node.node_id}:{value.attempt_id}")
    receipt = adapter.submit(value)
    return adapter, queue, repository, value, receipt


def recover(adapter, value, *, granted=True):
    return recover_submission(
        queue=adapter,
        grants=SimpleNamespace(revalidate=lambda envelope: granted),
        plan=SimpleNamespace(
            tenant_id=value.tenant_id,
            workflow_id=value.workflow_id,
            plan_hash=value.plan_hash,
            policy_version=value.policy_version,
        ),
        request=SimpleNamespace(
            run_id=value.run_id, control_task_id=value.control_task_id, correlation_id=value.correlation_id
        ),
        node=value.node,
        ownership=SimpleNamespace(status="active", attempt_id=value.attempt_id, fencing_token=value.fencing_token),
        input_data=value.input_data,
    )


def test_production_adapter_reads_existing_delegation_without_ingesting_again():
    adapter, queue, _, value, receipt = setup_submission()
    adopted = recover(adapter, value)
    assert adopted.command == value
    assert adopted.receipt == receipt
    assert len(queue.ingested_values) == 1
    assert recover(adapter, value) == adopted
    assert len(queue.ingested_values) == 1


@pytest.mark.parametrize("field", ["tenant_id", "run_id", "command_id"])
def test_production_reader_rejects_wrong_requested_identity(field):
    adapter, _, _, value, _ = setup_submission()
    query = {"tenant_id": value.tenant_id, "run_id": value.run_id, "command_id": value.command_id}
    query[field] = "different"
    if field == "command_id":
        assert adapter.get_submission(**query) is None
    else:
        with pytest.raises(ValueError, match="binding_mismatch"):
            adapter.get_submission(**query)


def test_recovery_rechecks_revoked_grant_without_requeueing():
    adapter, queue, _, value, _ = setup_submission()
    with pytest.raises(PermissionError, match="grant_denied"):
        recover(adapter, value, granted=False)
    assert len(queue.ingested_values) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"input_data": {"untrusted": True}},
        {"control_task_id": "different"},
        {"fencing_token": 2},
        {"plan_hash": "0" * 64},
        {"correlation_id": "foreign-trace"},
    ],
)
def test_recovery_rejects_changed_persisted_submission(change):
    _, _, _, value, receipt = setup_submission()
    stored = replace(value, **change)
    adapter = SimpleNamespace(get_submission=lambda **kwargs: HubTaskSubmission(stored, receipt))
    with pytest.raises(ValueError, match="binding_mismatch"):
        recover(adapter, value)


def test_reader_supports_detached_mapping_repository_rows():
    adapter, _, repository, value, receipt = setup_submission()
    repository.values[receipt.hub_task_id] = vars(repository.values[receipt.hub_task_id])
    assert recover(adapter, value).receipt == receipt
