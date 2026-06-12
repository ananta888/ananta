import json
import os
import types
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_snake_chat_background_threads(monkeypatch):
    monkeypatch.setattr("agent.routes.snakes._spawn_ai_chat_reply", lambda **kwargs: None)


@pytest.fixture(autouse=True)
def _disable_llm_context_compaction(monkeypatch):
    class _NoopCompactor:
        def compact(self, **kwargs):
            return types.SimpleNamespace(payload={}, meta={"status": "disabled"})

    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.get_planning_context_compactor_service",
        lambda: _NoopCompactor(),
    )


@pytest.fixture(autouse=True)
def _enable_legacy_cli_step_path(app):
    cfg = dict(app.config.get("AGENT_CONFIG") or {})
    task_scoped_execution = dict(cfg.get("task_scoped_execution") or {})
    task_scoped_execution["allow_legacy_single_step_path"] = True
    cfg["task_scoped_execution"] = task_scoped_execution
    app.config["AGENT_CONFIG"] = cfg


@pytest.fixture
def force_hub_role(monkeypatch):
    monkeypatch.setattr("agent.config.settings.role", "hub")



# Split from tests/test_task_endpoints.py to keep source files below 1000 lines.

def test_task_execute_auto_records_llm_benchmark(client, app, tmp_path, admin_auth_header):
    tid = "T-BENCH-AUTO"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        app.config["DATA_DIR"] = str(tmp_path)
        _update_local_task_status(tid, "assigned", description="Implement feature X")

    with patch("agent.routes.tasks.execution.run_llm_cli_command") as mock_cli:
        mock_cli.return_value = (0, '{"reason":"go","command":"echo ok"}', "", "aider")
        propose_res = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "implement endpoint", "model": "gpt-4o-mini"},
            headers=admin_auth_header,
        )
        assert propose_res.status_code == 200

    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        mock_exec.return_value = ("ok", 0)
        execute_res = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)
        assert execute_res.status_code == 200
        assert execute_res.json["data"]["status"] == "completed"

    bench_path = os.path.join(str(tmp_path), "llm_model_benchmarks.json")
    assert os.path.exists(bench_path)
    with open(bench_path, "r", encoding="utf-8") as fh:
        db = json.load(fh)

    model_entry = (db.get("models") or {}).get("aider:gpt-4o-mini")
    assert model_entry is not None
    coding_bucket = (model_entry.get("task_kinds") or {}).get("coding") or {}
    assert int(coding_bucket.get("total") or 0) >= 1


def test_task_execute_benchmark_fallback_uses_config_defaults(client, app, tmp_path, admin_auth_header):
    tid = "T-BENCH-FALLBACK"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        app.config["DATA_DIR"] = str(tmp_path)
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["default_provider"] = "lmstudio"
        cfg["default_model"] = "model-fallback"
        cfg["llm_config"] = {"provider": "lmstudio", "model": "model-fallback"}
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(
            tid,
            "proposing",
            description="Document architecture",
            last_proposal={"command": "echo ok", "reason": "legacy proposal without model/backend"},
        )

    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        mock_exec.return_value = ("ok", 0)
        execute_res = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)
        assert execute_res.status_code == 200
        assert execute_res.json["data"]["status"] == "completed"

    bench_path = os.path.join(str(tmp_path), "llm_model_benchmarks.json")
    with open(bench_path, "r", encoding="utf-8") as fh:
        db = json.load(fh)
    model_entry = (db.get("models") or {}).get("lmstudio:model-fallback")
    assert model_entry is not None


def test_task_execute_benchmark_precedence_can_prefer_defaults_over_llm_config(client, app, tmp_path, admin_auth_header):
    tid = "T-BENCH-PRECEDENCE"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        app.config["DATA_DIR"] = str(tmp_path)
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["default_provider"] = "lmstudio"
        cfg["default_model"] = "model-default-preferred"
        cfg["llm_config"] = {"provider": "ollama", "model": "llama3"}
        cfg["benchmark_identity_precedence"] = {
            "provider_order": ["default_provider", "llm_config_provider", "proposal_backend"],
            "model_order": ["default_model", "llm_config_model", "proposal_model"],
        }
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(
            tid,
            "proposing",
            description="Write docs",
            last_proposal={"command": "echo ok", "reason": "legacy proposal"},
        )

    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        mock_exec.return_value = ("ok", 0)
        execute_res = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)
        assert execute_res.status_code == 200
        assert execute_res.json["data"]["status"] == "completed"

    bench_path = os.path.join(str(tmp_path), "llm_model_benchmarks.json")
    with open(bench_path, "r", encoding="utf-8") as fh:
        db = json.load(fh)
    model_entry = (db.get("models") or {}).get("lmstudio:model-default-preferred")
    assert model_entry is not None


