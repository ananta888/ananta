from unittest.mock import patch

from flask import g
import pytest

from worker.core.propose import ExecutableProposal, ProposeStrategyResult
from agent.services.model_recovery_signal import build_model_recovery_signal


def _mk_result(*, with_profile: bool) -> ProposeStrategyResult:
    metadata = {
        "provider": "ollama",
        "model": "qwen2.5",
    }
    if with_profile:
        metadata["llm_call_profile"] = [
            {
                "name": "chat_completions",
                "backend": "llm_api",
                "provider": "ollama",
                "model": "qwen2.5",
                "success": True,
                "latency_ms": 777,
                "prompt_tokens": 42,
                "completion_tokens": 7,
                "total_tokens": 49,
                "source": "model_invocation_service",
                "estimated": False,
                "error_type": None,
                "error_message": None,
                "started_at": 1.0,
                "ended_at": 2.0,
            }
        ]
    proposal = ExecutableProposal(
        proposal_id="p-1",
        goal_id="g-1",
        task_id="T-PROFILE",
        strategy_id="tool_calling_llm",
        command="echo ok",
        metadata=metadata,
    )
    result = ProposeStrategyResult.executable("tool_calling_llm", proposal)
    if with_profile:
        result.metadata["llm_call_profile"] = list(metadata["llm_call_profile"])
    return result


def _mk_declined_result_with_profile() -> ProposeStrategyResult:
    result = ProposeStrategyResult.declined("json_schema_llm", reason="llm_returned_no_executable_output")
    result.metadata["llm_call_profile"] = [
        {
            "name": "chat_completions",
            "backend": "llm_api",
            "provider": "ollama",
            "model": "qwen2.5",
            "success": True,
            "latency_ms": 321,
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "total_tokens": 15,
            "source": "model_invocation_service",
            "estimated": False,
            "error_type": None,
            "error_message": None,
            "started_at": 1.0,
            "ended_at": 2.0,
        }
    ]
    return result


def _mk_exhausted_result() -> ProposeStrategyResult:
    profile = [
        {
            "name": "chat_completions",
            "backend": "llm_api",
            "profile_id": "gemma",
            "provider": "ollama",
            "model": "gemma4",
            "success": False,
            "error_type": "schema_validation_failed",
        }
    ]
    signal = build_model_recovery_signal(
        terminal_reason="schema_validation_failed",
        llm_call_profile=profile,
        fallback_decisions=[
            {
                "reason": "candidate_chain_exhausted",
                "previous_profile_id": "gemma",
                "next_profile_id": None,
                "trigger": "schema_validation_failed",
                "terminal": True,
            }
        ],
    )
    return ProposeStrategyResult.needs_review(
        "orchestrator",
        reason="llm_required_but_unavailable",
        metadata={
            "llm_call_profile": profile,
            "model_recovery_signal": signal,
            "fallback_decisions": list(signal["fallback_decisions"]),
        },
    )


def _mk_mixed_terminal_denial_result(
    terminal_reason: str,
) -> ProposeStrategyResult:
    profile = [
        {
            "name": "chat_completions",
            "backend": "llm_api",
            "profile_id": "phi",
            "provider": "ollama",
            "model": "phi4-mini",
            "success": False,
            "error_type": "timeout",
        },
        {
            "name": "chat_completions",
            "backend": "provider_middleware",
            "profile_id": "phi",
            "provider": "ollama",
            "model": "phi4-mini",
            "success": False,
            "error_type": terminal_reason,
        },
    ]
    decisions = [
        {
            "reason": "provider invocation denied",
            "previous_profile_id": "phi",
            "next_profile_id": None,
            "trigger": terminal_reason,
            "terminal": True,
        }
    ]
    signal = build_model_recovery_signal(
        terminal_reason=terminal_reason,
        llm_call_profile=profile,
        fallback_decisions=decisions,
    )
    return ProposeStrategyResult.needs_review(
        "orchestrator",
        reason="llm_required_but_unavailable",
        metadata={
            "llm_call_profile": profile,
            "model_recovery_signal": signal,
            "fallback_decisions": decisions,
        },
    )


def test_propose_persists_real_llm_profile_when_available(client, app, admin_auth_header):
    from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

    tid = "T-PROFILE-REAL"
    with app.app_context():
        _update_local_task_status(tid, "assigned", goal_id="g-1", description="test")

    with patch("worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run", return_value=_mk_result(with_profile=True)):
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "hello"}, headers=admin_auth_header)

    assert res.status_code == 200
    with app.app_context():
        task = _get_local_task_status(tid)
        cli_result = ((task or {}).get("last_proposal") or {}).get("cli_result") or {}
        profile = list(cli_result.get("llm_call_profile") or [])
        assert profile
        assert profile[0]["source"] == "model_invocation_service"
        assert profile[0]["estimated"] is False
        assert profile[0]["latency_ms"] == 777


