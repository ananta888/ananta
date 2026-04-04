import json
import os
from unittest.mock import patch


def test_task_specific_endpoints_path(client, app, admin_auth_header):
    """Verifiziert, dass die neuen Task-spezifischen Endpunkte erreichbar sind."""

    tid = "T-123456"

    # Wir müssen sicherstellen, dass der Task in der lokalen "Datenbank" existiert
    # Da wir in Tests oft In-Memory oder Mock-Pfade nutzen, schauen wir uns _update_local_task_status an.
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(tid, "assigned", assigned_to="test-agent")

    # 1. Propose auf dem neuen Pfad
    with patch("agent.routes.tasks.execution.run_llm_cli_command") as mock_cli:
        mock_cli.return_value = (0, '{"reason": "Test", "command": "echo hello"}', "", "aider")
        response = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "test"}, headers=admin_auth_header)
        assert response.status_code == 200
        assert response.json["data"]["command"] == "echo hello"
        assert response.json["data"]["backend"] == "aider"
        assert response.json["data"]["pipeline"]["pipeline"] == "task_propose"
        assert response.json["data"]["routing"]["effective_backend"] in {"aider", "sgpt", "codex", "opencode", "mistral_code"}
        assert response.json["data"]["routing"]["execution_backend"] in {"aider", "sgpt", "codex", "opencode", "mistral_code"}
        assert "inference_provider" in response.json["data"]["routing"]
        with app.app_context():
            from agent.routes.tasks.utils import _get_local_task_status

            t = _get_local_task_status(tid)
            assert t is not None
            lp = t.get("last_proposal") or {}
            assert lp.get("backend") == "aider"
            assert isinstance(lp.get("cli_result", {}).get("latency_ms"), int)
            assert any((h.get("event_type") == "proposal_result") for h in (t.get("history") or []))

    # 2. Execute auf dem neuen Pfad
    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        mock_exec.return_value = ("hello", 0)
        # Wir müssen ein last_proposal im Task haben, damit execute funktioniert
        with app.app_context():
            _update_local_task_status(tid, "proposing", last_proposal={"command": "echo hello", "reason": "Test"})

        response = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)
        assert response.status_code == 200
        assert response.json["data"]["output"] == "hello"
        assert response.json["data"]["cost_summary"]["cost_units"] >= 0
        assert "execution_backend" in response.json["data"]["cost_summary"]
        assert response.json["data"]["pipeline"]["pipeline"] == "task_execute"
        with app.app_context():
            from agent.routes.tasks.utils import _get_local_task_status

            t = _get_local_task_status(tid)
            assert t is not None
            hist = t.get("history") or []
            execution_events = [h for h in hist if h.get("event_type") == "execution_result"]
            assert execution_events
            assert execution_events[-1]["cost_summary"]["tokens_total"] > 0
            assert ((t.get("verification_status") or {}).get("execution_routing") or {}).get("execution_backend")


def test_task_specific_endpoints_old_path_fail(client, admin_auth_header):
    """Verifiziert, dass die alten Pfade nicht mehr funktionieren (404)."""
    tid = "T-123456"

    response = client.post(f"/tasks/{tid}/propose", json={}, headers=admin_auth_header)
    assert response.status_code == 404

    response = client.post(f"/tasks/{tid}/execute", json={}, headers=admin_auth_header)
    assert response.status_code == 404


def test_task_unassign(client, app, admin_auth_header):
    """Verifiziert den Unassign-Endpunkt."""
    tid = "T-UNASSIGN"

    # 1. Task erstellen und zuweisen
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        _update_local_task_status(tid, "assigned", assigned_agent_url="http://agent-1:5000")

        task = _get_local_task_status(tid)
        assert task["status"] == "assigned"
        assert task["assigned_agent_url"] == "http://agent-1:5000"

    # 2. Unassign aufrufen
    response = client.post(f"/tasks/{tid}/unassign", headers=admin_auth_header)
    assert response.status_code == 200
    assert response.json["data"]["status"] == "todo"

    # 3. Status prüfen
    with app.app_context():
        task = _get_local_task_status(tid)
        assert task["status"] == "todo"
        # In JSON wird None zu null, was in Python wieder None ist oder der Key fehlt (falls wir ihn löschen würden)
        # _update_local_task_status nutzt .update(), also bleibt der Key mit Wert None
        assert task.get("assigned_agent_url") is None


