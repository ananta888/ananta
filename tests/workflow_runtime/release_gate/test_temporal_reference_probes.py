from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("temporalio")

from agent.services.workflow_runtime.reference_workflows import load_reference_workflows  # noqa: E402
from ananta_contracts.temporal_workflow import (  # noqa: E402
    AnantaWorkflowInput,
    TemporalWorkflowStep,
    WorkflowCommandType,
)
from ananta_contracts.workflow_operation import operation_id_for  # noqa: E402
from tests.workflow_runtime.release_gate.evidence_helpers import (  # noqa: E402
    emit_reference_run_evidence,
)
from tests.workflow_runtime.temporal.test_temporal_runtime_test_environment import (  # noqa: E402
    PLAN_HASH,
    ScriptedHubGateway,
    _authorization,
    _command,
    _running_worker,
    _wait_for_status,
)


def _step(
    *,
    workflow_id: str,
    run_id: str,
    step_id: str,
    depends_on: tuple[str, ...] = (),
    gate: bool = False,
    task_kind: str = "coding",
    node_type: str = "task",
    merge_strategy: str = "",
    declared_operation: str = "hub_task",
) -> TemporalWorkflowStep:
    return TemporalWorkflowStep(
        step_id=step_id,
        title=step_id,
        operation_id=operation_id_for(
            tenant_id="tenant-1",
            run_id=run_id,
            step_id=step_id,
            declared_operation=declared_operation,
        ),
        authorization_envelope=_authorization(
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
        ),
        depends_on=depends_on,
        gate=gate,
        task_kind=task_kind,
        node_type=node_type,
        merge_strategy=merge_strategy,
    )


def _input(
    *,
    workflow_id: str,
    run_id: str,
    steps: tuple[TemporalWorkflowStep, ...],
    parallel: int = 1,
) -> AnantaWorkflowInput:
    return AnantaWorkflowInput(
        tenant_id="tenant-1",
        workflow_id=workflow_id,
        run_id=run_id,
        correlation_id=f"correlation-{run_id}",
        plan_hash=PLAN_HASH,
        policy_version="policy-v1",
        steps=steps,
        retry_budget_remaining=0,
        max_parallel_steps=parallel,
        tenant_parallel_limit=parallel,
        worker_parallel_limit=parallel,
    )