def test_task_propose_respects_ops_routing_to_opencode(client, app, admin_auth_header):
    tid = "T-OPS-ROUTING"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["sgpt_routing"] = {
            "policy_version": "v2",
            "default_backend": "sgpt",
            "task_kind_backend": {"ops": "opencode"},
        }
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(tid, "assigned", description="Deploy service and restart kubernetes pods")

    with patch("agent.routes.tasks.execution.run_llm_cli_command") as mock_cli:
        mock_cli.return_value = (0, '{"reason":"ops","command":"kubectl rollout restart deploy/api"}', "", "opencode")
        response = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "deploy to kubernetes"}, headers=admin_auth_header)

    assert response.status_code == 200
    data = response.json["data"]
    assert data["backend"] == "opencode"
    assert (data.get("routing") or {}).get("effective_backend") == "opencode"
    assert (data.get("routing") or {}).get("task_kind") == "ops"


def test_task_propose_multi_provider_uses_cli_backends(client, app, admin_auth_header):
    tid = "T-MULTI-CLI-COMPARE"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(tid, "assigned", description="Implement API and write tests")

    calls = []

    def _fake_run_llm_cli_command(prompt, options, timeout, backend, model, routing_policy, session=None):
        calls.append({"backend": backend, "model": model, "routing_policy": routing_policy})
        if backend == "aider":
            return 0, '{"reason":"aider path","command":"pytest -q"}', "", "aider"
        if backend == "opencode":
            return 0, '{"reason":"opencode path","command":"echo ok"}', "", "opencode"
        return 1, "", "unsupported", backend

    with patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_run_llm_cli_command):
        response = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "implement endpoint", "providers": ["aider:gpt-4o-mini", "opencode:gpt-4.1-mini"]},
            headers=admin_auth_header,
        )

    assert response.status_code == 200
    data = response.json["data"]
    assert len(calls) == 2
    assert {c["backend"] for c in calls} == {"aider", "opencode"}
    assert data["backend"] in {"aider", "opencode"}
    assert isinstance(data.get("comparisons"), dict)
    assert "aider:gpt-4o-mini" in data["comparisons"]
    assert "opencode:gpt-4.1-mini" in data["comparisons"]
    assert (data["comparisons"]["aider:gpt-4o-mini"].get("routing") or {}).get("effective_backend") == "aider"
    assert (data["comparisons"]["opencode:gpt-4.1-mini"].get("routing") or {}).get("effective_backend") == "opencode"


def test_task_propose_accepts_stderr_json_as_fallback_output(client, app, admin_auth_header):
    tid = "T-PROPOSE-STDERR-FALLBACK"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(tid, "assigned", description="Implement API from stderr-only model output")

    stderr_json = '{"reason":"stderr fallback","command":"echo from-stderr"}'
    with patch("agent.routes.tasks.execution.run_llm_cli_command") as mock_cli:
        mock_cli.return_value = (1, "", stderr_json, "sgpt")
        response = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "implement endpoint"}, headers=admin_auth_header)

    assert response.status_code == 200
    data = response.json["data"]
    assert data["command"] == "echo from-stderr"
    assert data["reason"] == "stderr fallback"
    assert (data.get("cli_result") or {}).get("output_source") == "stderr"


