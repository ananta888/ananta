import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from agent.routes import sgpt as sgpt_route


@pytest.fixture(autouse=True)
def reset_sgpt_state(monkeypatch):
    sgpt_route.user_requests.clear()
    sgpt_route.SGPT_CIRCUIT_BREAKER["failures"] = 0
    sgpt_route.SGPT_CIRCUIT_BREAKER["last_failure"] = 0
    sgpt_route.SGPT_CIRCUIT_BREAKER["open"] = False
    monkeypatch.setattr(sgpt_route, "is_rate_limited", lambda _user_id: False)
    yield
    sgpt_route.user_requests.clear()


def test_sgpt_execute_success(client, admin_auth_header):
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ls -la\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "list files", "options": ["--shell"]}
        response = client.post("/api/sgpt/execute", json=payload, headers=admin_auth_header)

        assert response.status_code == 200
        assert response.json["status"] == "success"
        assert "ls -la" in response.json["data"]["output"]
        assert response.json["data"]["pipeline"]["pipeline"] == "sgpt_execute"
        assert any(stage["name"] == "route" for stage in (response.json["data"]["pipeline"]["stages"] or []))
        mock_run.assert_called_once()


def test_sgpt_execute_model_overrides_default(client, admin_auth_header):
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "hello", "backend": "sgpt", "model": "custom-model"}
        response = client.post("/api/sgpt/execute", json=payload, headers=admin_auth_header)

        assert response.status_code == 200
        args = mock_run.call_args[0][0]
        assert "--model" in args
        model_idx = args.index("--model")
        assert args[model_idx + 1] == "custom-model"


def test_sgpt_execute_rejects_unsupported_flags_for_opencode(client, admin_auth_header):
    payload = {"prompt": "hello", "backend": "opencode", "options": ["--shell"]}
    response = client.post("/api/sgpt/execute", json=payload, headers=admin_auth_header)
    assert response.status_code == 400
    assert "Unsupported options for backend 'opencode'" in response.json["message"]


def test_sgpt_execute_opencode_backend(client, admin_auth_header):
    with (
        patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\opencode.cmd"),
        patch("subprocess.run") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok from opencode\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "say hi", "backend": "opencode"}
        response = client.post("/api/sgpt/execute", json=payload, headers=admin_auth_header)

        assert response.status_code == 200
        assert response.json["status"] == "success"
        assert response.json["data"]["backend"] == "opencode"
        called_args = mock_run.call_args[0][0]
        assert called_args[0].endswith("opencode.cmd")
        assert called_args[1] == "run"


def test_sgpt_execute_codex_backend(client, admin_auth_header):
    with (
        patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\codex.cmd"),
        patch("subprocess.run") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok from codex\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "review this diff", "backend": "codex", "model": "gpt-5-codex"}
        response = client.post("/api/sgpt/execute", json=payload, headers=admin_auth_header)

        assert response.status_code == 200
        assert response.json["status"] == "success"
        assert response.json["data"]["backend"] == "codex"
        called_args = mock_run.call_args[0][0]
        assert called_args[0].endswith("codex.cmd")
        assert called_args[1] == "exec"
        assert "--model" in called_args


def test_sgpt_execute_aider_backend(client, admin_auth_header):
    with (
        patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\aider.exe"),
        patch("subprocess.run") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok from aider\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "refactor this", "backend": "aider", "model": "gpt-4o-mini"}
        response = client.post("/api/sgpt/execute", json=payload, headers=admin_auth_header)

        assert response.status_code == 200
        assert response.json["status"] == "success"
        assert response.json["data"]["backend"] == "aider"
        called_args = mock_run.call_args[0][0]
        assert called_args[0].endswith("aider.exe")
        assert "--message" in called_args
        assert "--model" in called_args


def test_sgpt_execute_mistral_code_backend(client, admin_auth_header):
    with (
        patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\mistral-code.cmd"),
        patch("subprocess.run") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok from mistral code\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "generate tests", "backend": "mistral_code", "model": "codestral-latest"}
        response = client.post("/api/sgpt/execute", json=payload, headers=admin_auth_header)

        assert response.status_code == 200
        assert response.json["status"] == "success"
        assert response.json["data"]["backend"] == "mistral_code"
        called_args = mock_run.call_args[0][0]
        assert called_args[0].endswith("mistral-code.cmd")
        assert len(called_args) == 1
        assert "generate tests" in mock_run.call_args[1]["input"]


