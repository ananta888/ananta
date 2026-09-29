from unittest.mock import patch


def test_sgpt_capability_matrix_endpoint(client, admin_auth_header):
    response = client.get("/api/sgpt/capability-matrix", headers=admin_auth_header)
    assert response.status_code == 200
    items = response.json["data"]["items"]
    assert isinstance(items, list)
    assert any(i["backend"] == "sgpt" for i in items)
    codex = next((i for i in items if i["backend"] == "codex"), None)
    assert codex is not None
    assert codex["task_fit"]["coding"] is True


def test_sgpt_execute_exposes_trace_and_grounding(client, admin_auth_header):
    fake_rag_service = type(
        "FakeRagService",
        (),
        {
            "build_execution_context": lambda self, _q: (
                {
                    "chunks": [{"engine": "repository_map", "source": "x.py", "content": "x"}],
                    "context_text": "ctx",
                    "strategy": {"repository_map": 1},
                    "policy_version": "v1",
                    "token_estimate": 10,
                },
                "ctx",
            )
        },
    )()
    with (
        patch("agent.routes.sgpt.get_rag_service", return_value=fake_rag_service),
        patch("agent.routes.sgpt.run_llm_cli_command") as run,
    ):
        run.return_value = (0, "ok", "", "sgpt")
        res = client.post("/api/sgpt/execute", json={"prompt": "x", "use_hybrid_context": True}, headers=admin_auth_header)
    assert res.status_code == 200
    data = res.json["data"]
    assert data.get("trace_id")
    assert "grounding" in data
    assert "fallback" in data
