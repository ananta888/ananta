import json
import os
import types
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_snake_chat_background_threads(monkeypatch):
    monkeypatch.setattr("agent.routes.snakes._spawn_ai_chat_reply", lambda **kwargs: None)


@pytest.fixture(autouse=True)
def _disable_llm_context_compaction(monkeypatch):
    class _NoopCompactor:
        def compact(self, **kwargs):
            return types.SimpleNamespace(payload={}, meta={"status": "disabled"})

    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.get_planning_context_compactor_service",
        lambda: _NoopCompactor(),
    )


@pytest.fixture(autouse=True)
def _enable_legacy_cli_step_path(app):
    cfg = dict(app.config.get("AGENT_CONFIG") or {})
    task_scoped_execution = dict(cfg.get("task_scoped_execution") or {})
    task_scoped_execution["allow_legacy_single_step_path"] = True
    cfg["task_scoped_execution"] = task_scoped_execution
    app.config["AGENT_CONFIG"] = cfg


@pytest.fixture
def force_hub_role(monkeypatch):
    monkeypatch.setattr("agent.config.settings.role", "hub")


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
        pipeline = response.json["data"].get("pipeline") or {}
        if pipeline:
            assert pipeline.get("pipeline") == "task_propose"
        routing = response.json["data"].get("routing") or {}
        if routing:
            assert routing["effective_backend"] in {"aider", "sgpt", "codex", "opencode", "mistral_code", "ananta-worker"}
            assert routing["execution_backend"] in {"aider", "sgpt", "codex", "opencode", "mistral_code"}
            assert "inference_provider" in routing
        with app.app_context():
            from agent.routes.tasks.utils import _get_local_task_status

            t = _get_local_task_status(tid)
            assert t is not None
            lp = t.get("last_proposal") or {}
            assert lp.get("backend") == "aider"
            latency_ms = lp.get("cli_result", {}).get("latency_ms")
            assert latency_ms is None or isinstance(latency_ms, int)
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
            execution_routing = ((t.get("verification_status") or {}).get("execution_routing") or {})
            if execution_routing:
                assert execution_routing.get("execution_backend")


def test_task_propose_records_native_worker_runtime_path_when_enabled(client, app, admin_auth_header):
    tid = "T-NATIVE-PROPOSE"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        worker_runtime = dict(cfg.get("worker_runtime") or {})
        native_cfg = dict(worker_runtime.get("native_worker_runtime") or {})
        native_cfg["enabled"] = True
        worker_runtime["native_worker_runtime"] = native_cfg
        worker_runtime["semantic_output_correction"] = {
            "enabled": True,
            "similarity_threshold": 0.88,
            "embedding_provider": {"provider": "local", "dimensions": 12},
            "fields": {"risk_classification": {"enabled": True, "candidates": ["low", "medium", "high", "critical"]}},
        }
        cfg["worker_runtime"] = worker_runtime
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(
            tid,
            "assigned",
            description="Plan native worker command",
            worker_execution_context={
                "context_bundle_id": "ctx-native-propose",
                "context": {"context_text": "native context", "chunks": [], "token_estimate": 12, "bundle_metadata": {}},
                "allowed_tools": [],
                "expected_output_schema": {},
                "worker_profile": "balanced",
                "profile_source": "agent_default",
            },
        )
        assert _get_local_task_status(tid) is not None

    with patch("agent.routes.tasks.execution.run_llm_cli_command") as mock_cli:
        mock_cli.return_value = (0, '{"reason":"native plan","command":"python -c \\"print(1)\\""}', "", "ananta-worker")
        response = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "run bounded diagnostics"}, headers=admin_auth_header)

    assert response.status_code == 200
    payload = response.json["data"]
    routing = payload.get("routing") or {}
    if routing:
        assert routing.get("worker_runtime_path") == "native_worker_pipeline"

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        task = _get_local_task_status(tid)
        proposal = dict(task.get("last_proposal") or {})
        native_runtime = dict((proposal.get("worker_context") or {}).get("native_runtime") or {})
        semantic_policy = dict((proposal.get("worker_context") or {}).get("semantic_output_correction") or {})
        if native_runtime:
            assert native_runtime.get("runtime_path") == "native_worker_pipeline"
            assert ((native_runtime.get("command_plan_artifact") or {}).get("schema")) == "command_plan_artifact.v1"
        if semantic_policy:
            assert semantic_policy.get("enabled") is True
            assert dict(semantic_policy.get("embedding_provider") or {}).get("provider") == "local"


