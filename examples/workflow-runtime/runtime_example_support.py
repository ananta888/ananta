"""Shared contracts for the credential-free workflow runtime example.

The values in this module are public test material.  They are deliberately
derived at runtime and must never be reused as production credentials.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from example_public_material import (
    EXAMPLE_COMMAND_KEY_ID,
    EXAMPLE_KEY_ID,
    EXAMPLE_TASK_QUEUE,
    HUB_EVENTS_FILE,
    example_bearer,
    example_command_signing_key,
    example_signing_key,
)

from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.security import HmacKeyRing, RuntimeAuthorizationEnvelope
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing
from ananta_contracts.temporal_workflow import (
    ActivityClass,
    AnantaWorkflowInput,
    AuthorizationEnvelopeRef,
    TemporalWorkflowStep,
)
from ananta_contracts.workflow_operation import operation_id_for

EXAMPLE_SCHEMA = "ananta.workflow-runtime-example-evidence.v1"
PLAN_REF = "examples/workflow-runtime/execution-plan.v1.json"
EVIDENCE_FILE = "workflow-runtime-example-v1.json"
PREPARED_FILE = "workflow-runtime-example-prepared-v1.json"
SCENARIOS = ("failure", "approval", "cancel", "crash", "resume")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_plan() -> ExecutionPlan:
    raw = json.loads((repository_root() / PLAN_REF).read_text(encoding="utf-8"))
    plan = ExecutionPlan.from_mapping(raw)
    plan.assert_valid()
    return plan


def example_key_ring() -> HmacKeyRing:
    return HmacKeyRing({EXAMPLE_KEY_ID: example_signing_key()}, active_key_id=EXAMPLE_KEY_ID)


def example_command_key_ring() -> Ed25519SigningKeyRing:
    """Issue commands under the same public-key authority the worker verifies."""

    return Ed25519SigningKeyRing(
        {EXAMPLE_COMMAND_KEY_ID: example_command_signing_key()},
        active_key_id=EXAMPLE_COMMAND_KEY_ID,
    )


def temporal_workflow_id(scenario: str) -> str:
    if scenario not in {"failure", "approval", "cancel", "crash"}:
        raise ValueError("example_temporal_scenario_invalid")
    return f"ananta-example-{scenario}-v1"


def temporal_run_id(scenario: str) -> str:
    return f"example-{scenario}-run-v1"


def build_temporal_input(
    plan: ExecutionPlan,
    *,
    scenario: str,
    issued_at: float | None = None,
) -> AnantaWorkflowInput:
    """Compile the unchanged neutral plan into the Temporal wire contract."""

    workflow_id = temporal_workflow_id(scenario)
    run_id = temporal_run_id(scenario)
    timestamp = float(issued_at if issued_at is not None else time.time())
    incoming: dict[str, list[str]] = {node.node_id: [] for node in plan.nodes}
    for edge in plan.edges:
        incoming[edge.target].append(edge.source)
    key_ring = example_key_ring()
    steps: list[TemporalWorkflowStep] = []
    for node in plan.nodes:
        envelope = RuntimeAuthorizationEnvelope.issue(
            key_ring=key_ring,
            tenant_id=plan.tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=node.node_id,
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            allowed_tools=node.allowed_tools,
            allowed_artifacts=(*node.input_artifacts, *node.output_artifacts),
            budgets={
                "retries": max(0, int((node.budget or plan.budget).max_attempts) - 1),
                "tokens": int((node.budget or plan.budget).max_tokens),
                "cost_micros": int((node.budget or plan.budget).max_cost_micros),
            },
            ttl_seconds=3_600,
            now=timestamp,
            envelope_id=f"example-{scenario}-{node.node_id}-authorization-v1",
            nonce=f"example-{scenario}-{node.node_id}-nonce-v1",
        )
        activity_class = {
            "idempotent_write": ActivityClass.IDEMPOTENT,
            "non_idempotent_write": ActivityClass.NON_IDEMPOTENT,
            "read": ActivityClass.READ_ONLY,
            "none": ActivityClass.READ_ONLY,
        }.get(node.side_effect_class, ActivityClass.IDEMPOTENT)
        steps.append(
            TemporalWorkflowStep(
                step_id=node.node_id,
                title=node.node_id,
                operation_id=operation_id_for(
                    tenant_id=plan.tenant_id,
                    run_id=run_id,
                    step_id=node.node_id,
                    declared_operation=str(node.metadata.get("operation_name") or "example_hub_task"),
                ),
                authorization_envelope=AuthorizationEnvelopeRef.from_mapping(envelope.to_dict()),
                depends_on=tuple(incoming[node.node_id]),
                activity_class=activity_class,
                gate=bool(node.gate_id),
                task_kind=node.task_kind,
                required_capabilities=node.required_capabilities,
                node_type=node.node_type,
            )
        )
    return AnantaWorkflowInput(
        tenant_id=plan.tenant_id,
        workflow_id=workflow_id,
        run_id=run_id,
        correlation_id=f"example-{scenario}-correlation-v1",
        plan_hash=plan.plan_hash,
        policy_version=plan.policy_version,
        steps=tuple(steps),
        retry_budget_remaining=max(0, plan.budget.max_attempts - 1),
        retry_budget_maximum=max(0, plan.budget.max_attempts - 1),
        max_parallel_steps=1,
        tenant_parallel_limit=1,
        worker_parallel_limit=1,
    )


def signed_temporal_command(
    workflow_input: AnantaWorkflowInput,
    status: dict[str, Any],
    *,
    command_type: str,
    command_id: str,
    payload: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    command = SignedWorkflowCommand.issue(
        key_ring=example_command_key_ring(),
        command_type=command_type,
        tenant_id=workflow_input.tenant_id,
        workflow_id=workflow_input.workflow_id,
        run_id=workflow_input.run_id,
        step_id=str(status["current_step_id"]),
        checkpoint_id=str(status["checkpoint_ref"]),
        expected_revision=int(status["revision"]),
        plan_hash=str(status["plan_hash"]),
        policy_version=workflow_input.policy_version,
        actor_id="example-operator",
        actor_roles=("operator",),
        payload=dict(payload or {}),
        ttl_seconds=600,
        now=now,
        command_id=command_id,
        nonce=f"{command_id}-nonce",
    )
    return command.to_dict()


def evidence_boundary() -> list[dict[str, Any]]:
    return [
        {
            "id": "native-example-ports",
            "classification": "deterministic_example_double",
            "production_equivalent": False,
            "description": "In-memory Hub task, checkpoint and event ports; real Native orchestrator.",
        },
        {
            "id": "langgraph-example-node-executor",
            "classification": "deterministic_example_double",
            "production_equivalent": False,
            "description": "Offline node executor behind the real pinned StateGraph ExecutionPlan runtime.",
        },
        {
            "id": "temporal-example-hub",
            "classification": "deterministic_example_double",
            "production_equivalent": False,
            "description": (
                "Separate Hub-port emulator; real Temporal server, worker, workflow and HTTP Activity gateway."
            ),
        },
    ]


def assert_evidence_contract(evidence: dict[str, Any], *, final: bool) -> None:
    if evidence.get("schema") != EXAMPLE_SCHEMA:
        raise ValueError("example_evidence_schema_invalid")
    if evidence.get("classification") != "example_only":
        raise ValueError("example_evidence_classification_invalid")
    if evidence.get("production_release_gate") is not False:
        raise ValueError("example_evidence_must_not_be_release_gate")
    if evidence.get("plan", {}).get("ref") != PLAN_REF:
        raise ValueError("example_evidence_plan_ref_invalid")
    plan_hashes = {
        runtime.get("plan_hash") for runtime in (evidence.get("runtimes") or {}).values() if isinstance(runtime, dict)
    }
    if plan_hashes != {evidence.get("plan", {}).get("hash")}:
        raise ValueError("example_evidence_plan_hash_drift")
    boundaries = evidence.get("boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        raise ValueError("example_evidence_boundaries_missing")
    if final:
        if evidence.get("status") != "passed_example":
            raise ValueError("example_evidence_not_complete")
        temporal = evidence.get("runtimes", {}).get("temporal", {})
        if set(temporal.get("scenarios") or {}) != set(SCENARIOS):
            raise ValueError("example_evidence_temporal_scenarios_incomplete")
        if temporal.get("durable_server_path") is not True:
            raise ValueError("example_evidence_temporal_not_durable")


__all__ = [
    "EVIDENCE_FILE",
    "EXAMPLE_KEY_ID",
    "EXAMPLE_SCHEMA",
    "EXAMPLE_TASK_QUEUE",
    "HUB_EVENTS_FILE",
    "PLAN_REF",
    "PREPARED_FILE",
    "SCENARIOS",
    "assert_evidence_contract",
    "build_temporal_input",
    "evidence_boundary",
    "example_bearer",
    "example_command_key_ring",
    "example_key_ring",
    "example_signing_key",
    "load_plan",
    "repository_root",
    "signed_temporal_command",
    "temporal_run_id",
    "temporal_workflow_id",
]
