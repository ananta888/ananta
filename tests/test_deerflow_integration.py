from unittest.mock import patch


def test_deerflow_adapter_submit_status_and_fetch_result():
    from agent.research_backend import DeerFlowAdapter

    adapter = DeerFlowAdapter()
    with patch("agent.research_backend._execute_deerflow_cli", return_value=(0, "# Report\n\nhttps://example.com", "")):
        record = adapter.submit_job(prompt="research", task_id="T-DF-ADAPTER")

    status = adapter.get_job_status(record["job_id"])
    fetched = adapter.fetch_job_result(record["job_id"])
    assert status["status"] == "completed"
    assert fetched["result"]["returncode"] == 0
    assert fetched["artifact"]["kind"] == "research_report"
    assert fetched["artifact"]["backend_metadata"]["cli_result"]["job_id"] == record["job_id"]


def test_config_post_merges_research_backend_partial_update(client, admin_auth_header):
    first = {
        "research_backend": {
            "provider": "deerflow",
            "enabled": True,
            "mode": "cli",
            "command": "uv run main.py {prompt}",
            "working_dir": "/tmp/deer-flow",
        }
    }
    res1 = client.post("/config", json=first, headers=admin_auth_header)
    assert res1.status_code == 200

    second = {"research_backend": {"enabled": False}}
    res2 = client.post("/config", json=second, headers=admin_auth_header)
    assert res2.status_code == 200

    res3 = client.get("/config", headers=admin_auth_header)
    assert res3.status_code == 200
    summary = (((res3.json.get("data") or {}).get("settings") or {}).get("summary") or {}).get("llm") or {}
    research_backend = summary.get("research_backend") or {}
    assert research_backend.get("provider") == "deerflow"
    assert research_backend.get("enabled") is False
    assert research_backend.get("command") == "uv run main.py {prompt}"
    assert research_backend.get("working_dir") == "/tmp/deer-flow"


def test_sgpt_execute_deerflow_backend_returns_research_artifact(client):
    with patch("agent.routes.sgpt.run_llm_cli_command", return_value=(0, "# Report\n\nSee https://example.com", "", "deerflow")):
        response = client.post("/api/sgpt/execute", json={"prompt": "research market", "backend": "deerflow"})

    assert response.status_code == 200
    data = response.json["data"]
    assert data["backend"] == "deerflow"
    assert (data.get("trace") or {}).get("task_kind") == "research"
    artifact = data.get("research_artifact") or {}
    assert artifact.get("kind") == "research_report"
    assert artifact.get("report_markdown", "").startswith("# Report")
    assert artifact.get("sources")[0]["url"] == "https://example.com"


def test_sgpt_backends_endpoint_includes_deerflow_preflight(client):
    with patch(
        "agent.common.sgpt.get_research_backend_preflight",
        return_value={
            "deerflow": {
                "provider": "deerflow",
                "enabled": True,
                "configured": True,
                "mode": "cli",
                "command": "uv run main.py {prompt}",
                "binary_path": "/usr/bin/uv",
                "binary_available": True,
                "working_dir": "/tmp/deer-flow",
                "working_dir_exists": True,
                "timeout_seconds": 900,
                "result_format": "markdown",
                "install_hint": "hint",
            }
        },
    ):
        response = client.get("/api/sgpt/backends")

    assert response.status_code == 200
    preflight = response.json["data"]["preflight"]
    deerflow = (preflight.get("research_backends") or {}).get("deerflow") or {}
    assert deerflow.get("configured") is True
    assert deerflow.get("binary_available") is True
    assert deerflow.get("mode") == "cli"


