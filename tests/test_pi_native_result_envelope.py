"""Actual Worker route shapes and conflicting nested-result regressions."""

import copy

import pytest
from flask import Flask

from agent.services.pi_native_result_envelope import pi_native_result_candidate
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand
from tests.test_pi_coding_agent_provider import Runner
from tests.test_pi_native_node import native_setup
from worker.runtime.workflow_adapter_task_consumer import WorkflowAdapterTaskConsumer
from worker.runtime.workflow_adapter_task_execution import consume_delegated_workflow_task
from worker.runtime.workflow_hub_gateway import HubExecutionAuthorizationAdapter


@pytest.fixture
def completed_route(tmp_path):
    return actual_route(tmp_path)


def actual_route(tmp_path, *, malformed=False):
    adapter, task, _, client, _ = native_setup(tmp_path, runner=Runner(malformed=malformed))
    app = Flask(__name__)
    app.extensions["workflow_adapter_task_consumer"] = WorkflowAdapterTaskConsumer(
        authorization=HubExecutionAuthorizationAdapter(client), native_adapter=adapter,
    )
    with app.app_context():
        response = consume_delegated_workflow_task(task)
    command = NativeNodeCommand.from_mapping(task["worker_execution_context"]["native_node_command"])
    return response, command, task["id"]


@pytest.mark.parametrize("malformed", [False, True])
def test_actual_worker_route_has_one_consistent_pi_terminal(tmp_path, malformed):
    response, command, task_id = actual_route(tmp_path, malformed=malformed)
    before = copy.deepcopy(response)
    candidate = pi_native_result_candidate(response, command=command, hub_task_id=task_id)
    assert candidate.result.status == ("failed" if malformed else "completed")
    assert candidate.result.output_data["output"] == ("" if malformed else "A\u2028B")
    assert candidate.adapter_result.hub_task_id == task_id and len(candidate.digest) == 64
    assert response == before


@pytest.mark.parametrize("field,value", [
    ("status", "failed"), ("exit_code", False), ("exit_code", 1), ("reason_code", "failure"),
    ("adapter_kind", "langgraph"), ("output", "unbound answer"), ("artifacts", [{"path": "/private"}]),
    ("sources", ["not-registry-evidence"]), ("unknown", "hidden"),
])
def test_worker_route_cannot_disagree_with_its_native_result(completed_route, field, value):
    response, command, task_id = completed_route
    response[field] = value
    with pytest.raises(ValueError, match="^pi_native_result_envelope_invalid$"):
        pi_native_result_candidate(response, command=command, hub_task_id=task_id)


@pytest.mark.parametrize("field,value", [
    ("hub_task_id", "foreign"), ("status", "failed"), ("reason_code", "failure"),
    ("adapter_kind", "langgraph"), ("artifacts", [{"path": "/private"}]),
    ("sources", [{"source_id": "unregistered"}]), ("unknown", "hidden"),
])
def test_adapter_projection_cannot_broaden_or_hide_result_scope(completed_route, field, value):
    response, command, task_id = completed_route
    response["workflow_adapter_verification"]["workflow_adapter_task_result"][field] = value
    with pytest.raises(ValueError, match="^pi_native_result_envelope_invalid$"):
        pi_native_result_candidate(response, command=command, hub_task_id=task_id)


def test_duplicate_native_verification_must_be_identical(completed_route):
    response, command, task_id = completed_route
    nested = response["workflow_adapter_verification"]["workflow_adapter_task_result"]["adapter_result"]
    nested["verification"]["native_node_result"]["output_data"]["output"] = "conflicting"
    with pytest.raises(ValueError, match="^pi_native_result_verification_conflict$"):
        pi_native_result_candidate(response, command=command, hub_task_id=task_id)


def test_framework_marker_is_not_copied_into_candidate(completed_route):
    response, command, task_id = completed_route
    response["handler_contract"] = {"private": "must-not-copy"}
    candidate = pi_native_result_candidate(response, command=command, hub_task_id=task_id)
    assert "must-not-copy" not in str(candidate)


def test_accepted_candidate_has_no_mutable_reference_to_the_worker_response(completed_route):
    response, command, task_id = completed_route
    candidate = pi_native_result_candidate(response, command=command, hub_task_id=task_id)
    before = candidate.adapter_result.to_dict()
    response["workflow_adapter_verification"]["workflow_adapter_task_result"]["adapter_result"]["output_data"].clear()
    candidate.adapter_result.adapter_result.clear()
    candidate.result.output_data.clear()
    assert candidate.adapter_result.to_dict() == before
    assert candidate.result.output_data["output"] == "A\u2028B"
