from unittest.mock import MagicMock, patch


def test_task_e2e_solved_via_opencode_glm5_default(client, app):
    tid = "T-E2E-OPENCODE-GLM5"

    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["sgpt_routing"] = {
            "policy_version": "v2",
            "default_backend": "opencode",
            "task_kind_backend": {"ops": "opencode"},
        }
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(tid, "assigned", description="Bitte Docker Service neu starten")

    with (
        patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\opencode.cmd"),
        patch("subprocess.run") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"reason":"Nutze Shell","command":"echo solved"}'
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        propose_res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "restart docker stack"})

    assert propose_res.status_code == 200
    pdata = propose_res.json["data"]
    assert pdata["backend"] == "opencode"
    assert pdata["command"] == "echo solved"

    called_args = mock_run.call_args[0][0]
    assert called_args[0].endswith("opencode.cmd")
    assert called_args[1] == "run"
    assert "--model" in called_args
    model_index = called_args.index("--model")
    assert called_args[model_index + 1] == "opencode/glm-5-free"

    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        mock_exec.return_value = ("solved", 0)
        execute_res = client.post(f"/tasks/{tid}/step/execute", json={})

    assert execute_res.status_code == 200
    edata = execute_res.json["data"]
    assert edata["status"] == "completed"
    assert edata["exit_code"] == 0
    assert "solved" in edata["output"]