def test_sgpt_execute_invalid_backend(client, admin_auth_header):
    payload = {"prompt": "list files", "backend": "unknown-cli"}
    response = client.post("/api/sgpt/execute", json=payload, headers=admin_auth_header)
    assert response.status_code == 400
    assert "Invalid backend" in response.json["message"]
    assert response.json["status"] == "error"


def test_sgpt_execute_accepts_ananta_worker_alias(client, admin_auth_header):
    with patch("agent.routes.sgpt.run_llm_cli_command") as mock_run:
        mock_run.return_value = (0, "ok", "", "ananta-worker")
        response = client.post(
            "/api/sgpt/execute",
            json={"prompt": "list files", "backend": "ananta_worker"},
            headers=admin_auth_header,
        )

    assert response.status_code == 200
    assert response.json["data"]["backend"] == "ananta-worker"
    assert response.json["data"]["routing"]["effective_backend"] == "ananta-worker"
    assert mock_run.call_args.kwargs["backend"] == "ananta-worker"


def test_sgpt_execute_ml_intern_backend_when_enabled(client, admin_auth_header):
    cfg_res = client.post(
        "/config",
        json={"ml_intern_spike": {"enabled": True, "command_template": "python worker.py --prompt-file {prompt_file}"}},
        headers=admin_auth_header,
    )
    assert cfg_res.status_code == 200
    with patch("agent.routes.sgpt.get_ml_intern_adapter_service") as mock_service_factory:
        mock_service = MagicMock()
        mock_service.invoke_spike.return_value = {"ok": True, "stdout": "ml intern output", "stderr": "", "backend": "ml_intern"}
        mock_service_factory.return_value = mock_service
        response = client.post(
            "/api/sgpt/execute",
            json={"prompt": "analyze dataset", "backend": "ml_intern"},
            headers=admin_auth_header,
        )
    assert response.status_code == 200
    assert response.json["data"]["backend"] == "ml_intern"
    assert response.json["data"]["output"] == "ml intern output"


def test_sgpt_backends_endpoint(client, admin_auth_header):
    with patch(
        "agent.llm_integration.probe_lmstudio_runtime",
        return_value={"ok": False, "status": "error", "models_url": None, "candidate_count": 0, "candidates": []},
    ):
        response = client.get("/api/sgpt/backends", headers=admin_auth_header)
    assert response.status_code == 200
    assert response.json["status"] == "success"
    data = response.json["data"]
    assert "supported_backends" in data
    assert "runtime" in data
    assert "preflight" in data
    assert "routing_dimensions" in data
    assert "sgpt" in data["supported_backends"]
    assert "codex" in data["supported_backends"]
    assert "opencode" in data["supported_backends"]
    assert "aider" in data["supported_backends"]
    assert "mistral_code" in data["supported_backends"]
    assert "sgpt" in data["runtime"]
    assert "health_score" in data["runtime"]["sgpt"]
    assert "cli_backends" in data["preflight"]
    assert "providers" in data["preflight"]


def test_sgpt_backends_endpoint_includes_runtime_preflight_metadata(client, admin_auth_header):
    with patch("agent.common.sgpt.shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}" if cmd in {"codex", "opencode"} else None), patch(
        "agent.llm_integration.probe_lmstudio_runtime",
        return_value={
            "ok": True,
            "status": "ok",
            "models_url": "http://host.docker.internal:1234/v1/models",
            "candidate_count": 3,
            "candidates": [{"id": "qwen2.5-coder"}],
        },
    ), patch("agent.common.sgpt.settings") as mock_settings:
        mock_settings.sgpt_execution_backend = "codex"
        mock_settings.codex_path = "codex"
        mock_settings.opencode_path = "opencode"
        mock_settings.aider_path = "aider"
        mock_settings.mistral_code_path = "mistral-code"
        mock_settings.codex_default_model = "gpt-5-codex"
        mock_settings.default_provider = "lmstudio"
        mock_settings.lmstudio_url = "http://host.docker.internal:1234/v1"
        mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
        mock_settings.openai_api_key = None
        mock_settings.http_timeout = 5.0

        response = client.get("/api/sgpt/backends", headers=admin_auth_header)

    assert response.status_code == 200
    preflight = response.json["data"]["preflight"]
    routing_dimensions = response.json["data"]["routing_dimensions"]
    assert preflight["cli_backends"]["codex"]["binary_available"] is True
    assert preflight["cli_backends"]["aider"]["binary_available"] is False
    assert preflight["cli_backends"]["codex"]["install_hint"] == "npm i -g @openai/codex"
    assert preflight["cli_backends"]["codex"]["verify_command"] == "codex --help"
    assert preflight["providers"]["lmstudio"]["host_kind"] in {"private_network", "loopback", "docker_host"}
    assert preflight["providers"]["lmstudio"]["candidate_count"] == 3
    assert str(preflight["providers"]["codex"]["base_url"]).endswith("/v1")
    assert preflight["providers"]["codex"]["api_key_configured"] is True
    assert routing_dimensions["execution_backend_default"] == response.json["data"]["configured_backend"]
    assert (routing_dimensions.get("codex_runtime_target") or {}).get("target_kind") in {"local_openai", "remote_openai_compatible", "remote_ananta_hub"}