def test_propose_omits_synthetic_profile_when_real_profile_missing_by_default(client, app, admin_auth_header):
    from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

    tid = "T-PROFILE-SYN"
    with app.app_context():
        _update_local_task_status(tid, "assigned", goal_id="g-2", description="test")

    with patch("worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run", return_value=_mk_result(with_profile=False)):
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "hello"}, headers=admin_auth_header)

    assert res.status_code == 200
    with app.app_context():
        task = _get_local_task_status(tid)
        cli_result = ((task or {}).get("last_proposal") or {}).get("cli_result") or {}
        profile = list(cli_result.get("llm_call_profile") or [])
        assert profile == []


def test_propose_persists_synthetic_profile_when_explicitly_enabled(client, app, admin_auth_header):
    from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

    tid = "T-PROFILE-SYN-ENABLED"
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        llm_policy = dict(cfg.get("llm_profile_policy") or {})
        llm_policy["allow_synthetic_fallback"] = True
        cfg["llm_profile_policy"] = llm_policy
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(tid, "assigned", goal_id="g-3", description="test")

    with patch("worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run", return_value=_mk_result(with_profile=False)):
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "hello"}, headers=admin_auth_header)

    assert res.status_code == 200
    with app.app_context():
        task = _get_local_task_status(tid)
        cli_result = ((task or {}).get("last_proposal") or {}).get("cli_result") or {}
        profile = list(cli_result.get("llm_call_profile") or [])
        assert profile
        assert profile[0]["source"] == "orchestrator_synthetic"
        assert profile[0]["estimated"] is True


def test_propose_persists_real_profile_for_declined_result(client, app, admin_auth_header):
    from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

    tid = "T-PROFILE-DECLINED-REAL"
    with app.app_context():
        _update_local_task_status(tid, "assigned", goal_id="g-4", description="test")

    with patch("worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run", return_value=_mk_declined_result_with_profile()):
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "hello"}, headers=admin_auth_header)

    assert res.status_code == 200
    with app.app_context():
        task = _get_local_task_status(tid)
        cli_result = ((task or {}).get("last_proposal") or {}).get("cli_result") or {}
        profile = list(cli_result.get("llm_call_profile") or [])
        assert profile
        assert profile[0]["source"] == "model_invocation_service"
        assert profile[0]["estimated"] is False


def test_configured_hub_recovery_prevents_unconfigured_cli_fallback(
    client, app, admin_auth_header
):
    from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

    tid = "T-MODEL-RECOVERY-DEFER"
    with app.app_context():
        _update_local_task_status(
            tid,
            "assigned",
            goal_id="g-recovery",
            description="test",
            worker_execution_context={
                "model_routing": {
                    "preferred_profile_id": "phi",
                    "fallback_group_id": "local",
                    "context_recovery_strategies": [
                        "compact_context",
                        "propose_task_plan",
                        "require_approval",
                        "stop",
                    ],
                }
            },
        )

    with (
        patch(
            "worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run",
            return_value=_mk_exhausted_result(),
        ),
        patch(
            "agent.services.task_scoped_execution_service.TaskScopedExecutionService._invoke_cli_runner"
        ) as cli_fallback,
    ):
        res = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "hello"},
            headers=admin_auth_header,
        )

    assert res.status_code == 200
    cli_fallback.assert_not_called()
    response_data = (res.get_json() or {}).get("data") or {}
    assert response_data["metadata"]["model_recovery_signal"]["schema"] == "model_recovery_signal.v1"
    assert response_data["propose_strategy_meta"]["hub_recovery_deferred"] is True
    with app.app_context():
        task = _get_local_task_status(tid)
        persisted_meta = (
            (((task or {}).get("last_proposal") or {}).get("routing") or {}).get("propose_strategy_meta")
            or {}
        )
        assert persisted_meta["model_recovery_signal"]["state"] == "exhausted"