def test_task_propose_multi_provider_uses_stderr_fallback_output(client, app, admin_auth_header):
    tid = "T-MULTI-STDERR-FALLBACK"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(tid, "assigned", description="Implement API and write tests")

    def _fake_run_llm_cli_command(prompt, options, timeout, backend, model, routing_policy, session=None):
        if backend == "aider":
            return 1, "", '{"reason":"stderr compare","command":"echo compare"}', "aider"
        return 1, "", "unsupported", backend

    with patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_run_llm_cli_command):
        response = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "implement endpoint", "providers": ["aider:gpt-4o-mini"]},
            headers=admin_auth_header,
        )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["command"] == "echo compare"
    assert data["reason"] == "stderr compare"
    assert (data.get("cli_result") or {}).get("output_source") == "stderr"
    assert isinstance(data.get("comparisons"), dict)
    assert data["comparisons"]["aider:gpt-4o-mini"]["cli_result"]["output_source"] == "stderr"


def test_task_propose_extracts_embedded_json_after_traceback_output(client, app, admin_auth_header):
    tid = "T-PROPOSE-TRACEBACK-JSON"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(tid, "assigned", description="Implement API from traceback-prefixed model output")

    traceback_json = (
        "Traceback (most recent call last):\n"
        "  File \"/tmp/runner.py\", line 1, in <module>\n"
        "ValueError: transient parse issue\n"
        '{"reason":"embedded json","command":"echo rescued"}'
    )
    with patch("agent.routes.tasks.execution.run_llm_cli_command") as mock_cli:
        mock_cli.return_value = (1, "", traceback_json, "sgpt")
        response = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "implement endpoint"}, headers=admin_auth_header)

    assert response.status_code == 200
    data = response.json["data"]
    assert data["command"] == "echo rescued"
    assert data["reason"] == "embedded json"
    assert (data.get("cli_result") or {}).get("output_source") == "stderr"
    assert (data.get("cli_result") or {}).get("repair_attempted") is False


def test_task_propose_repairs_invalid_output_with_followup_prompt(client, app, admin_auth_header):
    tid = "T-PROPOSE-REPAIR-FOLLOWUP"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["task_propose_repair_backend"] = "sgpt"
        cfg["task_propose_repair_model"] = "repair-model-x"
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(tid, "assigned", description="Implement endpoint robustly")

    calls = {"count": 0}

    def _fake_run_llm_cli_command(prompt, options, timeout, backend, model, routing_policy, research_context=None, session=None):
        calls["count"] += 1
        if calls["count"] == 1:
            assert model == "primary-model-a"
            return 1, "", "", "sgpt"
        if calls["count"] == 2:
            assert "Repariere die Antwort" in prompt
            assert "Validator/Fehlergrund:" in prompt
            # first repair attempt keeps the same model
            assert model == "primary-model-a"
            return 0, '{"reason":"still invalid","tool_calls":[]}', "", "sgpt"
        assert "Repariere die Antwort" in prompt
        assert model == "repair-model-x"
        return 0, '{"reason":"repaired","command":"echo repaired"}', "", "sgpt"

    with patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_run_llm_cli_command):
        response = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "implement endpoint", "model": "primary-model-a"},
            headers=admin_auth_header,
        )

    assert response.status_code == 200
    data = response.json["data"]
    assert calls["count"] == 3
    assert data["command"] == "echo repaired"
    assert data["reason"] == "repaired"
    cli_result = data.get("cli_result") or {}
    assert cli_result.get("repair_attempted") is True
    assert cli_result.get("repair_backend") == "sgpt"
    assert cli_result.get("repair_model") == "repair-model-x"


def test_task_propose_repairs_invalid_output_after_opencode_failure(client, app, admin_auth_header):
    tid = "T-PROPOSE-REPAIR-OPENCODE"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["sgpt_routing"] = {
            "policy_version": "v3",
            "default_backend": "opencode",
            "task_kind_backend": {"coding": "opencode"},
        }
        cfg["sgpt_execution_backend"] = "opencode"
        cfg["task_propose_repair_backend"] = "sgpt"
        cfg["task_propose_repair_model"] = "repair-model-x"
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(tid, "assigned", description="Implement endpoint robustly with opencode")

    calls = []

    def _fake_run_llm_cli_command(prompt, options, timeout, backend, model, routing_policy, research_context=None, session=None):
        calls.append({"backend": backend, "model": model, "prompt": prompt})
        if len(calls) == 1:
            assert backend == "opencode"
            assert model == "primary-model-a"
            return 1, "", "", "opencode"
        if len(calls) == 2:
            assert backend == "opencode"
            assert "Repariere die Antwort" in prompt
            assert model == "primary-model-a"
            return 0, '{"reason":"still invalid","tool_calls":[]}', "", "opencode"
        assert backend == "sgpt"
        assert "Repariere die Antwort" in prompt
        assert model == "repair-model-x"
        return 0, '{"reason":"repaired via fallback","command":"echo repaired"}', "", "sgpt"

    with patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_run_llm_cli_command):
        response = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "implement endpoint", "model": "primary-model-a"},
            headers=admin_auth_header,
        )

    assert response.status_code == 200
    data = response.json["data"]
    assert [entry["backend"] for entry in calls] == ["opencode", "opencode", "sgpt"]
    assert data["backend"] == "sgpt"
    assert data["command"] == "echo repaired"
    assert data["reason"] == "repaired via fallback"
    assert (data.get("routing") or {}).get("effective_backend") == "opencode"
    cli_result = data.get("cli_result") or {}
    assert cli_result.get("repair_attempted") is True
    assert cli_result.get("repair_backend") == "sgpt"
    assert cli_result.get("repair_model") == "repair-model-x"