def test_sgpt_backends_endpoint_lists_custom_local_openai_runtime(client, admin_auth_header):
    with patch(
        "agent.llm_integration.probe_lmstudio_runtime",
        return_value={"ok": False, "status": "error", "models_url": None, "candidate_count": 0, "candidates": []},
    ):
        client.post(
            "/config",
            json={
                "local_openai_backends": [
                    {
                        "id": "vllm_local",
                        "name": "vLLM Local",
                        "base_url": "http://127.0.0.1:8010/v1/chat/completions",
                        "supports_tool_calls": True,
                    }
                ]
            },
            headers=admin_auth_header,
        )
        response = client.get("/api/sgpt/backends", headers=admin_auth_header)

    assert response.status_code == 200
    providers = response.json["data"]["preflight"]["providers"]["local_openai"]
    assert any(item["provider"] == "vllm_local" and item["base_url"] == "http://127.0.0.1:8010/v1" for item in providers)
    assert any(item["provider"] == "vllm_local" and item["provider_type"] == "local_openai_compatible" for item in providers)


def test_sgpt_backends_endpoint_reports_invalid_lmstudio_runtime_metadata(client, admin_auth_header):
    with patch(
        "agent.llm_integration.probe_lmstudio_runtime",
        return_value={
            "ok": False,
            "status": "invalid_url",
            "base_url": "not-a-valid-url",
            "models_url": None,
            "candidate_count": 0,
            "candidates": [],
        },
    ), patch("agent.common.sgpt.settings") as mock_settings:
        mock_settings.sgpt_execution_backend = "codex"
        mock_settings.codex_path = "codex"
        mock_settings.opencode_path = "opencode"
        mock_settings.aider_path = "aider"
        mock_settings.mistral_code_path = "mistral-code"
        mock_settings.codex_default_model = "gpt-5-codex"
        mock_settings.default_provider = "lmstudio"
        mock_settings.lmstudio_url = "not-a-valid-url"
        mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
        mock_settings.openai_api_key = None
        mock_settings.http_timeout = 5.0

        response = client.get("/api/sgpt/backends", headers=admin_auth_header)

    assert response.status_code == 200
    provider = response.json["data"]["preflight"]["providers"]["lmstudio"]
    assert provider["configured"] is True
    assert provider["status"] == "invalid_url"
    assert provider["reachable"] is False
    assert provider["models_url"] is None


def test_sgpt_execute_missing_prompt(client, admin_auth_header):
    payload = {"options": ["--shell"]}
    response = client.post("/api/sgpt/execute", json=payload, headers=admin_auth_header)
    assert response.status_code == 400
    assert "Missing prompt" in response.json["message"]
    assert response.json["status"] == "error"


def test_sgpt_execute_error(client, admin_auth_header):
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = Exception("Internal Error")

        payload = {"prompt": "list files"}
        response = client.post("/api/sgpt/execute", json=payload, headers=admin_auth_header)

        assert response.status_code == 500
        assert "Internal Error" in response.json["message"]
        assert response.json["status"] == "error"