def test_task_execute_uses_native_worker_pipeline_without_shell_proxy(client, app, admin_auth_header, tmp_path):
    tid = "T-NATIVE-EXECUTE"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status
        from worker.shell.command_planner import build_command_plan_artifact

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["worker_runtime"] = {
            **dict(cfg.get("worker_runtime") or {}),
            "workspace_root": str(tmp_path),
            "native_worker_runtime": {
                **dict(((cfg.get("worker_runtime") or {}).get("native_worker_runtime") or {})),
                "enabled": True,
            },
        }
        app.config["AGENT_CONFIG"] = cfg
        command_plan = build_command_plan_artifact(
            task_id=tid,
            capability_id="worker.command.plan",
            command="echo 7",
            explanation="native test",
            expected_effects=["print output"],
            policy={"allowlist": ["echo"], "approval_required_commands": ["rm"], "denylist_tokens": ["rm -rf /"]},
            hub_policy_decision="allow",
            execution_profile="balanced",
        )
        _update_local_task_status(
            tid,
            "proposing",
            description="Native execute",
            last_proposal={
                "reason": "native command",
                "command": "echo 7",
                "backend": "ananta-worker",
                "routing": {
                    "task_kind": "ops",
                    "reason": "native_worker",
                    "worker_runtime_path": "native_worker_pipeline",
                    "worker_profile": "balanced",
                    "profile_source": "agent_default",
                    "policy_classification_summary": "safe:command_allowlisted",
                },
                "trace": {"trace_id": "trace-native-exec", "policy_version": "v1"},
                "worker_context": {
                    "worker_profile": "balanced",
                    "profile_source": "agent_default",
                    "native_runtime": {
                        "runtime_path": "native_worker_pipeline",
                        "context_hash": "ctx-native-exec",
                        "command_plan_artifact": command_plan,
                    },
                },
            },
            worker_execution_context={
                "workspace": {
                    "task_id": tid,
                    "scope_key": "scope-native-exec",
                    "worker_job_id": "job-native-exec",
                }
            },
        )
        assert _get_local_task_status(tid) is not None

    with patch("agent.shell.PersistentShell.execute") as shell_exec:
        response = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)
        assert shell_exec.call_count == 0

    assert response.status_code == 200
    payload = response.json["data"]
    assert payload["status"] in {"completed", "failed", "blocked"}
    assert payload["failure_type"] in {
        "success",
        "command_failure",
        "schema_invalid",
        "approval_required",
        "policy_denied",
        "unsafe_command",
        "runtime_failure",
    }
    assert ("native_worker_runtime" in payload["output"]) or ("degraded" in payload["output"])

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        task = _get_local_task_status(tid)
        latest = (task.get("history") or [])[-1]
        repair_meta = dict(latest.get("execution_repair") or {})
        if repair_meta:
            assert repair_meta.get("runtime_path") == "native_worker_pipeline"
        metrics = ((task.get("verification_status") or {}).get("task_flow_metrics") or {})
        if metrics:
            assert metrics.get("worker_profile") == "balanced"
            assert metrics.get("profile_source") == "agent_default"


def test_task_specific_endpoints_old_path_fail(client, admin_auth_header):
    """Verifiziert, dass die alten Pfade nicht mehr funktionieren (404)."""
    tid = "T-123456"

    response = client.post(f"/tasks/{tid}/propose", json={}, headers=admin_auth_header)
    assert response.status_code == 404

    response = client.post(f"/tasks/{tid}/execute", json={}, headers=admin_auth_header)
    assert response.status_code == 404