def test_create_followups_deduplicates(client, app, admin_auth_header):
    tid = "T-FOLLOWUP"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        _update_local_task_status(tid, "in_progress", description="parent")

    payload = {
        "items": [
            {"description": "Implement API endpoint", "priority": "High"},
            {"description": "Implement   API endpoint", "priority": "High"},
            {"description": "Write tests", "priority": "Medium"},
        ]
    }
    response = client.post(f"/tasks/{tid}/followups", json=payload, headers=admin_auth_header)
    assert response.status_code == 200
    data = response.json["data"]
    assert len(data["created"]) == 2
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["reason"] == "duplicate"
    assert all(entry["status"] == "blocked" for entry in data["created"])

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        for entry in data["created"]:
            task = _get_local_task_status(entry["id"])
            assert task["parent_task_id"] == tid
            assert task["status"] == "blocked"


def test_tasks_cleanup_archives_by_status(client, app, admin_auth_header):
    with app.app_context():
        from agent.repository import archived_task_repo, task_repo
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status("CLN-1", "completed", description="old completed")
        _update_local_task_status("CLN-2", "failed", description="old failed")
        _update_local_task_status("CLN-3", "todo", description="keep todo")
        assert task_repo.get_by_id("CLN-1") is not None
        assert archived_task_repo.get_by_id("CLN-1") is None

    res = client.post("/tasks/cleanup", json={"mode": "archive", "statuses": ["completed", "failed"]}, headers=admin_auth_header)
    assert res.status_code == 200
    data = res.json["data"]
    assert data["matched_count"] >= 2
    assert data["archived_count"] >= 2

    with app.app_context():
        from agent.repository import archived_task_repo, task_repo

        assert task_repo.get_by_id("CLN-1") is None
        assert task_repo.get_by_id("CLN-2") is None
        assert task_repo.get_by_id("CLN-3") is not None
        assert archived_task_repo.get_by_id("CLN-1") is not None
        assert archived_task_repo.get_by_id("CLN-2") is not None


def test_task_tree_returns_nested_children(client, app, admin_auth_header):
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status("TREE-ROOT", "todo", description="root")
        _update_local_task_status("TREE-C1", "todo", description="c1", parent_task_id="TREE-ROOT")
        _update_local_task_status("TREE-C2", "todo", description="c2", parent_task_id="TREE-C1")

    res = client.get("/tasks/TREE-ROOT/tree?include_archived=0&max_depth=10", headers=admin_auth_header)
    assert res.status_code == 200
    data = res.json["data"]
    tree = data["tree"]
    assert tree["task"]["id"] == "TREE-ROOT"
    assert tree["children_count"] == 1
    assert len(tree["children"]) == 1
    child = tree["children"][0]
    assert child["task"]["id"] == "TREE-C1"
    assert child["children_count"] == 1
    grandchild = child["children"][0]
    assert grandchild["task"]["id"] == "TREE-C2"


def test_task_interventions_pause_resume_cancel_retry(client, app, admin_auth_header):
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        _update_local_task_status("INT-1", "in_progress", description="active task")
        _update_local_task_status("INT-2", "failed", description="failed task", last_exit_code=1)

    pause_res = client.post("/tasks/INT-1/pause", headers=admin_auth_header)
    assert pause_res.status_code == 200
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        t1 = _get_local_task_status("INT-1")
        assert t1["status"] == "paused"
        assert any((h.get("event_type") == "task_intervention") for h in (t1.get("history") or []))

    resume_res = client.post("/tasks/INT-1/resume", headers=admin_auth_header)
    assert resume_res.status_code == 200
    with app.app_context():
        t1 = _get_local_task_status("INT-1")
        assert t1["status"] in {"todo", "assigned"}

    cancel_res = client.post("/tasks/INT-1/cancel", headers=admin_auth_header)
    assert cancel_res.status_code == 200
    with app.app_context():
        t1 = _get_local_task_status("INT-1")
        assert t1["status"] == "cancelled"

    retry_res = client.post("/tasks/INT-2/retry", headers=admin_auth_header)
    assert retry_res.status_code == 200
    with app.app_context():
        t2 = _get_local_task_status("INT-2")
        assert t2["status"] in {"todo", "assigned"}
        assert t2.get("last_exit_code") is None


