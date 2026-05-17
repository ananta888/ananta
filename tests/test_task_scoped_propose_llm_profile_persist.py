from unittest.mock import patch

from worker.core.propose import ExecutableProposal, ProposeStrategyResult


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