def test_sgpt_context_endpoint_success(client, admin_auth_header):
    fake_rag_service = MagicMock()
    fake_rag_service.retrieve_context_bundle.return_value = {
        "query": "find docs",
        "strategy": {"repository_map": 1, "semantic_search": 2, "agentic_search": 1},
        "policy_version": "v1",
        "bundle_type": "retrieval_context",
        "chunks": [
            {
                "engine": "knowledge_index",
                "source": "docs/README.md",
                "content": "x",
                "score": 1.0,
                "metadata": {
                    "artifact_id": "artifact-1",
                    "knowledge_index_id": "idx-1",
                    "record_kind": "md_section",
                    "collection_names": ["team-docs"],
                },
            }
        ],
        "context_text": "ctx",
        "token_estimate": 10,
        "chunk_count": 1,
        "explainability": {
            "engines": ["knowledge_index"],
            "artifact_ids": ["artifact-1"],
            "knowledge_index_ids": ["idx-1"],
            "chunk_types": ["md_section"],
            "collection_names": ["team-docs"],
            "source_count": 1,
        },
    }
    with patch("agent.routes.sgpt.get_rag_service", return_value=fake_rag_service):
        response = client.post(
            "/api/sgpt/context",
            json={
                "query": "find docs",
                "task_kind": "doc",
                "retrieval_intent": "architecture_and_decision_context",
                "source_types": ["repo", "artifact"],
            },
            headers=admin_auth_header,
        )

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["data"]["policy_version"] == "v1"
    assert response.json["data"]["chunks"]
    assert response.json["data"]["chunk_count"] == 1
    assert response.json["data"]["explainability"]["collection_names"] == ["team-docs"]
    fake_rag_service.retrieve_context_bundle.assert_called_once()
    kwargs = fake_rag_service.retrieve_context_bundle.call_args.kwargs
    assert kwargs["task_kind"] == "doc"
    assert kwargs["retrieval_intent"] == "architecture_and_decision_context"
    assert kwargs["source_types"] == ["repo", "artifact"]


def test_sgpt_execute_with_hybrid_context(client, admin_auth_header):
    fake_context_manager = MagicMock()
    fake_context_manager.build_cli_execution_context.return_value = (
        {
            "query": "where timeout bug",
            "strategy": {"repository_map": 3, "semantic_search": 1, "agentic_search": 1},
            "policy_version": "v1",
            "bundle_type": "retrieval_context",
            "chunks": [{"engine": "repository_map", "source": "module.py", "content": "x", "score": 2.0, "metadata": {}}],
            "context_text": "selected context",
            "token_estimate": 30,
        },
        "Nutze den folgenden selektiven Kontext und beantworte die Frage praezise.\n\nFrage:\nwhere timeout bug\n\nKontext:\nselected context",
    )

    with (
        patch("agent.routes.sgpt.get_context_manager_service", return_value=fake_context_manager),
        patch("subprocess.run") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "answer"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        response = client.post(
            "/api/sgpt/execute",
            json={
                "prompt": "where timeout bug",
                "use_hybrid_context": True,
                "task_kind": "bugfix",
                "retrieval_intent": "localize bug",
                "source_types": ["repo", "artifact"],
            },
            headers=admin_auth_header,
        )

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["data"]["context"]["policy_version"] == "v1"
    assert response.json["data"]["context"]["chunk_count"] == 1
    fake_context_manager.build_cli_execution_context.assert_called_once()
    kwargs = fake_context_manager.build_cli_execution_context.call_args.kwargs
    assert kwargs["task_kind"] == response.json["data"]["routing"]["task_kind"]
    assert kwargs["retrieval_intent"] == "localize bug"
    assert kwargs["source_types"] == ["repo", "artifact"]


def test_sgpt_execute_auto_routing_by_task_kind_policy(client, app, admin_auth_header):
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "sgpt_routing": {
            "policy_version": "v2",
            "default_backend": "opencode",
            "task_kind_backend": {"coding": "opencode", "analysis": "opencode", "doc": "opencode", "ops": "opencode"},
        },
    }
    with patch("agent.routes.sgpt.run_llm_cli_command") as mock_run:
        mock_run.return_value = (0, "ok", "", "opencode")
        response = client.post(
            "/api/sgpt/execute", json={"prompt": "implement endpoint", "backend": "auto", "task_kind": "coding"}, headers=admin_auth_header
        )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["backend"] == "opencode"
    assert data["routing"]["task_kind"] == "coding"
    assert data["routing"]["effective_backend"] == "opencode"
    assert data["routing"]["reason"] == "task_kind_policy:coding->opencode"


def test_sgpt_execute_auto_routing_exposes_reason_without_task_kind(client, app, admin_auth_header):
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "sgpt_routing": {
            "policy_version": "v2",
            "default_backend": "opencode",
            "task_kind_backend": {"coding": "opencode"},
        },
    }
    with patch("agent.routes.sgpt.run_llm_cli_command") as mock_run:
        mock_run.return_value = (0, "ok", "", "opencode")
        response = client.post("/api/sgpt/execute", json={"prompt": "explain architecture", "backend": "auto"}, headers=admin_auth_header)

    assert response.status_code == 200
    data = response.json["data"]
    assert data["routing"]["task_kind"] == "doc"
    assert data["routing"]["effective_backend"] == "opencode"
    assert data["routing"]["reason"] == "default_policy:opencode"


