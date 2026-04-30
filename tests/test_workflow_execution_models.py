from __future__ import annotations

import pytest

from agent.models import WorkflowExecutionRequestModel


def test_workflow_execution_request_model_valid() -> None:
    req = WorkflowExecutionRequestModel(provider="mock", workflow_id="wf", task_id="T1", goal_id="G1", trace_id="X", input_payload={"k": "v"}, dry_run=True, requested_by="tester")
    assert req.workflow_id == "wf"


def test_workflow_execution_request_model_requires_ids() -> None:
    with pytest.raises(Exception):
        WorkflowExecutionRequestModel(provider="", workflow_id="", requested_by="x")
