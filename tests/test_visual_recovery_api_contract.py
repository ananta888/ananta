from __future__ import annotations

import time
from typing import Any

import jwt

from agent.config import settings
from agent.db_models import GoalDB, PlanDB, PlanNodeDB, TaskDB, TeamDB
from agent.services.model_recovery_signal import build_model_recovery_signal
from agent.services.recovery_plan_contract import (
    calculate_recovery_plan_digest,
)
from agent.services.task_recovery_planning_service import (
    RECOVERY_MATERIALIZE_TOOL,
    TaskRecoveryPlanningService,
)


def _team_auth_headers(
    team_id: str,
    *,
    role: str = "user",
) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": f"operator-{team_id}",
            "role": role,
            "tenant_id": team_id,
            "team_id": team_id,
            "iat": now,
            "exp": now + 3600,
        },
        settings.secret_key,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class _PersistedRecoveryPlanner:
    """Small deterministic planning port for the Hub-owned HTTP contract test."""

    def __init__(self, repositories: Any) -> None:
        self._repositories = repositories
        self._stats = {"tasks_created": 0}

    def plan_goal(self, **values: Any) -> dict[str, Any]:
        plan = self._repositories.plan_repo.save(
            PlanDB(
                id="recovery-api-plan",
                goal_id=values["goal_id"],
                trace_id=values["goal_trace_id"],
                status="draft",
                planning_mode="task_recovery",
                rationale=dict(values.get("initial_plan_rationale") or {}),
            )
        )
        specifications = (
            (
                "recovery-api-node-code",
                "code",
                "Implement bounded recovery",
                "Implement the concrete recovery adapter and record its artifact.",
                "coding",
                [],
            ),
            (
                "recovery-api-node-test",
                "test",
                "Verify bounded recovery",
                "Create and run focused regression tests for the recovery adapter.",
                "testing",
                ["code"],
            ),
            (
                "recovery-api-node-review",
                "review",
                "Review bounded recovery",
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


def _terminal_recovery_failures() -> list[dict[str, Any]]:
    call_profile = [
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
    call_profile.extend(
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
    signal = build_model_recovery_signal(
        terminal_reason="schema_validation_failed",
        llm_call_profile=call_profile,
    )
    return [
        {
            "failure_type": "invalid_proposal",
            "model_recovery_signal": signal,
        }
    ]


def test_visual_dry_run_exposes_effective_recovery_and_candidate_plan(
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    from agent.services.model_invocation_service import ModelInvocationService
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import (
        ModelProfileResolver,
        RoutingRules,
    )

    phi = ModelProfile(
        profile_id="local_ollama_phi4_mini",
        provider_id="ollama",
        model="ananta-phi4-mini-32k",
        local=True,
        block_secret_context=False,
        supports_json=True,
        tool_calling_mode="prompt_json",
        fallback_group="local_phi_to_gemma_reasoning",
        fallback_rank=10,
    )
    gemma = ModelProfile(
        profile_id="local_ollama_gemma4_e4b_reasoning",
        provider_id="ollama",
        model="ananta-gemma4-reasoning-8k",
        model_role="reasoning",
        local=True,
        block_secret_context=False,
        supports_json=True,
        tool_calling_mode="prompt_json",
        fallback_group="local_phi_to_gemma_reasoning",
        fallback_rank=20,
    )
    resolver = ModelProfileResolver(
        [phi, gemma],
        routing_rules=RoutingRules.from_dict(
            {
                "fallback_groups": {
                    "local_phi_to_gemma_reasoning": {
                        "ordered_profiles": [
                            phi.profile_id,
                            gemma.profile_id,
                        ]
                    }
                }
            }
        ),
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    inherited_strategies = [
        "compact_context",
        "segment_planning",
        "propose_task_plan",
        "require_approval",
        "stop",
    ]
    graph = {
        "id": "visual-recovery-dry-run",
        "name": "Visual recovery dry-run",
        "metadata": {
            "model_routing": {
                "preferred_profile_id": phi.profile_id,
                "fallback_group_id": "local_phi_to_gemma_reasoning",
                "context_recovery_strategies": inherited_strategies,
                "require_approval_for_generated_plan": True,
            }
        },
        "steps": [
            {
                "id": "inherit",
                "label": "Inherit recovery",
                "kind": "analysis",
            },
            {
                "id": "stop-only",
                "label": "Stop locally",
                "kind": "coding",
                "metadata": {
                    "model_routing": {
                        "context_recovery_strategies": ["stop"],
                    }
                },
            },
        ],
        "edges": [],
    }

    response = client.post(
        "/api/visual-process/dry-run",
        headers=admin_auth_header,
        json=graph,
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["validation"]["valid"] is True
    assert payload["model_routing_summary"]["status"] == "ready"
    plans = {
        item["step_id"]: item
        for item in payload["per_step_model_plan"]
    }
    expected_candidates = [phi.profile_id, gemma.profile_id]
    assert plans["inherit"]["context_recovery_strategies"] == (
        inherited_strategies
    )
    assert plans["stop-only"]["context_recovery_strategies"] == ["stop"]
    assert plans["inherit"]["candidate_chain"] == expected_candidates
    assert plans["stop-only"]["candidate_chain"] == expected_candidates
    assert plans["inherit"]["selected_profile_id"] == phi.profile_id
    blueprint_steps = {
        item["step_id"]: item
        for item in payload["blueprint"]["workflow"]["steps"]
    }
    assert blueprint_steps["inherit"]["model_routing"][
        "context_recovery_strategies"
    ] == inherited_strategies
    assert blueprint_steps["stop-only"]["model_routing"][
        "context_recovery_strategies"
    ] == ["stop"]


def test_invalid_graph_step_recovery_merge_fails_closed_without_model_plan(
    client,
    admin_auth_header,
) -> None:
    graph = {
        "id": "visual-recovery-invalid-merge",
        "name": "Invalid recovery merge",
        "metadata": {
            "model_routing": {
                "context_recovery_strategies": [
                    "propose_task_plan",
                    "require_approval",
                    "stop",
                ],
                "require_approval_for_generated_plan": True,
            }
        },
        "steps": [
            {
                "id": "unsafe-override",
                "label": "Unsafe override",
                "kind": "coding",
                "metadata": {
                    "model_routing": {
                        "require_approval_for_generated_plan": False,
                    }
                },
            }
        ],
        "edges": [],
    }

    validation_response = client.post(
        "/api/visual-process/model-routing/validate",
        headers=admin_auth_header,
        json=graph,
    )
    dry_run_response = client.post(
        "/api/visual-process/dry-run",
        headers=admin_auth_header,
        json=graph,
    )

    assert validation_response.status_code == 422
    validation_payload = validation_response.get_json()
    assert validation_payload["validation"]["valid"] is False
    assert validation_payload["per_step_model_plan"] == []
    assert validation_payload["model_routing_summary"]["status"] == "invalid"
    assert any(
        issue["code"] == "model_routing_invalid"
        and issue["step_id"] == "unsafe-override"
        and "after graph/step merge" in issue["message"]
        for issue in validation_payload["validation"]["issues"]
    )

    # The general dry-run endpoint remains a diagnostics response (HTTP 200),
    # but it must never compile a blueprint or candidate plan from the unsafe
    # merge.
    assert dry_run_response.status_code == 200
    dry_run_payload = dry_run_response.get_json()
    assert dry_run_payload["validation"]["valid"] is False
    assert dry_run_payload["blueprint"] is None
    assert dry_run_payload["per_step_model_plan"] == []
    assert dry_run_payload["model_routing_summary"]["status"] == "invalid"


def test_recovery_plan_http_patch_refreshes_digest_bound_admin_approval(
    client,
    app,
    monkeypatch,
) -> None:
    import agent.services.task_recovery_planning_service as recovery_module
    from agent.services.approval_request_service import (
        get_approval_request_service,
    )
    from agent.services.repository_registry import get_repository_registry

    same_team_headers = _team_auth_headers("team-recovery")
    same_team_admin_headers = _team_auth_headers(
        "team-recovery",
        role="admin",
    )
    other_team_headers = _team_auth_headers("team-other")

    with app.app_context():
        repositories = get_repository_registry(app)
        repositories.team_repo.save(
            TeamDB(
                id="team-recovery",
                name="Recovery API fixture team",
                is_active=True,
            )
        )
        goal = repositories.goal_repo.save(
            GoalDB(
                id="goal-recovery-api",
                trace_id="trace-recovery-api",
                goal="Review an edited recovery plan",
                status="in_progress",
                source="test",
                team_id="team-recovery",
                tenant_id="team-recovery",
            )
        )
        source_task = repositories.task_repo.save(
            TaskDB(
                id="task-recovery-api",
                title="Recover exhausted local model execution",
                description=(
                    "Create a bounded, approval-gated plan after local model "
                    "exhaustion."
                ),
                status="proposing",
                goal_id=goal.id,
                goal_trace_id=goal.trace_id,
                team_id=goal.team_id,
                tenant_id=goal.tenant_id,
                worker_execution_context={
                    "model_routing": {
                        "preferred_profile_id": "local_ollama_phi4_mini",
                        "fallback_group_id": (
                            "local_phi_to_gemma_reasoning"
                        ),
                        "context_recovery_strategies": [
                            "compact_context",
                            "segment_planning",
                            "propose_task_plan",
                            "require_approval",
                            "stop",
                        ],
                        "require_approval_for_generated_plan": True,
                    }
                },
            )
        )
        planner = _PersistedRecoveryPlanner(repositories)
        recovery_service = TaskRecoveryPlanningService(
            role_provider=lambda: "hub",
            repository_provider=lambda: repositories,
            planner_provider=lambda: planner,
        )
        monkeypatch.setattr(recovery_module, "_service", recovery_service)
        proposal = recovery_service.propose_after_model_exhaustion(
            task=source_task,
            strategy_failures=_terminal_recovery_failures(),
        )
        assert proposal["status"] == "pending_approval"
        plan_id = proposal["plan_id"]
        old_approval_id = proposal["approval_request_id"]
        old_digest = proposal["plan_digest"]
        node = repositories.plan_node_repo.get_by_plan_id(plan_id)[0]
        approval_service = get_approval_request_service()
        old_approval = approval_service.get_request(old_approval_id)
        assert old_approval is not None
        assert old_approval.tool_name == RECOVERY_MATERIALIZE_TOOL
        assert old_approval.status == "pending"
        assert old_approval.target_fingerprint == old_digest
        assert old_approval.canonical_arguments["team_id"] == (
            "team-recovery"
        )

    same_team_view = client.get(
        f"/goals/{goal.id}/plans/{plan_id}",
        headers=same_team_headers,
    )
    other_team_view = client.get(
        f"/goals/{goal.id}/plans/{plan_id}",
        headers=other_team_headers,
    )
    same_team_patch = client.patch(
        f"/goals/{goal.id}/plans/{plan_id}/nodes/{node.id}",
        headers=same_team_headers,
        json={"title": "Team member must not mutate this plan"},
    )
    same_team_grant = client.post(
        f"/api/approvals/{old_approval_id}/decision",
        headers=same_team_headers,
        json={"decision": "granted"},
    )

    assert same_team_view.status_code == 200
    assert other_team_view.status_code == 404
    assert same_team_patch.status_code == 403
    assert same_team_grant.status_code == 403

    patch_response = client.patch(
        f"/goals/{goal.id}/plans/{plan_id}/nodes/{node.id}",
        headers=same_team_admin_headers,
        json={
            "title": "Operator-reviewed bounded recovery",
            "priority": "High",
        },
    )
    assert patch_response.status_code == 200
    assert patch_response.get_json()["data"]["materialized_task_id"] is None

    with app.app_context():
        repositories = get_repository_registry(app)
        edited_plan = repositories.plan_repo.get_by_id(plan_id)
        edited_nodes = repositories.plan_node_repo.get_by_plan_id(plan_id)
        edited_digest = calculate_recovery_plan_digest(
            edited_plan,
            edited_nodes,
        )
        assert edited_digest != old_digest
        assert all(node.materialized_task_id is None for node in edited_nodes)

    stale_grant_response = client.post(
        f"/api/approvals/{old_approval_id}/decision",
        headers=same_team_admin_headers,
        json={
            "decision": "granted",
            "reason": "Review complete after the node edit",
        },
    )
    assert stale_grant_response.status_code == 200

    with app.app_context():
        repositories = get_repository_registry(app)
        approval_service = get_approval_request_service()
        refreshed_plan = repositories.plan_repo.get_by_id(plan_id)
        refreshed_nodes = repositories.plan_node_repo.get_by_plan_id(plan_id)
        refreshed_digest = calculate_recovery_plan_digest(
            refreshed_plan,
            refreshed_nodes,
        )
        new_approval_id = refreshed_plan.rationale["approval_request_id"]
        old_approval = approval_service.get_request(old_approval_id)
        new_approval = approval_service.get_request(new_approval_id)

        assert refreshed_digest == edited_digest
        assert new_approval_id != old_approval_id
        assert refreshed_plan.status == "pending_approval"
        assert refreshed_plan.rationale["plan_digest"] == refreshed_digest
        assert all(node.materialized_task_id is None for node in refreshed_nodes)
        assert old_approval is not None
        assert old_approval.status == "consumed"
        assert old_approval.target_fingerprint == old_digest
        assert new_approval is not None
        assert new_approval.status == "pending"
        assert new_approval.target_fingerprint == refreshed_digest
        assert new_approval.canonical_arguments["plan_digest"] == (
            refreshed_digest
        )
        assert new_approval.canonical_arguments["team_id"] == (
            "team-recovery"
        )
        source = repositories.task_repo.get_by_id(source_task.id)
        recovery_state = source.status_reason_details["model_recovery"]
        assert recovery_state["approval_request_id"] == new_approval_id

    stale_grant_reuse = client.post(
        f"/api/approvals/{old_approval_id}/decision",
        headers=same_team_admin_headers,
        json={"decision": "granted"},
    )
    assert stale_grant_reuse.status_code == 409
    assert stale_grant_reuse.get_json()["error"] == (
        "request_already_consumed"
    )

    same_team_approvals = client.get(
        f"/api/approvals?goal_id={goal.id}",
        headers=same_team_headers,
    )
    other_team_approvals = client.get(
        f"/api/approvals?goal_id={goal.id}",
        headers=other_team_headers,
    )
    assert same_team_approvals.status_code == 200
    assert {
        row["request_id"]
        for row in same_team_approvals.get_json()["requests"]
    } == {old_approval_id, new_approval_id}
    assert other_team_approvals.status_code == 200
    assert other_team_approvals.get_json()["requests"] == []