def test_sgpt_execute_auto_routing_defaults_to_ananta_worker(client, app, admin_auth_header):
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "sgpt_routing": {
            "policy_version": "v2",
            "task_kind_backend": {},
        },
    }
    with patch("agent.routes.sgpt.run_llm_cli_command") as mock_run:
        mock_run.return_value = (0, "ok", "", "ananta-worker")
        response = client.post("/api/sgpt/execute", json={"prompt": "explain architecture", "backend": "auto"}, headers=admin_auth_header)

    assert response.status_code == 200
    data = response.json["data"]
    assert data["routing"]["effective_backend"] == "ananta-worker"
    assert data["routing"]["reason"] == "default_policy:ananta-worker"


def test_sgpt_source_preview_success(client, tmp_path, admin_auth_header):
    source_file = tmp_path / "sample.py"
    source_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    with patch("agent.routes.sgpt.settings") as mock_settings:
        mock_settings.rag_repo_root = str(tmp_path)
        mock_settings.rag_enabled = True
        response = client.post("/api/sgpt/source", json={"source_path": "sample.py"}, headers=admin_auth_header)

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert "def hello" in response.json["data"]["preview"]


def test_sgpt_execute_returns_context_limit_diagnostics_for_opencode_errors(client, admin_auth_header):
    with patch("agent.routes.sgpt.run_llm_cli_command") as mock_run:
        mock_run.return_value = (
            1,
            "",
            'Error: "Cannot truncate prompt with n_keep (9801) >= n_ctx (4096)"',
            "opencode",
        )
        response = client.post("/api/sgpt/execute", json={"prompt": "analyze", "backend": "opencode"}, headers=admin_auth_header)

    assert response.status_code == 500
    assert response.json["status"] == "error"
    diagnostics = (response.json.get("data") or {}).get("diagnostics") or {}
    assert diagnostics.get("type") == "context_limit_mismatch"
    assert diagnostics.get("backend") == "opencode"


def test_sgpt_stateful_sessions_create_and_turn(client, app, admin_auth_header):
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "cli_session_mode": {
            "enabled": True,
            "stateful_backends": ["opencode", "codex"],
            "max_turns_per_session": 6,
            "max_sessions": 50,
        },
    }
    create_res = client.post(
        "/api/sgpt/sessions",
        json={"backend": "opencode", "model": "opencode/glm-5-free", "conversation_id": "conv-1"},
        headers=admin_auth_header,
    )
    assert create_res.status_code == 201
    payload = create_res.json["data"]
    session_id = (payload.get("session") or {}).get("id")
    assert session_id

    with patch("agent.routes.sgpt.run_llm_cli_command") as mock_run:
        mock_run.return_value = (0, "turn-1-output", "", "opencode")
        turn_res = client.post(
            f"/api/sgpt/sessions/{session_id}/turn",
            json={"prompt": "first turn"},
            headers=admin_auth_header,
        )
    assert turn_res.status_code == 200
    turn_payload = turn_res.json["data"]
    assert turn_payload["session_id"] == session_id
    assert turn_payload["output"] == "turn-1-output"
    assert (turn_payload.get("routing") or {}).get("session_mode") == "stateful"
    assert (turn_payload.get("session_turn") or {}).get("index") == 1

    get_res = client.get(f"/api/sgpt/sessions/{session_id}?include_history=1", headers=admin_auth_header)
    assert get_res.status_code == 200
    assert len((get_res.json.get("data") or {}).get("history") or []) == 1


def test_sgpt_stateful_sessions_reject_when_disabled(client, app, admin_auth_header):
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "cli_session_mode": {"enabled": False, "stateful_backends": ["opencode"]},
    }
    res = client.post("/api/sgpt/sessions", json={"backend": "opencode"}, headers=admin_auth_header)
    assert res.status_code == 403
    assert res.json["message"] == "cli_sessions_disabled"