def test_task_propose_uses_worker_execution_context_and_allowed_tools(client, app, admin_auth_header):
    tid = "T-WORKER-CONTEXT"
    captured = {}

    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            tid,
            "assigned",
            description="Implement endpoint using provided context",
            worker_execution_context={
                "context_bundle_id": "bundle-ctx-1",
                "context": {
                    "context_text": "Repository note: use the payments service adapter.",
                    "chunks": [{"id": "chunk-1"}],
                },
                "allowed_tools": ["allowed_tool"],
                "expected_output_schema": {"type": "object", "required": ["summary"]},
            },
        )

    def _fake_tool_defs(allowlist=None, denylist=None):
        if allowlist == ["allowed_tool"]:
            return [{"name": "allowed_tool", "description": "Allowed"}]
        return [
            {"name": "allowed_tool", "description": "Allowed"},
            {"name": "blocked_tool", "description": "Blocked"},
        ]

    def _fake_cli(prompt, options, timeout, backend, model, routing_policy, session=None):
        captured["prompt"] = prompt
        return 0, '{"reason":"ok","command":"echo done"}', "", "aider"

    with patch("agent.routes.tasks.execution.tool_registry.get_tool_definitions", side_effect=_fake_tool_defs):
        with patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_cli):
            response = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "implement it"}, headers=admin_auth_header)

    assert response.status_code == 200
    prompt = captured["prompt"]
    assert "Selektierter Hub-Kontext" in prompt
    assert "payments service adapter" in prompt
    assert "allowed_tool" in prompt
    assert "blocked_tool" not in prompt
    assert '"required"' in prompt
    assert response.json["data"]["worker_context"]["context_bundle_id"] == "bundle-ctx-1"


def test_task_propose_passes_temperature_to_cli_and_exposes_routing_field(client, app, admin_auth_header):
    tid = "T-PROPOSE-TEMP-1"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(tid, "assigned", description="Implement endpoint with deterministic JSON output")

    captured: dict = {}

    def _fake_cli(prompt, options, timeout, backend, model, routing_policy, temperature=None, research_context=None, session=None):
        captured["temperature"] = temperature
        return 0, '{"reason":"ok","command":"echo temp"}', "", backend

    with patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_cli):
        response = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "implement endpoint", "temperature": 0.35},
            headers=admin_auth_header,
        )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["command"] == "echo temp"
    assert abs(float(captured.get("temperature") or 0.0) - 0.35) < 0.0001
    routing = data.get("routing") or {}
    assert abs(float(routing.get("inference_temperature") or 0.0) - 0.35) < 0.0001


def test_task_propose_uses_dedicated_proposal_timeout(client, app, admin_auth_header):
    tid = "T-PROPOSE-TIMEOUT-1"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["command_timeout"] = 60
        cfg["task_propose_timeout_seconds"] = 300
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(tid, "assigned", description="Implement endpoint with opencode-friendly timeout")

    captured: dict = {}

    def _fake_cli(prompt, options, timeout, backend, model, routing_policy, temperature=None, research_context=None, session=None, workdir=None):
        captured["timeout"] = timeout
        return 0, '{"reason":"ok","command":"echo timeout"}', "", backend

    with patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_cli):
        response = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "implement endpoint"},
            headers=admin_auth_header,
        )

    assert response.status_code == 200
    assert response.json["data"]["command"] == "echo timeout"
    assert captured["timeout"] == 300