def test_temporal_executes_every_durable_reference_scenario_ten_times() -> None:
    async def scenario() -> None:
        scenarios = {item.scenario_id: item for item in load_reference_workflows()}
        gateway = ScriptedHubGateway()
        async with _running_worker(gateway) as (environment, task_queue):
            for scenario_id, task_kind in (
                ("research", "research"),
                ("code-analysis", "code_analysis"),
            ):
                for iteration in range(1, 11):
                    workflow_id = f"release-{scenario_id}-{iteration}-{uuid.uuid4().hex}"
                    run_id = f"run-{scenario_id}-{iteration}"
                    required_artifacts = scenarios[scenario_id].invariants.required_artifacts
                    gateway.artifacts_by_step = {
                        "execute": tuple(
                            {"artifact_id": artifact_id, "kind": "generated"}
                            for artifact_id in required_artifacts
                        )
                    }
                    workflow_input = _input(
                        workflow_id=workflow_id,
                        run_id=run_id,
                        steps=(
                            _step(
                                workflow_id=workflow_id,
                                run_id=run_id,
                                step_id="execute",
                                task_kind=task_kind,
                            ),
                        ),
                    )
                    result = await environment.client.execute_workflow(
                        "AnantaWorkflow",
                        workflow_input.to_dict(),
                        id=workflow_id,
                        task_queue=task_queue,
                    )
                    assert result["status"] == "completed"
                    assert result["completed_step_ids"] == ["execute"]
                    assert {
                        item["artifact_id"] for item in gateway.artifacts_by_step["execute"]
                    } == set(required_artifacts)
                    emit_reference_run_evidence(
                        runtime_id="temporal",
                        scenario=scenarios[scenario_id],
                        iteration=iteration,
                        run_id=run_id,
                        terminal_status=result["status"],
                        event_types={
                            "workflow.run.started",
                            "workflow.step.completed",
                            "workflow.run.completed",
                        },
                        artifact_ids=required_artifacts,
                        policy_decisions=scenarios[scenario_id].invariants.required_policy_decisions,
                        proofs={
                            "artifact": "passed",
                            "event": "passed",
                            "port": "passed",
                            "security": "passed",
                        },
                    )

            for iteration in range(1, 11):
                workflow_id = f"release-approval-{iteration}-{uuid.uuid4().hex}"
                run_id = f"run-approval-{iteration}"
                required_artifacts = scenarios["approval"].invariants.required_artifacts
                gateway.artifacts_by_step = {
                    "publish-approval": tuple(
                        {"artifact_id": artifact_id, "kind": "generated"}
                        for artifact_id in required_artifacts
                    )
                }
                workflow_input = _input(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    steps=(
                        _step(
                            workflow_id=workflow_id,
                            run_id=run_id,
                            step_id="publish-approval",
                            gate=True,
                            declared_operation="publish-artifact",
                        ),
                    ),
                )
                handle = await environment.client.start_workflow(
                    "AnantaWorkflow",
                    workflow_input.to_dict(),
                    id=workflow_id,
                    task_queue=task_queue,
                )
                waiting = await _wait_for_status(handle, "waiting_approval")
                await handle.execute_update(
                    "command",
                    _command(
                        workflow_input,
                        command_id=f"approve-{iteration}",
                        command_type=WorkflowCommandType.APPROVE,
                        revision=waiting["revision"],
                    ),
                )
                approval_result = await handle.result()
                assert approval_result["status"] == "completed"
                assert approval_result["completed_step_ids"] == ["publish-approval"]
                emit_reference_run_evidence(
                    runtime_id="temporal",
                    scenario=scenarios["approval"],
                    iteration=iteration,
                    run_id=run_id,
                    terminal_status=approval_result["status"],
                    event_types={
                        "workflow.run.started",
                        "workflow.approval.granted",
                        "workflow.side_effect.completed",
                        "workflow.run.completed",
                    },
                    artifact_ids=required_artifacts,
                    gate_ids={"publish-approval"},
                    side_effect_operations={"publish-artifact"},
                    policy_decisions=scenarios["approval"].invariants.required_policy_decisions,
                    proofs={
                        "approval": "passed",
                        "artifact": "passed",
                        "event": "passed",
                        "ledger": "passed",
                        "port": "passed",
                        "security": "passed",
                    },
                )

            gateway.submission_delay_seconds = 0.01
            for iteration in range(1, 11):
                workflow_id = f"release-parallel-{iteration}-{uuid.uuid4().hex}"
                run_id = f"run-parallel-{iteration}"
                gateway.artifacts_by_step = {
                    "branch-a": ({"artifact_id": "branch-a-result", "kind": "generated"},),
                    "branch-b": ({"artifact_id": "branch-b-result", "kind": "generated"},),
                    "merge": ({"artifact_id": "merged-result", "kind": "generated"},),
                }
                workflow_input = _input(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    parallel=2,
                    steps=(
                        _step(workflow_id=workflow_id, run_id=run_id, step_id="branch-a"),
                        _step(workflow_id=workflow_id, run_id=run_id, step_id="branch-b"),
                        _step(
                            workflow_id=workflow_id,
                            run_id=run_id,
                            step_id="merge",
                            depends_on=("branch-a", "branch-b"),
                            node_type="merge",
                            merge_strategy="ordered_artifact_refs",
                        ),
                    ),
                )
                result = await environment.client.execute_workflow(
                    "AnantaWorkflow",
                    workflow_input.to_dict(),
                    id=workflow_id,
                    task_queue=task_queue,
                )
                assert result["completed_step_ids"] == ["branch-a", "branch-b", "merge"]
                assert gateway.submitted_artifacts_by_step["merge"] == (
                    "branch-a-result",
                    "branch-b-result",
                )
                emit_reference_run_evidence(
                    runtime_id="temporal",
                    scenario=scenarios["bounded-parallel-merge"],
                    iteration=iteration,
                    run_id=run_id,
                    terminal_status=result["status"],
                    event_types={
                        "workflow.run.started",
                        "workflow.step.completed",
                        "workflow.run.completed",
                    },
                    artifact_ids={"branch-a-result", "branch-b-result", "merged-result"},
                    policy_decisions=(
                        scenarios["bounded-parallel-merge"].invariants.required_policy_decisions
                    ),
                    proofs={
                        "artifact": "passed",
                        "event": "passed",
                        "port": "passed",
                        "security": "passed",
                    },
                )
            assert gateway.max_active_submissions == 2
            gateway.submission_delay_seconds = 0

            for iteration in range(1, 11):
                workflow_id = f"release-resume-{iteration}-{uuid.uuid4().hex}"
                run_id = f"run-resume-{iteration}"
                required_artifacts = scenarios["long-running-resume"].invariants.required_artifacts
                gateway.artifacts_by_step = {
                    "resume-gate": tuple(
                        {"artifact_id": artifact_id, "kind": "generated"}
                        for artifact_id in required_artifacts
                    )
                }
                workflow_input = _input(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    steps=(
                        _step(
                            workflow_id=workflow_id,
                            run_id=run_id,
                            step_id="resume-gate",
                            gate=True,
                            task_kind="long_running",
                        ),
                    ),
                )
                handle = await environment.client.start_workflow(
                    "AnantaWorkflow",
                    workflow_input.to_dict(),
                    id=workflow_id,
                    task_queue=task_queue,
                )
                waiting = await _wait_for_status(handle, "waiting_approval")
                await handle.execute_update(
                    "command",
                    _command(
                        workflow_input,
                        command_id=f"pause-{iteration}",
                        command_type=WorkflowCommandType.PAUSE,
                        revision=waiting["revision"],
                    ),
                )
                paused = await _wait_for_status(handle, "paused")
                await handle.execute_update(
                    "command",
                    _command(
                        workflow_input,
                        command_id=f"resume-{iteration}",
                        command_type=WorkflowCommandType.RESUME,
                        revision=paused["revision"],
                    ),
                )
                resumed = await _wait_for_status(handle, "waiting_approval")
                await handle.execute_update(
                    "command",
                    _command(
                        workflow_input,
                        command_id=f"finish-{iteration}",
                        command_type=WorkflowCommandType.APPROVE,
                        revision=resumed["revision"],
                    ),
                )
                result = await handle.result()
                assert result["status"] == "completed"
                assert result["completed_step_ids"] == ["resume-gate"]
                emit_reference_run_evidence(
                    runtime_id="temporal",
                    scenario=scenarios["long-running-resume"],
                    iteration=iteration,
                    run_id=run_id,
                    terminal_status=result["status"],
                    event_types={
                        "workflow.run.started",
                        "workflow.checkpoint.created",
                        "workflow.run.paused",
                        "workflow.run.resumed",
                        "workflow.run.completed",
                    },
                    artifact_ids=required_artifacts,
                    gate_ids={"resume-gate"},
                    policy_decisions=(
                        scenarios["long-running-resume"].invariants.required_policy_decisions
                    ),
                    proofs={
                        "approval": "passed",
                        "artifact": "passed",
                        "checkpoint": "passed",
                        "event": "passed",
                        "port": "passed",
                        "recovery": "passed",
                        "security": "passed",
                    },
                )

    asyncio.run(scenario())
