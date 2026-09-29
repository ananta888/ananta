from __future__ import annotations

from typing import Any

import pytest

from agent.services.workflow_backend import WORKFLOW_STATUS_SCHEMA
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_QUEUE_RESERVE,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_LANGGRAPH,
    TRANSITION_RUNTIME_NATIVE,
    WorkflowTransition,
    WorkflowTransitionEffect,
    workflow_transition_id,
)
from agent.services.workflow_transition_public_projection import (
    WorkflowTransitionPublicStatusProjector,
)

_PLAN_HASH = "f" * 64


def _transition(runtime_id: str) -> WorkflowTransition:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        kind=TRANSITION_KIND_COMMAND,
        identity_key="command-a",
    )
    effects = (
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=1,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key="task-a",
            payload={"task_id": "task-a"},
            created_at=1_000.0,
        ),
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=2,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key="workflow-a",
            payload={"workflow_id": "workflow-a"},
            created_at=1_000.0,
        ),
    )
    return WorkflowTransition.build(
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        kind=TRANSITION_KIND_COMMAND,
        command_id="command-a",
        receipt_id="command-a",
        admitted_command={"command_id": "command-a"},
        request_payload={"command": "advance"},
        effects=effects,
        expected_revision=7,
        expected_checkpoint_ref="checkpoint-7",
        created_at=1_000.0,
    )


def _binding(runtime_id: str) -> dict[str, Any]:
    return {
        "tenant_id": "tenant-a",
        "subject_id": "subject-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "runtime_id": runtime_id,
        "plan_hash": _PLAN_HASH,
        "policy_version": "policy-v1",
        "checkpoint_id": "checkpoint-initial",
        "workflow_request": {
            "workflow_id": "workflow-a",
            "correlation_id": "correlation-a",
            "requested_by": "subject-a",
            "steps": [],
        },
        "execution_plan": {},
    }


def _raw_status(runtime_id: str, *, revision: int = 8, status: str = "running") -> dict[str, Any]:
    if runtime_id == "local":
        backend = "local"
        checkpoint_ref = f"wfc-{'a' * 32}"
    else:
        backend = TRANSITION_RUNTIME_LANGGRAPH
        checkpoint_ref = f"langgraph:{_PLAN_HASH}:{revision}"
    return {
        "schema": WORKFLOW_STATUS_SCHEMA,
        "backend": backend,
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "plan_hash": _PLAN_HASH,
        "status": status,
        "revision": revision,
        "checkpoint_ref": checkpoint_ref,
        "steps": [],
        "updated_at": 1_000.0 + revision,
    }


@pytest.mark.parametrize(
    ("binding_runtime", "transition_runtime", "expected_checkpoint"),
    [
        ("local", TRANSITION_RUNTIME_NATIVE, "local:workflow-a:8"),
        (
            TRANSITION_RUNTIME_LANGGRAPH,
            TRANSITION_RUNTIME_LANGGRAPH,
            f"langgraph:{_PLAN_HASH}:8",
        ),
    ],
)
def test_projector_derives_exact_binding_aware_native_and_langgraph_status(
    binding_runtime: str,
    transition_runtime: str,
    expected_checkpoint: str,
) -> None:
    projected = WorkflowTransitionPublicStatusProjector().project(
        transition=_transition(transition_runtime),
        binding=_binding(binding_runtime),
        binding_status=_raw_status(binding_runtime),
        previous_public_status=None,
    )

    assert projected["schema"] == WORKFLOW_STATUS_SCHEMA
    assert projected["runtime_id"] == transition_runtime
    assert projected["tenant_id"] == "tenant-a"
    assert projected["workflow_id"] == "workflow-a"
    assert projected["run_id"] == "run-a"
    assert projected["plan_hash"] == _PLAN_HASH
    assert projected["checkpoint_ref"] == expected_checkpoint
    assert projected["source_observation"]["backend"] == _raw_status(binding_runtime)["backend"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda raw: raw.pop("workflow_id"), "workflow_runtime_source_workflow_id_required"),
        (lambda raw: raw.update(schema="unknown.status.v1"), "workflow_runtime_source_schema_unsupported"),
        (lambda raw: raw.update(backend="langgraph"), "workflow_runtime_source_backend_mismatch"),
    ],
)
def test_projector_rejects_missing_identity_schema_and_provenance(
    mutate: Any,
    reason: str,
) -> None:
    raw = _raw_status("local")
    mutate(raw)
    with pytest.raises(ValueError, match=reason):
        WorkflowTransitionPublicStatusProjector().project(
            transition=_transition(TRANSITION_RUNTIME_NATIVE),
            binding=_binding("local"),
            binding_status=raw,
            previous_public_status=None,
        )


def test_projector_rejects_runtime_mismatch_and_public_progression_conflicts() -> None:
    projector = WorkflowTransitionPublicStatusProjector()
    transition = _transition(TRANSITION_RUNTIME_NATIVE)
    with pytest.raises(ValueError, match="workflow_control_public_status_binding_mismatch"):
        projector.project(
            transition=transition,
            binding=_binding(TRANSITION_RUNTIME_LANGGRAPH),
            binding_status=_raw_status(TRANSITION_RUNTIME_LANGGRAPH),
            previous_public_status=None,
        )

    previous = projector.project(
        transition=transition,
        binding=_binding("local"),
        binding_status=_raw_status("local"),
        previous_public_status=None,
    )
    regressed = projector.project(
        transition=transition,
        binding=_binding("local"),
        binding_status=_raw_status("local", revision=7),
        previous_public_status=None,
    )
    with pytest.raises(RuntimeError, match="workflow_control_public_status_revision_regressed"):
        projector.project(
            transition=transition,
            binding=_binding("local"),
            binding_status=regressed,
            previous_public_status=previous,
        )
    divergent = projector.project(
        transition=transition,
        binding=_binding("local"),
        binding_status=_raw_status("local", status="paused"),
        previous_public_status=None,
    )
    with pytest.raises(RuntimeError, match="workflow_control_public_status_revision_conflict"):
        projector.project(
            transition=transition,
            binding=_binding("local"),
            binding_status=divergent,
            previous_public_status=previous,
        )