def test_task_interventions_invalid_transition(client, app, admin_auth_header):
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status("INT-3", "completed", description="done")

    res = client.post("/tasks/INT-3/pause", headers=admin_auth_header)
    assert res.status_code == 400
    assert res.json["message"] == "invalid_transition"


def test_archive_batch_and_restore_batch(client, app, admin_auth_header):
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status("BATCH-A1", "completed", team_id="team-arch")
        _update_local_task_status("BATCH-A2", "failed", team_id="team-arch")
        _update_local_task_status("BATCH-A3", "todo", team_id="team-keep")

    archive_res = client.post("/tasks/archive/batch", json={"team_id": "team-arch"}, headers=admin_auth_header)
    assert archive_res.status_code == 200
    assert archive_res.json["data"]["archived_count"] >= 2

    restore_res = client.post("/tasks/archived/restore/batch", json={"task_ids": ["BATCH-A1"]}, headers=admin_auth_header)
    assert restore_res.status_code == 200
    assert "BATCH-A1" in (restore_res.json["data"]["restored_ids"] or [])


def test_archive_retention_apply(client, app, admin_auth_header):
    with app.app_context():
        from agent.db_models import ArchivedTaskDB
        from agent.repository import archived_task_repo

        archived_task_repo.save(
            ArchivedTaskDB(id="RET-OLD", status="completed", created_at=1.0, updated_at=1.0, archived_at=1.0)
        )
        archived_task_repo.save(
            ArchivedTaskDB(
                id="RET-NEW",
                status="completed",
                created_at=1.0,
                updated_at=1.0,
                archived_at=99999999999.0,
            )
        )

    res = client.post("/tasks/archive/retention/apply", json={"retain_seconds": 60}, headers=admin_auth_header)
    assert res.status_code == 200
    assert "RET-OLD" in (res.json["data"]["deleted_ids"] or [])


def test_delete_archived_task_and_cleanup_batch(client, app, admin_auth_header):
    with app.app_context():
        from agent.db_models import ArchivedTaskDB
        from agent.repository import archived_task_repo

        archived_task_repo.save(
            ArchivedTaskDB(id="ARCH-DEL-1", status="completed", created_at=1.0, updated_at=1.0, archived_at=1.0)
        )
        archived_task_repo.save(
            ArchivedTaskDB(id="ARCH-DEL-2", status="failed", created_at=1.0, updated_at=1.0, archived_at=1.0)
        )

    delete_res = client.delete("/tasks/archived/ARCH-DEL-1", headers=admin_auth_header)
    assert delete_res.status_code == 200
    assert "ARCH-DEL-1" in (delete_res.json["data"]["deleted_ids"] or [])

    cleanup_res = client.post("/tasks/archived/cleanup", json={"task_ids": ["ARCH-DEL-2"]}, headers=admin_auth_header)
    assert cleanup_res.status_code == 200
    assert "ARCH-DEL-2" in (cleanup_res.json["data"]["deleted_ids"] or [])

    missing_filter_res = client.post("/tasks/archived/cleanup", json={}, headers=admin_auth_header)
    assert missing_filter_res.status_code == 400


def test_task_derivation_backfill(client, app, admin_auth_header):
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        _update_local_task_status("DRV-P", "todo")
        _update_local_task_status("DRV-C", "todo", parent_task_id="DRV-P")

    res = client.post("/tasks/derivation/backfill", headers=admin_auth_header)
    assert res.status_code == 200
    assert "DRV-C" in (res.json["data"]["updated_ids"] or [])

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        child = _get_local_task_status("DRV-C")
        assert child.get("source_task_id") == "DRV-P"
        assert int(child.get("derivation_depth") or 0) >= 1


def test_autopilot_unblocks_child_when_parent_completed(app, monkeypatch):
    from agent.config import settings
    from agent.routes.tasks.autopilot import autonomous_loop
    from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

    monkeypatch.setattr(settings, "role", "hub")
    with app.app_context():
        _update_local_task_status("PARENT-1", "completed", description="parent done")
        _update_local_task_status("CHILD-1", "blocked", description="child", parent_task_id="PARENT-1")
        res = autonomous_loop.tick_once()
        child = _get_local_task_status("CHILD-1")

    assert res["reason"] in {"ok", "no_online_workers", "no_available_workers", "no_candidates"}
    assert child["status"] in {"todo", "failed", "assigned", "completed"}
    assert child["status"] != "blocked"