def test_sgpt_native_opencode_session_turn_skips_prompt_replay(client, app, admin_auth_header):
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "cli_session_mode": {
            "enabled": True,
            "stateful_backends": ["opencode"],
            "max_turns_per_session": 6,
            "max_sessions": 50,
            "native_opencode_sessions": True,
        },
    }
    runtime_service = MagicMock()
    runtime_service.ensure_session_runtime.return_value = {
        "kind": "native_server",
        "native_session_id": "ses-native-1",
        "server_key": "role::model",
        "server_url": "http://127.0.0.1:4100",
        "agent": "ananta-worker",
    }
    with patch("agent.services.opencode_runtime_service.get_opencode_runtime_service", return_value=runtime_service):
        create_res = client.post(
            "/api/sgpt/sessions",
            json={"backend": "opencode", "model": "ollama/example", "conversation_id": "role:po"},
            headers=admin_auth_header,
        )
    assert create_res.status_code == 201
    session_id = ((create_res.json.get("data") or {}).get("session") or {}).get("id")
    assert session_id
    runtime_service.ensure_session_runtime.assert_called_once()
    runtime_args = runtime_service.ensure_session_runtime.call_args
    assert runtime_args.args[0]["id"] == session_id
    assert runtime_args.kwargs["model"] == "ollama/example"

    with patch("agent.routes.sgpt.run_llm_cli_command") as mock_run:
        mock_run.return_value = (0, "native-turn-output", "", "opencode")
        turn_res = client.post(
            f"/api/sgpt/sessions/{session_id}/turn",
            json={"prompt": "second turn"},
            headers=admin_auth_header,
        )

    assert turn_res.status_code == 200
    args = mock_run.call_args.args
    kwargs = mock_run.call_args.kwargs
    assert args[0] == "second turn"
    assert kwargs["session"]["id"] == session_id


# ── CCSH: CodeCompass Relevant-Snippet Handoff Tests ─────────────────────────

def _make_workdir(tmp_path: pathlib.Path, refs: list[dict], hub_content: str | None = None) -> pathlib.Path:
    """Create a minimal ananta-worker workspace with research-context.json."""
    rag_dir = tmp_path / "rag_helper"
    rag_dir.mkdir(parents=True, exist_ok=True)
    (rag_dir / "research-context.json").write_text(
        json.dumps({"repo_scope_refs": refs}), encoding="utf-8"
    )
    if hub_content is not None:
        ananta_dir = tmp_path / ".ananta"
        ananta_dir.mkdir(exist_ok=True)
        (ananta_dir / "hub-context.md").write_text(hub_content, encoding="utf-8")
    return tmp_path


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal fake repo root with an agent/ subdirectory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent").mkdir()
    return repo


