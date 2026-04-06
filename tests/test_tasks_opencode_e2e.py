import json
from unittest.mock import patch


def test_task_e2e_solved_via_opencode_glm5_default(client, app, admin_auth_header):
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

    cli_calls = []

    def _fake_run_llm_cli_command(prompt, options, timeout, backend, model, routing_policy, research_context=None, session=None, workdir=None, temperature=None):
        cli_calls.append(
            {
                "prompt": prompt,
                "options": options,
                "timeout": timeout,
                "backend": backend,
                "model": model,
                "routing_policy": routing_policy,
            }
        )
        return 0, '{"reason":"Nutze Shell","command":"echo solved"}', "", backend

    with patch("agent.routes.tasks.execution.run_llm_cli_command", side_effect=_fake_run_llm_cli_command):
        propose_res = client.post(
            f"/tasks/{tid}/step/propose",
            json={"prompt": "restart docker stack"},
            headers=admin_auth_header,
        )

    assert propose_res.status_code == 200
    pdata = propose_res.json["data"]
    assert pdata["backend"] == "opencode"
    command = pdata.get("command")
    if not command:
        command = str((json.loads(pdata["raw"]) or {}).get("command") or "")
    assert command == "echo solved"

    assert cli_calls
    assert cli_calls[0]["backend"] == "opencode"
    assert cli_calls[0]["model"] is None

    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        mock_exec.return_value = ("solved", 0)
        execute_res = client.post(f"/tasks/{tid}/step/execute", json={"command": command}, headers=admin_auth_header)

    assert execute_res.status_code == 200
    edata = execute_res.json["data"]
    assert edata["status"] == "completed"
    assert edata["exit_code"] == 0
    assert "solved" in edata["output"]
