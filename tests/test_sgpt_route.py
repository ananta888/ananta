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
        response = client.post("/api/sgpt/context", json={"query": "find docs"}, headers=admin_auth_header)

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["data"]["policy_version"] == "v1"
    assert response.json["data"]["chunks"]
    assert response.json["data"]["chunk_count"] == 1
    assert response.json["data"]["explainability"]["collection_names"] == ["team-docs"]


def test_sgpt_execute_with_hybrid_context(client, admin_auth_header):
    fake_rag_service = MagicMock()
    fake_rag_service.build_execution_context.return_value = (
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
        patch("agent.routes.sgpt.get_rag_service", return_value=fake_rag_service),
        patch("subprocess.run") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "answer"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        response = client.post(
            "/api/sgpt/execute",
            json={"prompt": "where timeout bug", "use_hybrid_context": True},
            headers=admin_auth_header,
        )

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["data"]["context"]["policy_version"] == "v1"
    assert response.json["data"]["context"]["chunk_count"] == 1


def test_sgpt_execute_auto_routing_by_task_kind_policy(client, app, admin_auth_header):
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "sgpt_routing": {
            "policy_version": "v2",
            "default_backend": "sgpt",
            "task_kind_backend": {"coding": "aider", "analysis": "sgpt", "doc": "sgpt", "ops": "opencode"},
        },
    }
    with patch("agent.routes.sgpt.run_llm_cli_command") as mock_run:
        mock_run.return_value = (0, "ok", "", "aider")
        response = client.post(
            "/api/sgpt/execute", json={"prompt": "implement endpoint", "backend": "auto", "task_kind": "coding"}, headers=admin_auth_header
        )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["backend"] == "aider"
    assert data["routing"]["task_kind"] == "coding"
    assert data["routing"]["effective_backend"] == "aider"
    assert data["routing"]["reason"] == "task_kind_policy:coding->aider"


def test_sgpt_execute_auto_routing_exposes_reason_without_task_kind(client, app, admin_auth_header):
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "sgpt_routing": {
            "policy_version": "v2",
            "default_backend": "sgpt",
            "task_kind_backend": {"coding": "aider"},
        },
    }
    with patch("agent.routes.sgpt.run_llm_cli_command") as mock_run:
        mock_run.return_value = (0, "ok", "", "sgpt")
        response = client.post("/api/sgpt/execute", json={"prompt": "explain architecture", "backend": "auto"}, headers=admin_auth_header)

    assert response.status_code == 200
    data = response.json["data"]
    assert data["routing"]["task_kind"] == "doc"
    assert data["routing"]["effective_backend"] == "sgpt"
    assert data["routing"]["reason"] in {"default_policy:sgpt", "task_kind_policy:analysis->sgpt"}


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