# CCSH-003: Regression test — path-only behavior still works
class TestSourceFileBatchesPathOnly:
    def test_path_only_loads_file_beginning(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        sample.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py"}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        block = batches[0][0]
        assert block["rel_path"] == "sample.py"
        assert "def hello" in block["content"]
        assert block["source_kind"] == "file_excerpt"
        assert block["start_line"] is None
        assert block["end_line"] is None

    def test_path_traversal_blocked(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        secret = tmp_path / "secret.txt"
        secret.write_text("top-secret", encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "../secret.txt"}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert batches == []

    def test_nonexistent_path_skipped(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = _make_workdir(tmp_path / "ws", [{"path": "doesnotexist.py"}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert batches == []

    def test_empty_refs_falls_back_to_hub_context(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = _make_workdir(tmp_path / "ws", [], hub_content="# Hub context\nsome content")

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "hub_context"
        assert "Hub context" in batches[0][0]["content"]

    def test_invalid_json_research_context_falls_back_gracefully(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = tmp_path / "ws"
        rag_dir = workdir / "rag_helper"
        rag_dir.mkdir(parents=True)
        (rag_dir / "research-context.json").write_text("{not valid json", encoding="utf-8")
        hub_dir = workdir / ".ananta"
        hub_dir.mkdir()
        (hub_dir / "hub-context.md").write_text("fallback content", encoding="utf-8")

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        # Should gracefully fall back to hub-context.md
        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "hub_context"

    def test_missing_workdir_returns_empty(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        batches = _load_source_file_batches(str(tmp_path / "nonexistent"))
        assert batches == []


# CCSH-004: Line-range normalization
class TestLineRangeNormalization:
    def test_start_line_end_line_loads_specific_range(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        lines = ["# irrelevant header"] * 20 + ["def target_func():", "    return 42"] + ["# irrelevant footer"] * 20
        sample.write_text("\n".join(lines), encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py", "start_line": 21, "end_line": 22}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), context_lines=0)

        assert len(batches) == 1
        block = batches[0][0]
        assert block["source_kind"] == "line_range"
        assert "def target_func" in block["content"]
        # File beginning (irrelevant headers) should NOT dominate
        assert block["content"].count("# irrelevant header") == 0

    def test_line_start_alias_accepted(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        sample.write_text("\n".join([f"line{i}" for i in range(1, 30)]), encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py", "line_start": 5, "line_end": 7}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), context_lines=0)

        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "line_range"
        assert "line5" in batches[0][0]["content"]

    def test_from_line_to_line_alias_accepted(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        sample.write_text("\n".join([f"L{i}" for i in range(1, 30)]), encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py", "from_line": 10, "to_line": 12}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), context_lines=0)

        assert batches[0][0]["source_kind"] == "line_range"
        assert "L10" in batches[0][0]["content"]

    def test_invalid_line_range_falls_back_to_file_excerpt(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        sample.write_text("content\n", encoding="utf-8")

        # end < start — invalid
        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py", "start_line": 10, "end_line": 5}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "file_excerpt"


# CCSH-004/005: Snippet and chunk priority
class TestSnippetAndChunkPriority:
    def test_snippet_used_when_path_missing(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = _make_workdir(tmp_path / "ws", [{"snippet": "def my_func(): pass"}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "codecompass_snippet"
        assert "def my_func" in batches[0][0]["content"]

    def test_chunks_in_ref_used_as_context_blocks(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        chunks = [
            {"source": "module.py", "content": "def func_a(): pass", "score": 0.9},
            {"source": "module.py", "content": "def func_b(): pass", "score": 0.8},
        ]
        workdir = _make_workdir(tmp_path / "ws", [{"path": "module.py", "chunks": chunks}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), files_per_batch=5)

        # Chunks from ref used, not file-beginning fallback
        all_blocks = [b for batch in batches for b in batch]
        kinds = [b["source_kind"] for b in all_blocks]
        assert all(k == "chunk" for k in kinds)
        contents = " ".join(b["content"] for b in all_blocks)
        assert "func_a" in contents
        assert "func_b" in contents

    def test_duplicate_blocks_deduplicated(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        sample.write_text("def hello(): pass\n", encoding="utf-8")

        # Same path twice → only one block
        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py"}, {"path": "sample.py"}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        all_blocks = [b for batch in batches for b in batch]
        paths = [b["rel_path"] for b in all_blocks]
        assert paths.count("sample.py") == 1

    def test_blocks_sorted_by_score_descending(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        for name in ("a.py", "b.py", "c.py"):
            (repo / name).write_text(f"# {name}\n", encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [
            {"path": "a.py", "score": 0.3},
            {"path": "b.py", "score": 0.9},
            {"path": "c.py", "score": 0.6},
        ])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), files_per_batch=10)

        all_blocks = [b for batch in batches for b in batch]
        scores = [b["score"] for b in all_blocks]
        assert scores == sorted(scores, reverse=True)


# CCSH-002: Prompt header visibility
class TestPromptHeaders:
    def test_line_range_header_includes_line_numbers(self):
        from agent.common.sgpt import _build_iteration_prompt

        batch = [{
            "rel_path": "agent/foo.py",
            "lang": "python",
            "content": "def bar(): pass",
            "source_kind": "line_range",
            "start_line": 42,
            "end_line": 55,
            "score": 0.85,
            "reason": None,
            "symbol": None,
        }]
        prompt = _build_iteration_prompt("do something", batch=batch, progress_so_far="", step=1, total_steps=1)

        assert "agent/foo.py:42-55" in prompt
        assert "[line_range" in prompt
        assert "score=0.85" in prompt

    def test_file_excerpt_header_shows_source_kind(self):
        from agent.common.sgpt import _build_iteration_prompt

        batch = [{
            "rel_path": "agent/bar.py",
            "lang": "python",
            "content": "x = 1",
            "source_kind": "file_excerpt",
            "start_line": None,
            "end_line": None,
            "score": None,
            "reason": None,
            "symbol": None,
        }]
        prompt = _build_iteration_prompt("question", batch=batch, progress_so_far="", step=1, total_steps=1)

        assert "### agent/bar.py [file_excerpt]" in prompt

    def test_hub_context_header(self):
        from agent.common.sgpt import _build_iteration_prompt

        batch = [{
            "rel_path": "hub-context.md",
            "lang": "markdown",
            "content": "some context",
            "source_kind": "hub_context",
            "start_line": None,
            "end_line": None,
            "score": None,
            "reason": None,
            "symbol": None,
        }]
        prompt = _build_iteration_prompt("q", batch=batch, progress_so_far="", step=1, total_steps=1)
        assert "### hub-context.md [hub_context]" in prompt


# CCSH-011: E2E — relevant function far down in file is loaded, not file beginning
class TestCodeCompassSnippetHandoff:
    def test_line_range_loads_deep_function_not_file_beginning(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "deep_func.py"
        # 100 lines of irrelevant header, then relevant function
        header = ["# IRRELEVANT_MARKER"] * 100
        target_func = [
            "def deeply_buried_target():",
            "    return 'found_me'",
        ]
        sample.write_text("\n".join(header + target_func), encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{
            "path": "deep_func.py",
            "start_line": 101,
            "end_line": 102,
            "score": 0.95,
            "reason": "target function",
        }])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), context_lines=0, per_file_chars=4000)

        assert len(batches) == 1
        block = batches[0][0]
        assert block["source_kind"] == "line_range"
        assert "deeply_buried_target" in block["content"]
        # The irrelevant file beginning should NOT be present in the content
        assert "IRRELEVANT_MARKER" not in block["content"]

    def test_single_batch_prompt_includes_header_annotation(self, tmp_path):
        """Single-batch path in _run_ananta_worker_iterative also uses annotated headers."""
        from agent.common.sgpt import _load_source_file_batches, _format_block_header

        repo = _make_repo(tmp_path)
        sample = repo / "module.py"
        sample.write_text("def answer(): return 42\n", encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "module.py"}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        header = _format_block_header(batches[0][0])
        assert "### module.py" in header
        assert "[file_excerpt]" in header

    def test_path_traversal_still_blocked_with_line_range(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        secret = tmp_path / "secret.txt"
        secret.write_text("classified", encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "../secret.txt", "start_line": 1, "end_line": 1}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert batches == []


# CCSH-012: Backward compatibility regression tests
class TestBackwardCompatibility:
    def test_path_only_still_works_after_refactor(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        (repo / "legacy.py").write_text("x = 1\n", encoding="utf-8")
        workdir = _make_workdir(tmp_path / "ws", [{"path": "legacy.py"}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "file_excerpt"

    def test_empty_repo_scope_refs_falls_back_to_hub(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = _make_workdir(tmp_path / "ws", [], hub_content="hub fallback")

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert batches[0][0]["source_kind"] == "hub_context"

    def test_missing_research_context_json_uses_hub_fallback(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = tmp_path / "ws"
        workdir.mkdir()
        ananta_dir = workdir / ".ananta"
        ananta_dir.mkdir()
        (ananta_dir / "hub-context.md").write_text("hub only", encoding="utf-8")

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert batches[0][0]["source_kind"] == "hub_context"

    def test_invalid_json_does_not_raise(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = tmp_path / "ws"
        (workdir / "rag_helper").mkdir(parents=True)
        (workdir / "rag_helper" / "research-context.json").write_text("{{broken", encoding="utf-8")

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            # Must not raise
            batches = _load_source_file_batches(str(workdir))

        assert isinstance(batches, list)


# CCSH-013: Budget guard
class TestBudgetGuard:
    def test_max_files_cap_limits_loaded_blocks(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        refs = []
        for i in range(10):
            f = repo / f"file{i}.py"
            f.write_text(f"# file {i}\n", encoding="utf-8")
            refs.append({"path": f"file{i}.py"})

        workdir = _make_workdir(tmp_path / "ws", refs)

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), max_files=3)

        all_blocks = [b for batch in batches for b in batch]
        assert len(all_blocks) == 3

    def test_higher_score_blocks_survive_budget_cut(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        # 3 files, different scores; budget = 2
        (repo / "low.py").write_text("# low score\n", encoding="utf-8")
        (repo / "mid.py").write_text("# mid score\n", encoding="utf-8")
        (repo / "high.py").write_text("# high score\n", encoding="utf-8")

        refs = [
            {"path": "low.py", "score": 0.1},
            {"path": "mid.py", "score": 0.5},
            {"path": "high.py", "score": 0.9},
        ]
        workdir = _make_workdir(tmp_path / "ws", refs)

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), max_files=2)

        all_blocks = [b for batch in batches for b in batch]
        assert len(all_blocks) == 2
        # Highest-scored blocks should be present
        paths = {b["rel_path"] for b in all_blocks}
        assert "high.py" in paths
        assert "mid.py" in paths
        assert "low.py" not in paths


# CCSH-006: Config-driven line window
class TestConfigDrivenContext:
    def test_context_lines_parameter_controls_window_size(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        lines = [f"line{i}" for i in range(1, 31)]
        sample.write_text("\n".join(lines), encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py", "start_line": 15, "end_line": 15}])

        with patch("agent.common.sgpt._resolve_repo_root", return_value=repo):
            batches_narrow = _load_source_file_batches(str(workdir), context_lines=0)
            batches_wide = _load_source_file_batches(str(workdir), context_lines=3)

        content_narrow = batches_narrow[0][0]["content"]
        content_wide = batches_wide[0][0]["content"]

        # Narrow: only line15
        assert "line15" in content_narrow
        assert "line12" not in content_narrow

        # Wide: lines 12–18 should be visible
        assert "line15" in content_wide
        assert "line12" in content_wide
