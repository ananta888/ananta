from unittest.mock import MagicMock, patch

import pytest

from agent.routes import sgpt as sgpt_route


@pytest.fixture(autouse=True)
def reset_sgpt_state():
    sgpt_route.user_requests.clear()
    sgpt_route.SGPT_CIRCUIT_BREAKER["failures"] = 0
    sgpt_route.SGPT_CIRCUIT_BREAKER["last_failure"] = 0
    sgpt_route.SGPT_CIRCUIT_BREAKER["open"] = False
    yield
    sgpt_route.user_requests.clear()


def test_sgpt_execute_success(client):
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ls -la\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "list files", "options": ["--shell"]}
        response = client.post("/api/sgpt/execute", json=payload)

        assert response.status_code == 200
        assert response.json["status"] == "success"
        assert "ls -la" in response.json["data"]["output"]
        mock_run.assert_called_once()


def test_sgpt_execute_model_overrides_default(client):
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "hello", "backend": "sgpt", "model": "custom-model"}
        response = client.post("/api/sgpt/execute", json=payload)

        assert response.status_code == 200
        args = mock_run.call_args[0][0]
        assert "--model" in args
        model_idx = args.index("--model")
        assert args[model_idx + 1] == "custom-model"


def test_sgpt_execute_rejects_unsupported_flags_for_opencode(client):
    payload = {"prompt": "hello", "backend": "opencode", "options": ["--shell"]}
    response = client.post("/api/sgpt/execute", json=payload)
    assert response.status_code == 400
    assert "Unsupported options for backend 'opencode'" in response.json["message"]


def test_sgpt_execute_opencode_backend(client):
    with patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\opencode.cmd"), patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok from opencode\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "say hi", "backend": "opencode"}
        response = client.post("/api/sgpt/execute", json=payload)

        assert response.status_code == 200
        assert response.json["status"] == "success"
        assert response.json["data"]["backend"] == "opencode"
        called_args = mock_run.call_args[0][0]
        assert called_args[0].endswith("opencode.cmd")
        assert called_args[1] == "run"


def test_sgpt_execute_aider_backend(client):
    with patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\aider.exe"), patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok from aider\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "refactor this", "backend": "aider", "model": "gpt-4o-mini"}
        response = client.post("/api/sgpt/execute", json=payload)

        assert response.status_code == 200
        assert response.json["status"] == "success"
        assert response.json["data"]["backend"] == "aider"
        called_args = mock_run.call_args[0][0]
        assert called_args[0].endswith("aider.exe")
        assert "--message" in called_args
        assert "--model" in called_args


def test_sgpt_execute_mistral_code_backend(client):
    with patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\mistral-code.cmd"), patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok from mistral code\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        payload = {"prompt": "generate tests", "backend": "mistral_code", "model": "codestral-latest"}
        response = client.post("/api/sgpt/execute", json=payload)

        assert response.status_code == 200
        assert response.json["status"] == "success"
        assert response.json["data"]["backend"] == "mistral_code"
        called_args = mock_run.call_args[0][0]
        assert called_args[0].endswith("mistral-code.cmd")
        assert len(called_args) == 1
        assert "generate tests" in mock_run.call_args[1]["input"]


def test_sgpt_execute_invalid_backend(client):
    payload = {"prompt": "list files", "backend": "unknown-cli"}
    response = client.post("/api/sgpt/execute", json=payload)
    assert response.status_code == 400
    assert "Invalid backend" in response.json["message"]
    assert response.json["status"] == "error"


def test_sgpt_backends_endpoint(client):
    response = client.get("/api/sgpt/backends")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    data = response.json["data"]
    assert "supported_backends" in data
    assert "runtime" in data
    assert "sgpt" in data["supported_backends"]
    assert "opencode" in data["supported_backends"]
    assert "aider" in data["supported_backends"]
    assert "mistral_code" in data["supported_backends"]
    assert "sgpt" in data["runtime"]
    assert "health_score" in data["runtime"]["sgpt"]


def test_sgpt_execute_missing_prompt(client):
    payload = {"options": ["--shell"]}
    response = client.post("/api/sgpt/execute", json=payload)
    assert response.status_code == 400
    assert "Missing prompt" in response.json["message"]
    assert response.json["status"] == "error"


def test_sgpt_execute_error(client):
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = Exception("Internal Error")

        payload = {"prompt": "list files"}
        response = client.post("/api/sgpt/execute", json=payload)

        assert response.status_code == 500
        assert "Internal Error" in response.json["message"]
        assert response.json["status"] == "error"


def test_sgpt_context_endpoint_success(client):
    fake_orchestrator = MagicMock()
    fake_orchestrator.get_relevant_context.return_value = {
        "query": "find docs",
        "strategy": {"repository_map": 1, "semantic_search": 2, "agentic_search": 1},
        "policy_version": "v1",
        "chunks": [{"engine": "semantic_search", "source": "docs/README.md", "content": "x", "score": 1.0, "metadata": {}}],
        "context_text": "ctx",
        "token_estimate": 10,
    }
    with patch("agent.routes.sgpt.get_orchestrator", return_value=fake_orchestrator):
        response = client.post("/api/sgpt/context", json={"query": "find docs"})

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["data"]["policy_version"] == "v1"
    assert response.json["data"]["chunks"]


def test_sgpt_execute_with_hybrid_context(client):
    fake_orchestrator = MagicMock()
    fake_orchestrator.get_relevant_context.return_value = {
        "query": "where timeout bug",
        "strategy": {"repository_map": 3, "semantic_search": 1, "agentic_search": 1},
        "policy_version": "v1",
        "chunks": [{"engine": "repository_map", "source": "module.py", "content": "x", "score": 2.0, "metadata": {}}],
        "context_text": "selected context",
        "token_estimate": 30,
    }

    with patch("agent.routes.sgpt.get_orchestrator", return_value=fake_orchestrator), patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "answer"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        response = client.post(
            "/api/sgpt/execute",
            json={"prompt": "where timeout bug", "use_hybrid_context": True},
        )

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["data"]["context"]["policy_version"] == "v1"
    assert response.json["data"]["context"]["chunk_count"] == 1


def test_sgpt_execute_auto_routing_by_task_kind_policy(client, app):
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
        response = client.post("/api/sgpt/execute", json={"prompt": "implement endpoint", "backend": "auto", "task_kind": "coding"})

    assert response.status_code == 200
    data = response.json["data"]
    assert data["backend"] == "aider"
    assert data["routing"]["task_kind"] == "coding"
    assert data["routing"]["effective_backend"] == "aider"
    assert data["routing"]["reason"] == "task_kind_policy:coding->aider"


def test_sgpt_execute_auto_routing_exposes_reason_without_task_kind(client, app):
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
        response = client.post("/api/sgpt/execute", json={"prompt": "explain architecture", "backend": "auto"})

    assert response.status_code == 200
    data = response.json["data"]
    assert data["routing"]["task_kind"] == "doc"
    assert data["routing"]["effective_backend"] == "sgpt"
    assert data["routing"]["reason"] in {"default_policy:sgpt", "task_kind_policy:analysis->sgpt"}


def test_sgpt_source_preview_success(client, tmp_path):
    source_file = tmp_path / "sample.py"
    source_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    with patch("agent.routes.sgpt.settings") as mock_settings:
        mock_settings.rag_repo_root = str(tmp_path)
        mock_settings.rag_enabled = True
        response = client.post("/api/sgpt/source", json={"source_path": "sample.py"})

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert "def hello" in response.json["data"]["preview"]
