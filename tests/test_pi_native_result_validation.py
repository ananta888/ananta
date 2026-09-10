"""Pi results remain candidates until every persisted command binding agrees."""

import copy
from types import SimpleNamespace

import pytest

from agent.services.native_graph_task_queue_adapter import AnantaHubTaskQueueAdapter
from tests.test_native_graph_task_adapters import FakeQueue, FakeRepository, FakeTaskRuntime, result
from tests.test_pi_hub_budget_composition import composition
from tests.test_pi_native_node import task_command


def pending_pi_result():
    _, context, _ = composition()
    command = task_command(context)
    repository = FakeRepository()
    adapter = AnantaHubTaskQueueAdapter(
        task_queue=FakeQueue(repository), task_repository=repository, task_runtime=FakeTaskRuntime(repository),
    )
    receipt = adapter.submit(command)
    task = repository.values[receipt.hub_task_id]
    task.status = "completed"
    candidate = result(command, receipt.hub_task_id).to_dict()
    candidate["output_data"] = {
        "provider_id": "pi", "model_id": command.provider_binding.model_id,
        "output": "synthetic complete answer", "exit_code": 0,
    }
    task.verification_status = {"native_node_result": candidate}
    return adapter, command, task, candidate


def poll(adapter, command, task):
    return adapter.poll(tenant_id=command.tenant_id, run_id=command.run_id, hub_task_ids=(task.id,))


def test_pi_poll_requires_hub_admission_even_for_an_exact_bounded_terminal_result():
    adapter, command, task, candidate = pending_pi_result()
    before = copy.deepcopy(candidate)
    with pytest.raises(ValueError, match="^pi_native_result_receipt_required$"):
        poll(adapter, command, task)
    assert candidate == before


@pytest.mark.parametrize("field", [
    "command_id", "tenant_id", "workflow_id", "run_id", "node_id", "attempt_id",
])
def test_pi_poll_rejects_another_command_scope(field):
    adapter, command, task, candidate = pending_pi_result()
    candidate[field] = "other"
    with pytest.raises(ValueError, match="^pi_native_result_binding_mismatch$"):
        poll(adapter, command, task)


@pytest.mark.parametrize("fence", [True, "1", 1.0, 2])
def test_pi_poll_rejects_coercive_or_stale_fencing(fence):
    adapter, command, task, candidate = pending_pi_result()
    candidate["fencing_token"] = fence
    with pytest.raises(ValueError, match="^pi_native_result_binding_mismatch$"):
        poll(adapter, command, task)


@pytest.mark.parametrize("mutation", [
    "model", "provider", "output_type", "output_limit", "exit_type", "exit_nonzero", "missing_output",
    "extra_output", "artifacts", "budget", "side_effect", "reason", "extra_wire", "missing_schema", "null_budget",
    "invalid_unicode",
])
def test_pi_poll_rejects_success_outside_the_no_tools_result_contract(mutation):
    adapter, command, task, candidate = pending_pi_result()
    output = candidate["output_data"]
    if mutation in {"model", "provider"}:
        output[mutation + "_id"] = "foreign"
    elif mutation == "output_type":
        output["output"] = ["answer"]
    elif mutation == "output_limit":
        output["output"] = "x" * 16385
    elif mutation == "exit_type":
        output["exit_code"] = False
    elif mutation == "exit_nonzero":
        output["exit_code"] = 1
    elif mutation == "missing_output":
        candidate["output_data"] = {}
    elif mutation == "extra_output":
        output["evidence"] = "not-registry-authority"
    elif mutation == "artifacts":
        candidate["artifact_refs"] = {"unrequested": "file:///private"}
    elif mutation == "budget":
        candidate["budget_usage"] = {"tokens": 1}
    elif mutation == "side_effect":
        candidate["side_effect_status"] = "committed"
    elif mutation == "reason":
        candidate["reason_code"] = "contradictory_failure"
    elif mutation == "extra_wire":
        candidate["unknown"] = "must not disappear during parsing"
    elif mutation == "missing_schema":
        del candidate["schema"]
    elif mutation == "invalid_unicode":
        output["output"] = "\ud800"
    else:
        candidate["budget_usage"] = None
    with pytest.raises(ValueError, match="^pi_native_result_contract_invalid$"):
        poll(adapter, command, task)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_pi_poll_rejects_success_after_task_failed_or_cancelled(status):
    adapter, command, task, _ = pending_pi_result()
    task.status = status
    with pytest.raises(ValueError, match="^pi_native_result_terminal_mismatch$"):
        poll(adapter, command, task)


@pytest.mark.parametrize("early", [True, False])
def test_valid_bounded_worker_failure_still_requires_hub_admission(early):
    from agent.services.pi_native_result_validation import validate_pi_native_result

    adapter, command, task, candidate = pending_pi_result()
    candidate["status"] = task.status = "failed"
    candidate["reason_code"] = "pi_execution_not_authorized"
    candidate["output_data"] = {} if early else {
        **candidate["output_data"], "output": "", "exit_code": 77,
    }
    assert validate_pi_native_result(candidate, command=command, hub_task_id=task.id).status == "failed"
    with pytest.raises(ValueError, match="^pi_native_result_receipt_required$"):
        poll(adapter, command, task)


def test_pi_poll_rejects_partial_text_on_failure():
    adapter, command, task, candidate = pending_pi_result()
    candidate["status"] = task.status = "failed"
    candidate["reason_code"] = "pi_execution_not_authorized"
    with pytest.raises(ValueError, match="^pi_native_result_contract_invalid$"):
        poll(adapter, command, task)


@pytest.mark.parametrize("value", [None, [], {}, "unknown"])
def test_direct_validator_closes_malformed_status_without_a_raw_type_error(value):
    from agent.services.pi_native_result_validation import validate_pi_native_result

    _, command, task, candidate = pending_pi_result()
    candidate["status"] = value
    with pytest.raises(ValueError, match="^pi_native_result_contract_invalid$"):
        validate_pi_native_result(candidate, command=command, hub_task_id=task.id)


@pytest.mark.parametrize("malformed", [False, True])
def test_real_native_pi_output_does_not_bypass_hub_admission(tmp_path, malformed):
    from agent.services.pi_native_result_validation import validate_pi_native_result
    from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand
    from tests.test_pi_coding_agent_provider import Runner
    from tests.test_pi_native_node import native_setup

    worker, task, _, _, _ = native_setup(tmp_path, runner=Runner(malformed=malformed))
    actual = worker.execute_task(task)
    repository = FakeRepository()
    stored = SimpleNamespace(**task, status=actual.status, verification_status=worker.verification_update(actual))
    repository.values[task["id"]] = stored
    hub = AnantaHubTaskQueueAdapter(
        task_queue=FakeQueue(repository), task_repository=repository, task_runtime=FakeTaskRuntime(repository),
    )
    command = NativeNodeCommand.from_mapping(task["worker_execution_context"]["native_node_command"])
    assert validate_pi_native_result(actual.to_dict(), command=command, hub_task_id=stored.id) == actual
    with pytest.raises(ValueError, match="^pi_native_result_receipt_required$"):
        poll(hub, command, stored)
    assert actual.status == ("failed" if malformed else "completed")