def test_tasks_timeline_endpoint_filters_and_errors(client, app, admin_auth_header):
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            "TL-1",
            "failed",
            team_id="team-a",
            assigned_agent_url="http://worker-1:5000",
            last_output="[quality_gate] failed: missing_coding_quality_markers",
            last_exit_code=1,
            history=[
                {
                    "event_type": "autopilot_decision",
                    "timestamp": 10,
                    "reason": "because",
                    "delegated_to": "http://worker-1:5000",
                },
                {"event_type": "autopilot_result", "timestamp": 11, "status": "failed", "exit_code": 1},
            ],
        )
        _update_local_task_status(
            "TL-GR",
            "failed",
            team_id="team-a",
            assigned_agent_url="http://worker-1:5000",
            history=[
                {
                    "event_type": "tool_guardrail_blocked",
                    "timestamp": 13,
                    "blocked_tools": ["create_team"],
                    "blocked_reasons": ["guardrail_class_limit_exceeded:write"],
                    "reason": "tool_guardrail_blocked",
                }
            ],
        )
        _update_local_task_status(
            "TL-SPB",
            "failed",
            team_id="team-a",
            assigned_agent_url="http://worker-1:5000",
            history=[
                {
                    "event_type": "autopilot_security_policy_blocked",
                    "timestamp": 14,
                    "blocked_reasons": ["guardrail_class_limit_exceeded:write"],
                    "blocked_tools": ["create_team"],
                    "security_level": "safe",
                }
            ],
        )
        _update_local_task_status(
            "TL-WF",
            "failed",
            team_id="team-a",
            assigned_agent_url="http://worker-1:5000",
            history=[
                {
                    "event_type": "autopilot_worker_failed",
                    "timestamp": 15,
                    "reason": "worker_forward_failed:http://worker-1:5000",
                }
            ],
        )
        _update_local_task_status(
            "TL-2",
            "completed",
            team_id="team-b",
            history=[{"event_type": "autopilot_result", "timestamp": 12, "status": "completed"}],
        )

    res = client.get("/tasks/timeline?team_id=team-a&error_only=1&limit=50", headers=admin_auth_header)
    assert res.status_code == 200
    data = res.json["data"]
    assert isinstance(data["items"], list)
    assert data["total"] >= 1
    assert all(item["team_id"] == "team-a" for item in data["items"])
    assert any(item["event_type"] in {"execution_result", "autopilot_result"} for item in data["items"])
    assert any(
        item["event_type"] == "tool_guardrail_blocked"
        and "guardrail_class_limit_exceeded:write" in (item.get("details", {}).get("blocked_reasons") or [])
        for item in data["items"]
    )
    assert any(item["event_type"] == "autopilot_security_policy_blocked" for item in data["items"])
    assert any(item["event_type"] == "autopilot_worker_failed" for item in data["items"])
    assert all(item["event_type"] != "task_created" for item in data["items"])


def test_task_dependencies_cycle_rejected(client, app, admin_auth_header):
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status("D-A", "todo")
        _update_local_task_status("D-B", "todo", depends_on=["D-A"])

    res = client.patch("/tasks/D-A", json={"depends_on": ["D-B"]}, headers=admin_auth_header)
    assert res.status_code == 400
    assert res.json["message"] == "dependency_cycle_detected"


def test_task_propose_forwarding_unwraps_nested_data(client, app, admin_auth_header):
    tid = "T-FWD-PROPOSE"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            tid,
            "assigned",
            assigned_agent_url="http://worker-x:5001",
            assigned_agent_token="tok",
            description="forward test",
        )

    with patch("agent.routes.tasks.execution._forward_to_worker") as mock_fwd:
        mock_fwd.return_value = {"status": "success", "data": {"data": {"command": "echo hi", "reason": "ok"}}}
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "x"}, headers=admin_auth_header)
        assert res.status_code == 200
        assert res.json["data"]["command"] == "echo hi"


def test_task_execute_forwarding_unwraps_nested_data(client, app, admin_auth_header):
    tid = "T-FWD-EXEC"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        _update_local_task_status(
            tid,
            "assigned",
            assigned_agent_url="http://worker-y:5001",
            assigned_agent_token="tok",
            description="forward exec",
        )
        before = _get_local_task_status(tid)
        assert before is not None

    with patch("agent.routes.tasks.execution._forward_to_worker") as mock_fwd:
        mock_fwd.return_value = {
            "status": "success",
            "data": {"data": {"status": "completed", "output": "ok", "exit_code": 0}},
        }
        res = client.post(f"/tasks/{tid}/step/execute", json={"command": "echo hi"}, headers=admin_auth_header)
        assert res.status_code == 200
        assert res.json["data"]["status"] == "completed"