@pytest.mark.parametrize(
    "terminal_reason",
    (
        "provider_attempt_plan_sequence_denied",
        "provider_endpoint_binding_mismatch",
        "unknown_provider_denial",
    ),
)
def test_terminal_provider_denial_never_uses_cli_or_hub_recovery(
    client,
    app,
    admin_auth_header,
    terminal_reason,
):
    from agent.routes.tasks.utils import _update_local_task_status

    tid = f"T-MODEL-DENIAL-{terminal_reason}"
    with app.app_context():
        _update_local_task_status(
            tid,
            "assigned",
            goal_id="g-denial",
            description="test",
            worker_execution_context={
                "model_routing": {
                    "preferred_profile_id": "phi",
                    "fallback_group_id": "local",
                    "context_recovery_strategies": [
                        "compact_context",
                        "propose_task_plan",
                        "require_approval",
                        "stop",
                    ],
                }
            },
        )

    with (
        patch(
            "worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run",
            return_value=_mk_mixed_terminal_denial_result(
                terminal_reason
            ),
        ),
        patch(
            "agent.services.task_scoped_execution_service."
            "TaskScopedExecutionService._invoke_cli_runner"
        ) as cli_fallback,
    ):
        res = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "hello"},
            headers=admin_auth_header,
        )

    assert res.status_code == 200
    cli_fallback.assert_not_called()
    response_data = (res.get_json() or {}).get("data") or {}
    assert "model_recovery_signal" not in (
        response_data.get("metadata") or {}
    )
    assert (
        response_data["propose_strategy_meta"].get(
            "hub_recovery_deferred"
        )
        is not True
    )


# CPR-003: runtime_selection is consumed and visible in last_proposal
def test_propose_runtime_selection_visible_in_last_proposal(client, app, admin_auth_header):
    from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

    tid = "T-RUNTIME-SEL-1"
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        app.config["AGENT_CONFIG"] = {
            **cfg,
            "default_provider": "ollama",
            "default_model": "ananta-default:latest",
            "sgpt_routing": {"task_kind_backend": {"*": "ananta-worker"}},
        }
        _update_local_task_status(tid, "assigned", goal_id="g-cpr3", description="test")

    with patch("worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run", return_value=_mk_result(with_profile=True)):
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "hello"}, headers=admin_auth_header)

    assert res.status_code == 200
    with app.app_context():
        task = _get_local_task_status(tid)
        routing = ((task or {}).get("last_proposal") or {}).get("routing") or {}
        psmeta = routing.get("propose_strategy_meta") or {}
        runtime_sel = psmeta.get("runtime_selection") or {}
        assert runtime_sel.get("provider") == "ollama"
        assert runtime_sel.get("model") == "ananta-default:latest"
        assert runtime_sel.get("backend") == "ananta-worker"


def test_propose_runtime_selection_visible_for_opencode_profile(client, app, admin_auth_header):
    from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

    tid = "T-RUNTIME-SEL-2"
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        app.config["AGENT_CONFIG"] = {
            **cfg,
            "default_provider": "ollama",
            "default_model": "ananta-default:latest",
            "sgpt_routing": {"task_kind_backend": {"*": "opencode"}},
        }
        _update_local_task_status(tid, "assigned", goal_id="g-cpr3-oc", description="test")

    with patch("worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run", return_value=_mk_result(with_profile=True)):
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "hello"}, headers=admin_auth_header)

    assert res.status_code == 200
    with app.app_context():
        task = _get_local_task_status(tid)
        routing = ((task or {}).get("last_proposal") or {}).get("routing") or {}
        psmeta = routing.get("propose_strategy_meta") or {}
        runtime_sel = psmeta.get("runtime_selection") or {}
        assert runtime_sel.get("backend") == "opencode"


def test_propose_passes_effective_config_to_orchestrator(client, app, admin_auth_header):
    """Verifies effective_config is passed to the ProposeContext (consumed, not just persisted)."""
    from agent.routes.tasks.utils import _update_local_task_status

    tid = "T-RUNTIME-SEL-3"
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        app.config["AGENT_CONFIG"] = {
            **cfg,
            "default_provider": "ollama",
            "default_model": "qwen2.5-coder:7b",
        }
        _update_local_task_status(tid, "assigned", goal_id="g-cpr3-consumed", description="test")

    captured = {}

    def _capturing_run(context):
        captured["effective_config"] = dict(context.effective_config or {})
        return _mk_result(with_profile=False)

    with patch("worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run", side_effect=_capturing_run):
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "hello"}, headers=admin_auth_header)

    assert res.status_code == 200
    assert captured.get("effective_config", {}).get("default_provider") == "ollama"
    assert captured.get("effective_config", {}).get("default_model") == "qwen2.5-coder:7b"


def test_propose_sets_and_restores_llm_trace_request_context(client, app, admin_auth_header):
    from agent.routes.tasks.utils import _update_local_task_status

    tid = "T-PROFILE-CTX"
    with app.app_context():
        _update_local_task_status(tid, "assigned", goal_id="g-ctx", description="test")

    captured = {}

    def _capturing_run(_context):
        captured["during_goal"] = getattr(g, "llm_goal_id", None)
        captured["during_task"] = getattr(g, "llm_task_id", None)
        return _mk_result(with_profile=False)

    with patch("worker.core.propose_orchestrator.ProposeStrategyOrchestrator.run", side_effect=_capturing_run):
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "hello"}, headers=admin_auth_header)

    assert res.status_code == 200
    assert captured["during_goal"] == "g-ctx"
    assert captured["during_task"] == tid