def test_task_execute_auto_repairs_meta_blocked_command(client, app, admin_auth_header):
    tid = "T-AUTO-REPAIR"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        _update_local_task_status(
            tid,
            "proposing",
            description="Create a tiny workspace scaffold.",
            last_proposal={
                "reason": "Initial proposal",
                "command": "echo start || echo fallback",
                "backend": "opencode",
                "model": "gpt-4o-mini",
                "routing": {"effective_backend": "opencode", "task_kind": "implementation"},
            },
        )
        assert _get_local_task_status(tid) is not None

    with patch("agent.routes.tasks.execution.run_llm_cli_command") as mock_cli:
        with patch(
            "agent.services.task_scoped_execution_service.TaskScopedExecutionService._build_task_propose_prompt",
            return_value=("repair prompt", {}),
        ):
            with patch(
                "agent.services.task_scoped_execution_service.TaskScopedExecutionService._prepare_task_cli_session",
                return_value=None,
            ):
                with patch("agent.shell.PersistentShell.execute") as mock_exec:
                    mock_cli.return_value = (0, '{"reason":"repair","command":"echo repaired"}', "", "opencode")
                    mock_exec.return_value = ("repaired", 0)
                    response = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)

    assert response.status_code == 200
    assert response.json["data"]["status"] == "completed"
    assert response.json["data"]["exit_code"] == 0
    assert response.json["data"]["output"] == "repaired"
    assert mock_exec.call_count == 1

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        task = _get_local_task_status(tid)
        assert task is not None
        history = task.get("history") or []
        execution_events = [entry for entry in history if entry.get("event_type") == "execution_result"]
        assert execution_events
        repair_meta = execution_events[-1].get("execution_repair") or {}
        if repair_meta:
            assert repair_meta.get("attempted") is True
            assert repair_meta.get("trigger") == "shell_meta_character_blocked"


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
    assert all(entry["status"] in {"blocked", "blocked_by_dependency"} for entry in data["created"])

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        for entry in data["created"]:
            task = _get_local_task_status(entry["id"])
            assert task["parent_task_id"] == tid
            assert task["status"] in {"blocked", "blocked_by_dependency"}


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


def test_task_workspace_files_endpoint_returns_worker_workspace_listing(client, app, admin_auth_header):
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status
        from agent.services.worker_workspace_service import get_worker_workspace_service

        _update_local_task_status(
            "WS-1",
            "in_progress",
            worker_execution_context={
                "workspace": {
                    "task_id": "subtask-ws-1",
                    "scope_key": "scope-ws-1",
                    "worker_job_id": "job-ws-1",
                }
            },
        )
        task = _get_local_task_status("WS-1")
        workspace = get_worker_workspace_service().resolve_workspace_context(task=task)
        (workspace.workspace_dir / "src").mkdir(parents=True, exist_ok=True)
        (workspace.workspace_dir / "src" / "main.ts").write_text("export const ready = true;\n", encoding="utf-8")
        (workspace.workspace_dir / ".ananta").mkdir(parents=True, exist_ok=True)
        (workspace.workspace_dir / ".ananta" / "internal.txt").write_text("hidden\n", encoding="utf-8")

    response = client.get("/tasks/WS-1/workspace/files", headers=admin_auth_header)
    assert response.status_code == 200
    files = response.json["data"]["workspace"]["files"]
    paths = [item["relative_path"] for item in files]
    assert "src/main.ts" in paths
    assert ".ananta/internal.txt" not in paths

    untracked_response = client.get("/tasks/WS-1/workspace/files?tracked_only=0", headers=admin_auth_header)
    assert untracked_response.status_code == 200
    untracked_files = untracked_response.json["data"]["workspace"]["files"]
    untracked_paths = [item["relative_path"] for item in untracked_files]
    assert ".ananta/internal.txt" in untracked_paths


def test_task_workspace_files_endpoint_requires_admin(client, app, user_auth_header):
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status("WS-NONADMIN", "todo")

    response = client.get("/tasks/WS-NONADMIN/workspace/files", headers=user_auth_header)
    assert response.status_code == 403


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


def test_task_propose_forwarding_unwraps_nested_data(client, app, admin_auth_header, force_hub_role):
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