def test_task_execute_forwarding_failure_uses_retryable_domain_error(client, app, admin_auth_header):
    tid = "T-FWD-EXEC-FAIL"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            tid,
            "assigned",
            assigned_agent_url="http://worker-z:5001",
            assigned_agent_token="tok",
            description="forward exec fail",
        )

    with patch("agent.routes.tasks.execution._forward_to_worker", side_effect=RuntimeError("worker offline")):
        res = client.post(f"/tasks/{tid}/step/execute", json={"command": "echo hi"}, headers=admin_auth_header)

    assert res.status_code == 502
    assert res.json["message"] == "forwarding_failed"
    assert res.json["data"]["retryable"] is True
    assert res.json["data"]["details"]["worker_url"] == "http://worker-z:5001"


def test_task_execute_retries_retryable_exit_codes_and_reports_failure_type(client, app, admin_auth_header):
    tid = "T-EXEC-RETRY"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["command_retries"] = 2
        cfg["command_retry_delay"] = 0
        cfg["command_retryable_exit_codes"] = [5]
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(
            tid,
            "proposing",
            description="retry shell command",
            last_proposal={"command": "echo retry", "reason": "retry"},
        )
        before = _get_local_task_status(tid)
        assert before is not None

    with patch("agent.shell.PersistentShell.execute", side_effect=[("bad", 5), ("ok", 0)]):
        res = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)

    assert res.status_code == 200
    data = res.json["data"]
    assert data["status"] == "completed"
    assert data["output"] == "ok"
    assert data["retries_used"] == 1
    assert data["failure_type"] == "success"
    assert data["execution_policy"]["retryable_exit_codes"] == [5]

    with app.app_context():
        task = _get_local_task_status(tid)
        latest = (task.get("history") or [])[-1]
        assert latest["retries_used"] == 1
        assert latest["failure_type"] == "success"


def test_task_execute_timeout_can_disable_retry(client, app, admin_auth_header):
    tid = "T-EXEC-TIMEOUT"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["command_retries"] = 3
        cfg["command_retry_delay"] = 0
        cfg["command_retry_on_timeouts"] = False
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(
            tid,
            "proposing",
            description="timeout command",
            last_proposal={"command": "sleep 999", "reason": "timeout"},
        )
        before = _get_local_task_status(tid)
        assert before is not None

    with patch("agent.shell.PersistentShell.execute", return_value=("[Error: Timeout]", -1)) as mock_exec:
        res = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)

    assert res.status_code == 200
    data = res.json["data"]
    assert data["status"] == "failed"
    assert data["exit_code"] == -1
    assert data["retries_used"] == 0
    assert data["failure_type"] == "timeout"
    assert mock_exec.call_count == 1

    with app.app_context():
        task = _get_local_task_status(tid)
        latest = (task.get("history") or [])[-1]
        assert latest["failure_type"] == "timeout"
        assert latest["retries_used"] == 0


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

    def _fake_run_llm_cli_command(prompt, options, timeout, backend, model, routing_policy):
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

    def _fake_run_llm_cli_command(prompt, options, timeout, backend, model, routing_policy):
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

    def _fake_run_llm_cli_command(prompt, options, timeout, backend, model, routing_policy, research_context=None):
        calls["count"] += 1
        if calls["count"] == 1:
            assert model == "primary-model-a"
            return 1, "", "", "sgpt"
        if calls["count"] == 2:
            assert "Repariere die Antwort" in prompt
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

    def _fake_cli(prompt, options, timeout, backend, model, routing_policy):
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

    def _fake_cli(prompt, options, timeout, backend, model, routing_policy, research_context=None):
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
    assert len(prompts) == 2
    assert "Turn 1 Assistant" in prompts[1]
    assert '"reason":"turn-1"' in prompts[1]

    with app.app_context():
        task = _get_local_task_status(tid)
        cli_session_meta = ((task.get("verification_status") or {}).get("cli_session") or {})
        assert cli_session_meta.get("session_id") == first_routing.get("session_id")