def test_task_propose_reuses_stateful_cli_session_when_enabled(client, app, admin_auth_header):
    tid = "T-STATEFUL-PROPOSE"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["sgpt_routing"] = {
            "policy_version": "v2",
            "default_backend": "sgpt",
            "task_kind_backend": {"ops": "opencode"},
        }
        cfg["cli_session_mode"] = {
            "enabled": True,
            "stateful_backends": ["opencode"],
            "max_turns_per_session": 8,
            "max_sessions": 100,
            "allow_task_scoped_auto_session": True,
        }
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(tid, "assigned", description="deploy and restart services")

    prompts = []

    def _fake_cli(prompt, options, timeout, backend, model, routing_policy, research_context=None, session=None):
        prompts.append(prompt)
        if len(prompts) == 1:
            return 0, '{"reason":"turn-1","command":"echo one"}', "", "opencode"
        return 0, '{"reason":"turn-2","command":"echo two"}', "", "opencode"

    with patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_cli):
        first = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "deploy now"}, headers=admin_auth_header)
        second = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "restart kubernetes pods and verify rollout"}, headers=admin_auth_header)

    assert first.status_code == 200
    assert second.status_code == 200
    first_routing = (first.json.get("data") or {}).get("routing") or {}
    second_routing = (second.json.get("data") or {}).get("routing") or {}
    assert first_routing.get("session_mode") == "stateful"
    assert second_routing.get("session_mode") == "stateful"
    assert first_routing.get("session_id")
    assert second_routing.get("session_id") == first_routing.get("session_id")
    assert first_routing.get("session_reused") is False
    assert second_routing.get("session_reused") is True
    assert len(prompts) == 2
    assert "Turn 1 Assistant" in prompts[1]
    assert '"reason":"turn-1"' in prompts[1]

    with app.app_context():
        task = _get_local_task_status(tid)
        cli_session_meta = ((task.get("verification_status") or {}).get("cli_session") or {})
        assert cli_session_meta.get("session_id") in {None, first_routing.get("session_id")}


def test_task_propose_creates_live_terminal_session_metadata_when_enabled(client, app, admin_auth_header):
    tid = "T-LIVE-TERMINAL-PROPOSE"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["sgpt_routing"] = {
            "policy_version": "v2",
            "default_backend": "opencode",
            "task_kind_backend": {"ops": "opencode"},
        }
        cfg["cli_session_mode"] = {"enabled": False, "stateful_backends": ["opencode"]}
        cfg["opencode_runtime"] = {"tool_mode": "full", "execution_mode": "live_terminal"}
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(tid, "assigned", description="restart services in shared terminal")

    captured_sessions: list[dict | None] = []

    def _fake_cli(prompt, options=None, timeout=None, backend=None, model=None, routing_policy=None, research_context=None, session=None, workdir=None, **kwargs):
        captured_sessions.append(session)
        return 0, '{"reason":"turn-live","command":"echo live"}', "", "opencode"

    terminal_service = MagicMock()
    terminal_service.ensure_session_for_cli.return_value = {
        "terminal_session_id": "cli-live-1",
        "forward_param": "cli-live-1",
        "agent_url": "http://worker-live:5000",
        "agent_name": "worker-live",
        "status": "active",
        "shell": "/bin/sh",
    }

    with (
        patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_cli),
        patch("agent.services.task_scoped_execution_service.get_live_terminal_session_service", return_value=terminal_service),
    ):
        response = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "restart now"}, headers=admin_auth_header)

    assert response.status_code == 200
    data = response.json["data"]
    routing = data.get("routing") or {}
    assert routing.get("session_mode") == "stateful"
    assert routing.get("execution_mode") == "live_terminal"
    assert (routing.get("live_terminal") or {}).get("forward_param") == "cli-live-1"
    assert captured_sessions
    assert captured_sessions[0] is not None
    assert ((captured_sessions[0] or {}).get("metadata") or {}).get("opencode_execution_mode") == "live_terminal"
    terminal_call = terminal_service.ensure_session_for_cli.call_args
    assert terminal_call.kwargs["workdir"]

    with app.app_context():
        from agent.services.worker_workspace_service import get_worker_workspace_service

        task = _get_local_task_status(tid)
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        assert terminal_call.kwargs["workdir"] == str(workspace_context.workspace_dir)
        verification = dict(task.get("verification_status") or {})
        cli_session_meta = verification.get("cli_session") or {}
        if cli_session_meta:
            assert cli_session_meta.get("execution_mode") == "live_terminal"
            assert cli_session_meta.get("forward_param") == "cli-live-1"
            assert cli_session_meta.get("agent_url") == "http://worker-live:5000"
        live_meta = verification.get("opencode_live_terminal") or {}
        if live_meta:
            assert live_meta.get("agent_url") == "http://worker-live:5000"
            assert live_meta.get("terminal_session_id") == "cli-live-1"


