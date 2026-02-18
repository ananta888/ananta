from unittest.mock import patch


def test_sgpt_capability_matrix_endpoint(client):
    response = client.get("/api/sgpt/capability-matrix")
    assert response.status_code == 200
    items = response.json["data"]["items"]
    assert isinstance(items, list)
    assert any(i["backend"] == "sgpt" for i in items)


def test_sgpt_execute_exposes_trace_and_grounding(client):
    fake_orchestrator = type(
        "FakeOrchestrator",
        (),
        {
            "get_relevant_context": lambda self, _q: {
                "chunks": [{"engine": "repository_map", "source": "x.py", "content": "x"}],
                "context_text": "ctx",
                "strategy": {"repository_map": 1},
                "policy_version": "v1",
                "token_estimate": 10,
            }
        },
    )()
    with patch("agent.routes.sgpt.get_orchestrator", return_value=fake_orchestrator), patch("agent.routes.sgpt.run_llm_cli_command") as run:
        run.return_value = (0, "ok", "", "sgpt")
        res = client.post("/api/sgpt/execute", json={"prompt": "x", "use_hybrid_context": True})
    assert res.status_code == 200
    data = res.json["data"]
    assert data.get("trace_id")
    assert "grounding" in data
    assert "fallback" in data
