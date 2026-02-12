from unittest.mock import MagicMock, patch


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
