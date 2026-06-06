"""APRL-019: Tests for active agent profile in Propose/Instruction-Stack pipeline.

Verifies:
- propose_task_step persists active_agent_profile in last_proposal metadata
- InstructionLayerService.assemble_for_task includes agent_profile_template in applied_layers
- User-Overlay cannot suppress the agent_profile_template layer
- bug_fix vs refactor produce different active profiles
- Tasks without profile/task_kind remain backward-compatible
"""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from worker.core.propose import ExecutableProposal, ProposeStrategyResult
from worker.core.propose_orchestrator import ProposeContext


# ---------------------------------------------------------------------------
# Helpers shared with other propose tests
# ---------------------------------------------------------------------------

def _mk_executable_result(strategy_id: str = "tool_calling_llm") -> ProposeStrategyResult:
    proposal = ExecutableProposal(
        proposal_id="p-aprl019",
        goal_id="g-aprl",
        task_id="T-APRL019",
        strategy_id=strategy_id,
        command="echo aprl",
        metadata={"provider": "ollama", "model": "test"},
    )
    return ProposeStrategyResult.executable(strategy_id, proposal)


# ---------------------------------------------------------------------------
# APRL-019 T1: propose_task_step persists active_agent_profile in last_proposal
# ---------------------------------------------------------------------------

def test_propose_persists_active_agent_profile_in_last_proposal(client, app, admin_auth_header):
    from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

    tid = "T-APRL019-PROFILE-PERSIST"
    with app.app_context():
        _update_local_task_status(
            tid,
            "assigned",
            goal_id="g-aprl-persist",
            description="fix the bug",
            task_kind="bug_fix",
        )

    with patch(
        "worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run",
        return_value=_mk_executable_result(),
    ):
        res = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "fix the bug"},
            headers=admin_auth_header,
        )

    assert res.status_code == 200
    with app.app_context():
        task = _get_local_task_status(tid)
        psmeta = ((task or {}).get("last_proposal") or {}).get("routing", {}).get("propose_strategy_meta") or {}
        profile = psmeta.get("active_agent_profile")
        assert profile is not None, "active_agent_profile must be in last_proposal propose_strategy_meta"
        assert "profile_id" in profile


def test_propose_active_agent_profile_carries_through_context(client, app, admin_auth_header):
    """ProposeContext.active_agent_profile is populated when orchestrator runs."""
    from agent.routes.tasks.utils import _update_local_task_status

    tid = "T-APRL019-CTX"
    with app.app_context():
        _update_local_task_status(
            tid, "assigned", goal_id="g-aprl-ctx", description="refactor service", task_kind="refactor"
        )

    captured: dict = {}

    def _capturing_run(ctx):
        captured["active_agent_profile"] = ctx.active_agent_profile
        return _mk_executable_result()

    with patch(
        "worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run",
        side_effect=_capturing_run,
    ):
        client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "refactor"},
            headers=admin_auth_header,
        )

    profile = captured.get("active_agent_profile")
    assert profile is not None, "ProposeContext.active_agent_profile must be set"
    assert profile.get("profile_id") is not None


# ---------------------------------------------------------------------------
# APRL-019 T2: InstructionLayerService has agent_profile_template in applied_layers
# ---------------------------------------------------------------------------

def test_instruction_layer_service_has_agent_profile_template_layer(app):
    from agent.services.instruction_layer_service import get_instruction_layer_service

    task = {
        "id": "t-aprl-layer",
        "goal_id": "g-aprl-layer",
        "task_kind": "bug_fix",
        "description": "fix the bug",
    }
    with app.app_context():
        result = get_instruction_layer_service().assemble_for_task(
            task=task,
            base_prompt="fix the bug",
            system_prompt="system",
        )

    diagnostics = result.get("diagnostics") or {}
    applied = [layer["layer"] for layer in list(diagnostics.get("applied_layers") or [])]
    assert "agent_profile_template" in applied, (
        "agent_profile_template must appear in applied_layers"
    )

    # active_agent_profile must be in diagnostics
    assert "active_agent_profile" in diagnostics
    ap = diagnostics["active_agent_profile"]
    assert ap.get("profile_id") is not None


def test_instruction_layer_service_agent_profile_template_precedes_blueprint_template(app):
    from agent.services.instruction_layer_service import get_instruction_layer_service

    task = {"id": "t-aprl-order", "task_kind": "refactor", "description": "refactor"}
    with app.app_context():
        result = get_instruction_layer_service().assemble_for_task(
            task=task, base_prompt="refactor", system_prompt="sys"
        )

    applied = [l["layer"] for l in list((result.get("diagnostics") or {}).get("applied_layers") or [])]
    if "agent_profile_template" in applied and "blueprint_template" in applied:
        assert applied.index("agent_profile_template") < applied.index("blueprint_template"), (
            "agent_profile_template must precede blueprint_template"
        )


# ---------------------------------------------------------------------------
# APRL-019 T3: User-Overlay cannot suppress agent_profile_template layer
# ---------------------------------------------------------------------------