def test_task_propose_creates_interactive_terminal_session_metadata_when_enabled(client, app, admin_auth_header):
    tid = "T-INTERACTIVE-TERMINAL-PROPOSE"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["sgpt_routing"] = {
            "policy_version": "v2",
            "default_backend": "opencode",
            "task_kind_backend": {"ops": "opencode"},
        }
        cfg["cli_session_mode"] = {"enabled": False, "stateful_backends": ["opencode"]}
        cfg["opencode_runtime"] = {"tool_mode": "full", "execution_mode": "interactive_terminal"}
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(tid, "assigned", description="restart services in interactive terminal")

    captured_sessions: list[dict | None] = []

    def _fake_cli(prompt, options=None, timeout=None, backend=None, model=None, routing_policy=None, research_context=None, session=None, workdir=None, **kwargs):
        captured_sessions.append(session)
        return 0, '{"reason":"turn-interactive","command":"echo interactive"}', "", "opencode"

    terminal_service = MagicMock()
    terminal_service.ensure_session_for_cli.return_value = {
        "terminal_session_id": "cli-interactive-1",
        "forward_param": "cli-interactive-1",
        "agent_url": "http://worker-interactive:5000",
        "agent_name": "worker-interactive",
        "status": "active",
        "shell": "/bin/sh",
        "transport": "pty",
        "execution_mode": "interactive_terminal",
    }

    with (
        patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_cli),
        patch("agent.services.task_scoped_execution_service.get_live_terminal_session_service", return_value=terminal_service),
    ):
        response = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "restart now"}, headers=admin_auth_header)

    assert response.status_code == 200
    data = response.json["data"]
    routing = data.get("routing") or {}
    assert routing.get("session_mode") == "stateful"
    assert routing.get("execution_mode") == "interactive_terminal"
    assert (routing.get("live_terminal") or {}).get("forward_param") == "cli-interactive-1"
    assert captured_sessions
    assert captured_sessions[0] is not None
    assert ((captured_sessions[0] or {}).get("metadata") or {}).get("opencode_execution_mode") == "interactive_terminal"
    terminal_call = terminal_service.ensure_session_for_cli.call_args
    assert terminal_call.kwargs["workdir"]

    with app.app_context():
        from agent.services.worker_workspace_service import get_worker_workspace_service

        task = _get_local_task_status(tid)
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        assert terminal_call.kwargs["workdir"] == str(workspace_context.workspace_dir)
        verification = dict(task.get("verification_status") or {})
        cli_session_meta = verification.get("cli_session") or {}
        if cli_session_meta:
            assert cli_session_meta.get("execution_mode") == "interactive_terminal"
            assert cli_session_meta.get("forward_param") == "cli-interactive-1"
            assert cli_session_meta.get("agent_url") == "http://worker-interactive:5000"
        live_meta = verification.get("opencode_live_terminal") or {}
        if live_meta:
            assert live_meta.get("agent_url") == "http://worker-interactive:5000"
            assert live_meta.get("terminal_session_id") == "cli-interactive-1"