def test_task_propose_forwarding_persists_terminal_metadata_without_command(client, app, admin_auth_header, force_hub_role):
    tid = "T-FWD-PROPOSE-TERM"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        _update_local_task_status(
            tid,
            "assigned",
            assigned_agent_url="http://worker-x:5001",
            assigned_agent_token="tok",
            description="forward terminal metadata",
        )
        assert _get_local_task_status(tid) is not None

    with patch("agent.routes.tasks.execution._forward_to_worker") as mock_fwd:
        mock_fwd.return_value = {
            "status": "success",
            "data": {
                "data": {
                    "reason": "invalid proposal but live terminal exists",
                    "raw": '{"summary":"broken"}',
                    "backend": "opencode",
                    "model": "ananta-default",
                    "routing": {
                        "effective_backend": "opencode",
                        "reason": "task_kind_model_overrides",
                        "execution_mode": "interactive_terminal",
                        "live_terminal": {
                            "forward_param": "cli-forward-123",
                            "agent_url": "http://worker-x:5001",
                            "agent_name": "worker-x",
                        },
                    },
                    "cli_result": {"returncode": 0, "output_source": "live_terminal"},
                }
            },
        }
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "x"}, headers=admin_auth_header)
        assert res.status_code == 200
        assert (res.json["data"].get("routing") or {}).get("live_terminal", {}).get("forward_param") == "cli-forward-123"

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        task = _get_local_task_status(tid)
        assert task is not None
        assert task["status"] == "proposing"
        assert ((task.get("last_proposal") or {}).get("routing") or {}).get("live_terminal", {}).get("forward_param") == "cli-forward-123"
        history = list(task.get("history") or [])
        assert history and history[-1]["event_type"] == "proposal_result"


def test_task_execute_forwarding_unwraps_nested_data(client, app, admin_auth_header, force_hub_role):
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


def test_task_execute_forwarding_persists_execution_artifacts_and_provenance(client, app, admin_auth_header, force_hub_role):
    tid = "T-FWD-EXEC-RICH"
    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status

        _update_local_task_status(
            tid,
            "assigned",
            assigned_agent_url="http://worker-y:5001",
            assigned_agent_token="tok",
            description="forward exec rich",
        )
        before = _get_local_task_status(tid)
        assert before is not None

    with patch("agent.routes.tasks.execution._forward_to_worker") as mock_fwd:
        mock_fwd.return_value = {
            "status": "success",
            "data": {
                "data": {
                    "status": "completed",
                    "output": "ok",
                    "exit_code": 0,
                    "artifacts": [{"artifact_id": "artifact-1", "title": "patch.diff"}],
                    "execution_scope": {"workspace_id": "ws-1", "lifecycle_status": "completed"},
                    "execution_provenance": {"execution_mode": "remote_worker", "worker_url": "http://worker-y:5001"},
                    "review": {"summary": "done"},
                }
            },
        }
        res = client.post(f"/tasks/{tid}/step/execute", json={"command": "echo hi"}, headers=admin_auth_header)
        assert res.status_code == 200
        assert res.json["data"]["status"] == "completed"

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status

        task = _get_local_task_status(tid)
        assert task is not None
        assert task["last_output"] == "ok"
        assert task["last_exit_code"] == 0
        verification = dict(task.get("verification_status") or {})
        assert verification["execution_scope"]["workspace_id"] == "ws-1"
        assert verification["execution_provenance"]["execution_mode"] == "remote_worker"
        assert verification["execution_artifacts"][0]["artifact_id"] == "artifact-1"
        assert verification["execution_review"]["summary"] == "done"
        history = list(task.get("history") or [])
        assert history
        assert history[-1]["artifacts"][0]["artifact_id"] == "artifact-1"


def test_task_execute_forwarding_failure_uses_retryable_domain_error(client, app, admin_auth_header, force_hub_role):
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
        if "retries_used" in latest:
            assert latest["retries_used"] == 1
        if "failure_type" in latest:
            assert latest["failure_type"] == "success"
        loop_signals = list(latest.get("loop_signals") or [])
        if loop_signals:
            assert loop_signals[0]["failure_type"] != "success"
            assert loop_signals[-1]["failure_type"] == "success"
        assert latest.get("loop_detection") is None
        approval_decision = dict(latest.get("approval_decision") or {})
        if approval_decision:
            assert approval_decision.get("classification") in {"allow", "confirm_required"}


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
        loop_signals = list(latest.get("loop_signals") or [])
        assert len(loop_signals) == 1
        assert loop_signals[0]["failure_type"] == "timeout"
        approval_decision = dict(latest.get("approval_decision") or {})
        assert approval_decision.get("classification") in {"allow", "confirm_required"}
