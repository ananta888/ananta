from __future__ import annotations

import hashlib
import time
from typing import Any

import pytest

from agent.auth import resolve_configured_agent_token
from agent.db_models import AgentInfoDB, GoalDB, PlanDB, PlanNodeDB, TaskDB
from agent.services.model_recovery_signal import build_model_recovery_signal
from agent.services.task_recovery_planning_service import (
    RECOVERY_MATERIALIZE_TOOL,
    TaskRecoveryPlanningService,
)


class _PersistedRecoveryPlanner:
    """Deterministic planning port used behind the real Hub recovery service."""

    def __init__(self, repositories: Any) -> None:
        self._repositories = repositories
        self._stats = {"tasks_created": 0}
        self.calls: list[dict[str, Any]] = []

    def plan_goal(self, **values: Any) -> dict[str, Any]:
        self.calls.append(dict(values))
        plan = self._repositories.plan_repo.save(
            PlanDB(
                id="recovery-plan-flow",
                goal_id=values["goal_id"],
                trace_id=values["goal_trace_id"],
                status="draft",
                planning_mode="task_recovery",
                rationale=dict(values.get("initial_plan_rationale") or {}),
            )
        )
        specifications = (
            (
                "recovery-node-code",
                "code",
                "Implement bounded recovery",
                "Implement the concrete Python recovery adapter file and run its command.",
                "coding",
                [],
            ),
            (
                "recovery-node-test",
                "test",
                "Verify bounded recovery",
                "Create and run regression tests for the Python recovery adapter.",
                "testing",
                ["code"],
            ),
            (
                "recovery-node-review",
                "review",
                "Review recovery result",
                "Review the changed files, test artifact, and final handoff result.",
                "review",
                ["test"],
            ),
        )
        for position, (
            node_id,
            node_key,
            title,
            description,
            task_kind,
            depends_on,
        ) in enumerate(specifications, start=1):
            expected_artifacts = [
                {
                    "kind": "workspace_change",
                    "required": True,
                    "description": f"{node_key}-artifact",
                }
            ]
            self._repositories.plan_node_repo.save(
                PlanNodeDB(
                    id=node_id,
                    plan_id=plan.id,
                    node_key=node_key,
                    title=title,
                    description=description,
                    priority="High",
                    status="draft",
                    position=position,
                    depends_on=depends_on,
                    rationale={
                        "task_kind": task_kind,
                        "required_capabilities": [],
                        "expected_artifacts": expected_artifacts,
                    },
                    verification_spec={
                        "expected_artifacts": expected_artifacts,
                    },
                )
            )
        return {
            "plan_id": plan.id,
            "created_task_ids": [],
            "subtasks": [{}, {}, {}],
        }


def _terminal_worker_signal() -> dict[str, Any]:
    calls = [
        {
            "name": f"phi-{attempt}",
            "backend": "ollama",
            "profile_id": "local_ollama_phi4_mini",
            "provider": "ollama",
            "model": "ananta-phi4-mini-32k",
            "success": False,
            "error_type": "schema_validation_failed",
        }
        for attempt in range(1, 4)
    ]
    calls.extend(
        {
            "name": f"gemma-{attempt}",
            "backend": "ollama",
            "profile_id": "local_ollama_gemma4_e4b_reasoning",
            "provider": "ollama",
            "model": "ananta-gemma4-reasoning-8k",
            "success": False,
            "error_type": "schema_validation_failed",
        }
        for attempt in range(1, 3)
    )
    return build_model_recovery_signal(
        terminal_reason="schema_validation_failed",
        llm_call_profile=calls,
    )


def _deterministic_child_id(
    *,
    plan_id: str,
    node_id: str,
    node_key: str,
) -> str:
    digest = hashlib.sha256(
        f"{plan_id}\x00{node_id}\x00{node_key}".encode("utf-8")
    ).hexdigest()
    return f"goal-{digest[:16]}"