def test_task_propose_routes_research_to_deerflow_and_persists_artifact(client, app):
    tid = "T-RESEARCH-1"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        cfg = app.config.get("AGENT_CONFIG", {}) or {}
        cfg["sgpt_routing"] = {
            "policy_version": "v2",
            "default_backend": "sgpt",
            "task_kind_backend": {"research": "deerflow"},
        }
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(tid, "assigned", description="research competitor landscape")

    with patch(
        "agent.routes.tasks.execution.run_llm_cli_command",
        return_value=(0, "# Research Report\n\nSource: https://example.com/a", "", "deerflow"),
    ):
        response = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "research competitor landscape"})

    assert response.status_code == 200
    data = response.json["data"]
    assert data["backend"] == "deerflow"
    assert (data.get("routing") or {}).get("task_kind") == "research"
    assert (data.get("trace") or {}).get("event_type") == "proposal_result"
    assert (data.get("review") or {}).get("status") == "pending"
    artifact = data.get("research_artifact") or {}
    assert artifact.get("kind") == "research_report"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        task = _get_local_task_status(tid)
        assert ((task.get("last_proposal") or {}).get("research_artifact") or {}).get("kind") == "research_report"
        assert ((task.get("last_proposal") or {}).get("review") or {}).get("required") is True


def test_task_execute_blocks_research_artifact_when_review_pending(client, app):
    tid = "T-RESEARCH-2"
    artifact = {
        "kind": "research_report",
        "summary": "summary",
        "report_markdown": "# Report\n\nBody",
        "sources": [{"title": "Example", "url": "https://example.com", "kind": "web", "confidence": 0.5}],
        "backend_metadata": {"backend": "deerflow"},
    }
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            tid,
            "proposing",
            description="research task",
            last_proposal={
                "backend": "deerflow",
                "reason": "summary",
                "research_artifact": artifact,
                "review": {"required": True, "status": "pending", "policy_version": "review-v1"},
                "routing": {"task_kind": "research", "reason": "task_kind_policy:research->deerflow"},
            },
        )

    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        response = client.post(f"/tasks/{tid}/step/execute", json={})

    assert response.status_code == 409
    assert response.json["message"] == "research_review_required"
    mock_exec.assert_not_called()


def test_task_review_endpoint_approves_research_artifact_and_execute_completes(client, app):
    tid = "T-RESEARCH-3"
    artifact = {
        "kind": "research_report",
        "summary": "summary",
        "report_markdown": "# Report\n\nBody",
        "sources": [{"title": "Example", "url": "https://example.com", "kind": "web", "confidence": 0.5}],
        "backend_metadata": {"backend": "deerflow"},
    }
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            tid,
            "proposing",
            description="research task",
            last_proposal={
                "backend": "deerflow",
                "reason": "summary",
                "research_artifact": artifact,
                "review": {"required": True, "status": "pending", "policy_version": "review-v1"},
                "routing": {"task_kind": "research", "reason": "task_kind_policy:research->deerflow"},
            },
        )

    review_res = client.post(f"/tasks/{tid}/review", json={"action": "approve", "comment": "looks good"})
    assert review_res.status_code == 200
    assert review_res.json["data"]["review"]["status"] == "approved"

    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        execute_res = client.post(f"/tasks/{tid}/step/execute", json={})

    assert execute_res.status_code == 200
    assert execute_res.json["data"]["status"] == "completed"
    assert execute_res.json["data"]["output"].startswith("# Report")
    assert (execute_res.json["data"].get("trace") or {}).get("event_type") == "execution_result"
    mock_exec.assert_not_called()


def test_sgpt_execute_auto_routes_research_prompt_to_deerflow(client, app):
    with app.app_context():
        cfg = app.config.get("AGENT_CONFIG", {}) or {}
        cfg["sgpt_routing"] = {
            "policy_version": "v3",
            "default_backend": "sgpt",
            "task_kind_backend": {"research": "deerflow"},
        }
        app.config["AGENT_CONFIG"] = cfg

    with patch("agent.routes.sgpt.run_llm_cli_command", return_value=(0, "# Report", "", "deerflow")):
        response = client.post("/api/sgpt/execute", json={"prompt": "research competitor landscape", "backend": "auto"})

    assert response.status_code == 200
    data = response.json["data"]
    assert data["backend"] == "deerflow"
    assert (data.get("routing") or {}).get("effective_backend") == "deerflow"
    assert (data.get("routing") or {}).get("policy_version") == "v3"