def test_task_propose_interactive_terminal_retries_timeout_with_compact_context_and_returns_error(client, app, admin_auth_header):
    tid = "T-INTERACTIVE-RETRY-TIMEOUT"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["sgpt_routing"] = {
            "policy_version": "v2",
            "default_backend": "opencode",
            "task_kind_backend": {"ops": "opencode"},
        }
        cfg["cli_session_mode"] = {"enabled": False, "stateful_backends": ["opencode"]}
        cfg["opencode_runtime"] = {
            "tool_mode": "full",
            "execution_mode": "interactive_terminal",
            "interactive_propose_timeout_seconds": 420,
            "interactive_retry_timeout_seconds": 540,
        }
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(
            tid,
            "assigned",
            description="Run interactive flow with compact retry",
            worker_execution_context={
                "context": {"context_text": "C" * 9000},
            },
        )
        assert _get_local_task_status(tid) is not None

    calls: list[dict] = []

    def _fake_cli(
        prompt,
        options=None,
        timeout=None,
        backend=None,
        model=None,
        routing_policy=None,
        research_context=None,
        session=None,
        workdir=None,
        **kwargs,
    ):
        calls.append(
            {
                "prompt": prompt,
                "timeout": timeout,
                "research_context": dict(research_context or {}),
            }
        )
        return 1, "", "Read timed out", "opencode"

    with patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_cli):
        response = client.post(
            f"/tasks/{tid}/step/propose",
            json={
                "prompt": "restart now",
                "research_context": {
                    "artifact_ids": [f"artifact-{idx}" for idx in range(1, 11)],
                    "knowledge_collection_ids": [f"kc-{idx}" for idx in range(1, 8)],
                    "repo_scope_refs": [{"path": f"src/file_{idx}.py"} for idx in range(1, 10)],
                    "prompt_section": "R" * 4500,
                },
            },
            headers=admin_auth_header,
        )

    assert response.status_code == 502
    assert response.json["message"] == "llm_cli_failed"
    assert len(calls) == 2
    assert int(calls[0]["timeout"]) >= 420
    assert int(calls[1]["timeout"]) >= 540
    assert len((calls[1]["research_context"] or {}).get("artifact_ids") or []) < len(
        (calls[0]["research_context"] or {}).get("artifact_ids") or []
    )
    assert len(str((calls[1]["research_context"] or {}).get("prompt_section") or "")) <= len(
        str((calls[0]["research_context"] or {}).get("prompt_section") or "")
    )

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        task = _get_local_task_status(tid)
        metrics = ((task.get("verification_status") or {}).get("task_flow_metrics") or {})
        assert metrics.get("propose_ok") is False
        assert metrics.get("execute_ok") is None
        assert metrics.get("artifact_created") is None


def test_task_execute_interactive_finalize_persists_flow_metrics(client, app, admin_auth_header, tmp_path):
    tid = "T-INTERACTIVE-METRICS"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status
        from agent.services.worker_workspace_service import get_worker_workspace_service

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["worker_runtime"] = {"workspace_root": str(tmp_path)}
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(
            tid,
            "proposing",
            description="Finalize interactive workspace changes",
            last_proposal={
                "backend": "opencode",
                "routing": {"task_kind": "ops", "reason": "interactive"},
                "trace": {"trace_id": "trace-interactive-metrics", "policy_version": "v1"},
                "cli_result": {"returncode": 0, "latency_ms": 55},
            },
            worker_execution_context={
                "workspace": {
                    "task_id": tid,
                    "scope_key": "scope-interactive-metrics",
                    "worker_job_id": "job-interactive-metrics",
                }
            },
        )
        task = _get_local_task_status(tid)
        workspace = get_worker_workspace_service().resolve_workspace_context(task=task)
        (workspace.workspace_dir / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")
        get_worker_workspace_service().refresh_interactive_terminal_baseline(workspace_dir=workspace.workspace_dir)
        (workspace.workspace_dir / "src").mkdir(parents=True, exist_ok=True)
        (workspace.workspace_dir / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    response = client.post(
        f"/tasks/{tid}/step/execute",
        json={"command": "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"},
        headers=admin_auth_header,
    )
    assert response.status_code == 200
    assert response.json["data"]["status"] == "completed"

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        task = _get_local_task_status(tid)
        metrics = ((task.get("verification_status") or {}).get("task_flow_metrics") or {})
        assert metrics.get("run_id") == "trace-interactive-metrics"
        assert metrics.get("propose_ok") is True
        assert metrics.get("execute_ok") is True
        assert metrics.get("artifact_created") is True