@pytest.mark.parametrize(
    (
        "recovery_actions",
        "invalid_terminal_signal",
        "coordinator_raises",
        "expect_plan",
        "expect_compaction",
        "expect_segmented_plan",
    ),
    [
        (
            [
                "compact_context",
                "segment_planning",
                "propose_task_plan",
                "require_approval",
                "stop",
            ],
            False,
            False,
            True,
            True,
            True,
        ),
        (
            ["segment_planning", "require_approval", "stop"],
            False,
            False,
            True,
            False,
            True,
        ),
        (
            ["propose_task_plan", "require_approval", "stop"],
            False,
            False,
            True,
            False,
            False,
        ),
        (["stop"], False, False, False, False, False),
        (
            ["compact_context", "stop"],
            False,
            False,
            False,
            True,
            False,
        ),
        ([], False, False, False, False, False),
        (
            [
                "compact_context",
                "segment_planning",
                "propose_task_plan",
                "require_approval",
                "stop",
            ],
            True,
            False,
            False,
            False,
            False,
        ),
        (
            [
                "compact_context",
                "segment_planning",
                "propose_task_plan",
                "require_approval",
                "stop",
            ],
            False,
            True,
            False,
            False,
            False,
        ),
    ],
    ids=[
        "full-chain",
        "segment-with-approval",
        "proposal-with-approval",
        "stop-only",
        "compact-then-stop",
        "recovery-disabled",
        "rejected-terminal-signal",
        "coordinator-failure",
    ],
)
def test_worker_exhaustion_flows_through_hub_tick_and_admin_approval(
    client,
    app,
    admin_auth_header,
    monkeypatch,
    recovery_actions,
    invalid_terminal_signal,
    coordinator_raises,
    expect_plan,
    expect_compaction,
    expect_segmented_plan,
):
    import agent.services.task_recovery_planning_service as recovery_module
    from agent.config import settings
    from agent.routes.tasks.autopilot import autonomous_loop
    from agent.services.approval_request_service import (
        get_approval_request_service,
    )
    from agent.services.repository_registry import get_repository_registry

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_can_be_worker", False)
    monkeypatch.setattr(
        "agent.services.workflow_worker_assignment_runtime"
        ".bind_dispatched_workflow_task",
        lambda **_values: None,
    )

    with app.app_context():
        repositories = get_repository_registry(app)
        goal = repositories.goal_repo.save(
            GoalDB(
                id="goal-recovery-flow",
                trace_id="trace-recovery-flow",
                goal="Exercise Hub-controlled recovery",
                status="in_progress",
                source="test",
            )
        )
        source_task = repositories.task_repo.save(
            TaskDB(
                id="task-recovery-flow",
                title="Implement local model fallback",
                description=(
                    "Implement and verify the bounded Phi to Gemma fallback."
                ),
                status="todo",
                goal_id=goal.id,
                goal_trace_id=goal.trace_id,
                worker_execution_context={
                    "model_routing": {
                        "preferred_profile_id": (
                            "local_ollama_phi4_mini"
                        ),
                        "fallback_group_id": (
                            "local_phi_to_gemma_reasoning"
                        ),
                        "context_recovery_strategies": list(
                            recovery_actions
                        ),
                        "require_approval_for_generated_plan": True,
                    }
                },
            )
        )
        repositories.agent_repo.save(
            AgentInfoDB(
                url="http://recovery-worker:5000",
                name="recovery-worker",
                role="worker",
                token="recovery-worker-token",
                status="online",
                registration_validated=True,
                last_seen=time.time(),
            )
        )

        planner = _PersistedRecoveryPlanner(repositories)
        recovery_service = TaskRecoveryPlanningService(
            role_provider=lambda: "hub",
            planner_provider=lambda: planner,
        )
        monkeypatch.setattr(recovery_module, "_service", recovery_service)
        if coordinator_raises:
            def raise_coordinator_error(
                _executor,
                **_values,
            ):
                raise RuntimeError("coordinator unavailable")

            monkeypatch.setattr(
                "agent.services.model_recovery_strategy_executor"
                ".ModelRecoveryStrategyExecutor"
                ".execute_after_model_exhaustion",
                raise_coordinator_error,
            )

        agent_config = dict(app.config.get("AGENT_CONFIG") or {})
        agent_config.update(
            {
                "adaptive_model_routing_enabled": False,
                "autopilot_strategy_max_attempts": 3,
                "default_model": "ananta-phi4-mini-32k",
                "proposal_budget": {
                    "max_total_seconds": 30,
                    "max_llm_calls": 2,
                    "max_strategy_attempts": 2,
                },
                "propose_policy": {
                    "allow_human_review": True,
                    "on_all_strategies_declined": "needs_review",
                },
            }
        )
        app.config["AGENT_CONFIG"] = agent_config

        if invalid_terminal_signal:
            signal = {
                "schema": "model_recovery_signal.v1",
                "state": "exhausted",
                "terminal": True,
                "reason_code": "model_fallback_exhausted",
                "terminal_reason": (
                    "provider_attempt_plan_sequence_denied"
                ),
                "fallback_decisions": [],
                "llm_calls": [],
            }
        else:
            signal = _terminal_worker_signal()
            assert signal["attempt_count"] == 5

        forwarded: list[tuple[str, str]] = []

        def worker_response(
            worker_url: str,
            endpoint: str,
            _payload: dict[str, Any],
            token: str | None = None,
        ) -> dict[str, Any]:
            del token
            forwarded.append((worker_url, endpoint))
            assert endpoint.endswith("/step/propose")
            return {
                "status": "success",
                "data": {
                    "reason": "local model chain exhausted",
                    "metadata": {
                        "model_recovery_signal": signal,
                    },
                },
            }

        monkeypatch.setattr(
            "agent.routes.tasks.autopilot._forward_to_worker",
            worker_response,
        )
        autonomous_loop.bind_app(app)
        autonomous_loop.goal = goal.id
        autonomous_loop.max_concurrency = 1

        tick_result = autonomous_loop.tick_once()

        assert tick_result["reason"] == "ok"
        assert len(forwarded) == 1
        pending_source = repositories.task_repo.get_by_id(source_task.id)
        if invalid_terminal_signal:
            assert pending_source.status == "waiting_for_review"
            assert pending_source.status_reason_code == (
                "invalid_terminal_model_recovery_signal"
            )
            assert "model_recovery" not in dict(
                pending_source.status_reason_details or {}
            )
            assert planner.calls == []
            assert len(
                repositories.task_repo.get_by_goal_id(goal.id)
            ) == 1
            autonomous_loop.tick_once()
            assert len(forwarded) == 1
            return

        if coordinator_raises:
            assert pending_source.status == "waiting_for_review"
            assert pending_source.status_reason_code == (
                "task_recovery_coordinator_failed"
            )
            assert planner.calls == []
            autonomous_loop.tick_once()
            assert len(forwarded) == 1
            return

        if not expect_plan:
            assert pending_source.status == "waiting_for_review"
            expected_reason = (
                "model_recovery_disabled"
                if not recovery_actions
                else "model_recovery_stop_selected"
            )
            assert pending_source.status_reason_code == expected_reason
            strategy_state = pending_source.status_reason_details[
                "model_recovery_strategy"
            ]
            assert strategy_state["reason_code"] == expected_reason
            assert bool(strategy_state["compaction"]) is (
                expect_compaction
            )
            assert "model_recovery" not in dict(
                pending_source.status_reason_details or {}
            )
            assert planner.calls == []
            assert len(
                repositories.task_repo.get_by_goal_id(goal.id)
            ) == 1
            autonomous_loop.tick_once()
            assert len(forwarded) == 1
            return

        assert pending_source.status == "waiting_for_review"
        pending_state = pending_source.status_reason_details["model_recovery"]
        approval_id = pending_state["approval_request_id"]
        plan_id = pending_state["plan_id"]
        assert plan_id == "recovery-plan-flow"
        assert len(repositories.task_repo.get_by_goal_id(goal.id)) == 1
        assert len(planner.calls) == 1
        assert planner.calls[0]["mode_data"]["segment_planning"] is (
            expect_segmented_plan
        )

        pending_plan = repositories.plan_repo.get_by_id(plan_id)
        assert pending_plan.status == "pending_approval"
        failure_signal = pending_plan.rationale["failure_signal"]
        assert failure_signal["attempt_count"] == 5
        assert set(failure_signal["failed_profile_ids"]) == {
            "local_ollama_phi4_mini",
            "local_ollama_gemma4_e4b_reasoning",
        }
        compaction_status = str(
            pending_plan.rationale["compaction"].get("status")
            or ""
        )
        assert (
            compaction_status != "bounded_without_compactor"
        ) is expect_compaction
        approval = get_approval_request_service().get_request(approval_id)
        assert approval.tool_name == RECOVERY_MATERIALIZE_TOOL
        assert approval.status == "pending"

    decision = client.post(
        f"/api/approvals/{approval_id}/decision",
        headers={
            "Authorization": "Bearer "
            + str(resolve_configured_agent_token(app.config) or "")
        },
        json={
            "decision": "granted",
            "reason": "approved deterministic recovery plan",
        },
    )

    assert decision.status_code == 200, decision.get_json()
    with app.app_context():
        repositories = get_repository_registry(app)
        nodes = repositories.plan_node_repo.get_by_plan_id(plan_id)
        expected_child_ids = [
            _deterministic_child_id(
                plan_id=plan_id,
                node_id=node.id,
                node_key=node.node_key,
            )
            for node in nodes
        ]
        children = [
            repositories.task_repo.get_by_id(task_id)
            for task_id in expected_child_ids
        ]

        assert all(child is not None for child in children)
        assert [child.status for child in children] == [
            "todo",
            "blocked_by_dependency",
            "blocked_by_dependency",
        ]
        assert [child.source_task_id for child in children] == [
            source_task.id,
            source_task.id,
            source_task.id,
        ]
        assert [child.parent_task_id for child in children] == [
            None,
            None,
            None,
        ]
        assert children[1].depends_on == [expected_child_ids[0]]
        assert children[2].depends_on == [expected_child_ids[1]]

        materialized_source = repositories.task_repo.get_by_id(
            source_task.id
        )
        assert materialized_source.status == "blocked_by_dependency"
        assert materialized_source.depends_on == expected_child_ids
        assert repositories.plan_repo.get_by_id(plan_id).status == (
            "materialized"
        )
        assert get_approval_request_service().get_request(
            approval_id
        ).status == "consumed"
