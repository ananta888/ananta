from unittest.mock import patch

from flask import g

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