def test_user_overlay_cannot_suppress_agent_profile_template(app):
    """Even when a user overlay is present, agent_profile_template must remain applied."""
    import time
    from agent.db_models import InstructionOverlayDB
    from agent.services.instruction_layer_service import get_instruction_layer_service
    from agent.services.repository_registry import get_repository_registry

    with app.app_context():
        repos = get_repository_registry()
        # Create a user overlay
        overlay = InstructionOverlayDB(
            id="overlay-aprl-019",
            owner_username="testuser-aprl",
            name="test overlay",
            prompt_content="Use a very concise style.",
            overlay_metadata={"preferences": {"style": "concise"}},
            scope="task",
            attachment_kind="task",
            attachment_id="t-aprl-overlay",
            is_active=True,
            created_at=time.time(),
            updated_at=time.time(),
        )
        repos.instruction_overlay_repo.save(overlay)

        task = {
            "id": "t-aprl-overlay",
            "task_kind": "bug_fix",
            "description": "fix a bug",
            "worker_execution_context": {
                "instruction_context": {
                    "owner_username": "testuser-aprl",
                    "overlay_id": "overlay-aprl-019",
                }
            },
        }
        result = get_instruction_layer_service().assemble_for_task(
            task=task,
            base_prompt="fix a bug",
            system_prompt="sys",
        )

    diagnostics = result.get("diagnostics") or {}
    applied = [l["layer"] for l in list(diagnostics.get("applied_layers") or [])]
    assert "agent_profile_template" in applied, (
        "agent_profile_template must not be suppressable by user overlay"
    )


# ---------------------------------------------------------------------------
# APRL-019 T4: bug_fix and refactor produce different active profiles
# ---------------------------------------------------------------------------

def test_bug_fix_and_refactor_produce_different_active_profiles(client, app, admin_auth_header):
    from agent.routes.tasks.utils import _update_local_task_status

    captured_profiles: dict[str, dict] = {}

    def _capturing_run(ctx):
        captured_profiles[ctx.task.get("task_kind", "unknown")] = dict(ctx.active_agent_profile or {})
        return _mk_executable_result()

    for task_kind in ("bug_fix", "refactor"):
        tid = f"T-APRL019-DIFF-{task_kind.upper()}"
        with app.app_context():
            _update_local_task_status(
                tid, "assigned", goal_id=f"g-aprl-diff-{task_kind}", description="task", task_kind=task_kind
            )
        with patch(
            "worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run",
            side_effect=_capturing_run,
        ):
            client.post(
                f"/tasks/{tid}/step/propose",
                json={"prompt": "task"},
                headers=admin_auth_header,
            )

    bug_profile = captured_profiles.get("bug_fix", {})
    refactor_profile = captured_profiles.get("refactor", {})

    assert bug_profile.get("profile_id") is not None
    assert refactor_profile.get("profile_id") is not None
    assert bug_profile.get("profile_id") != refactor_profile.get("profile_id"), (
        "bug_fix and refactor must activate different agent profiles"
    )


# ---------------------------------------------------------------------------
# APRL-019 T5: Old task without task_kind / profile stays backward-compatible
# ---------------------------------------------------------------------------

def test_propose_backward_compatible_task_without_task_kind(client, app, admin_auth_header):
    """A task with no task_kind or profile still proposes without error."""
    from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

    tid = "T-APRL019-COMPAT"
    with app.app_context():
        _update_local_task_status(
            tid, "assigned", goal_id="g-aprl-compat", description="generic task"
        )

    with patch(
        "worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run",
        return_value=_mk_executable_result(),
    ):
        res = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "do something"},
            headers=admin_auth_header,
        )

    assert res.status_code == 200
    with app.app_context():
        task = _get_local_task_status(tid)
        psmeta = ((task or {}).get("last_proposal") or {}).get("routing", {}).get("propose_strategy_meta") or {}
        # active_agent_profile is set even for fallback (root_only)
        profile = psmeta.get("active_agent_profile")
        assert profile is not None
        # No crash; profile_id may be "root_only" for tasks without task_kind
        assert profile.get("profile_id") is not None


# ---------------------------------------------------------------------------
# APRL-019 T6: Instruction stack contains agent_profile section in rendered prompt
# ---------------------------------------------------------------------------

def test_rendered_system_prompt_contains_agent_profile_section(app):
    from agent.services.instruction_layer_service import get_instruction_layer_service

    task = {"id": "t-aprl-render", "task_kind": "bug_fix", "description": "fix"}
    with app.app_context():
        result = get_instruction_layer_service().assemble_for_task(
            task=task,
            base_prompt="fix",
            system_prompt="governance",
        )

    rendered = result.get("rendered_system_prompt") or ""
    assert "AGENT PROFILE" in rendered, (
        "rendered_system_prompt must include [AGENT PROFILE: ...] section"
    )


# ---------------------------------------------------------------------------
# APRL-019 T7: layer_model reports agent_profile_template as non-overridable
# ---------------------------------------------------------------------------

def test_layer_model_includes_agent_profile_template_as_non_overridable():
    from agent.services.instruction_layer_service import get_instruction_layer_service

    model = get_instruction_layer_service().layer_model()
    layers = {l["id"]: l for l in model["layers"]}
    assert "agent_profile_template" in layers, "layer_model must declare agent_profile_template"
    assert layers["agent_profile_template"]["overridable"] is False
    assert "agent_profile_template" in model["precedence"]
