from agent.repository import memory_entry_repo, task_repo, verification_record_repo, worker_job_repo
from agent.db_models import TaskDB, WorkerJobDB
from agent.services.result_memory_service import ResultMemoryService, normalize_result_memory_policy
from agent.services.verification_service import get_verification_service


def test_complete_task_persists_result_memory_entry(client, admin_auth_header):
    task_repo.save(
        TaskDB(
            id="memory-task-1",
            title="Summarize outcome",
            description="desc",
            status="assigned",
            goal_id="goal-memory-1",
            goal_trace_id="trace-memory-1",
            task_kind="analysis",
            current_worker_job_id="job-memory-1",
        )
    )
    worker_job_repo.save(
        WorkerJobDB(
            id="job-memory-1",
            parent_task_id="parent-memory",
            subtask_id="memory-task-1",
            worker_url="http://worker:5000",
            status="delegated",
        )
    )

    res = client.post(
        "/tasks/orchestration/complete",
        headers=admin_auth_header,
        json={
            "task_id": "memory-task-1",
            "actor": "http://worker:5000",
            "output": "Detailed result output for retrieval reuse",
            "gate_results": {"passed": True},
            "trace_id": "trace-memory-1",
        },
    )

    assert res.status_code == 200
    payload = res.get_json()["data"]
    memory_entry_id = payload["verification_status"]["memory_entry_id"]
    entry = memory_entry_repo.get_by_id(memory_entry_id)
    assert entry is not None
    assert entry.task_id == "memory-task-1"
    assert entry.goal_id == "goal-memory-1"
    assert entry.trace_id == "trace-memory-1"
    assert entry.worker_job_id == "job-memory-1"
    assert entry.summary.startswith("Detailed result output")
    assert "completed" in entry.retrieval_tags
    assert entry.artifact_refs[0]["kind"] == "task_output"
    assert (entry.memory_metadata or {}).get("compacted_summary") is not None
    assert (entry.memory_metadata or {}).get("memory_format") == "worker_result_compact_v3"
    assert isinstance(((entry.memory_metadata or {}).get("structured_summary") or {}).get("focus_terms"), list)
    assert isinstance((entry.memory_metadata or {}).get("retrieval_document"), str)
    assert isinstance((entry.memory_metadata or {}).get("bullet_points"), list)


def test_goal_detail_exposes_memory_entries(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda **kwargs: '[{"title":"Plan release","description":"Prepare release artifacts","priority":"High"}]',
    )
    create_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Deliver release"})
    goal_id = create_res.get_json()["data"]["goal"]["id"]
    task_id = create_res.get_json()["data"]["created_task_ids"][0]

    complete_res = client.post(
        "/tasks/orchestration/complete",
        headers=admin_auth_header,
        json={
            "task_id": task_id,
            "actor": "http://coder:5000",
            "gate_results": {"passed": True},
            "output": "Release notes ready",
            "trace_id": "goal-trace-for-memory",
        },
    )
    assert complete_res.status_code == 200

    detail_res = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
    assert detail_res.status_code == 200
    payload = detail_res.get_json()["data"]
    assert payload["artifacts"]["result_summary"]["memory_entries"] >= 1
    assert payload["artifacts"]["memory_entries"]
    assert payload["memory"]
    assert payload["memory"][0]["summary"].startswith("Release notes ready")


def test_result_memory_policy_clamps_invalid_values_and_can_disable_followup_artifact():
    policy = normalize_result_memory_policy(
        {
            "enabled": True,
            "create_followup_artifact": False,
            "retrieval_document_max_chars": 1,
            "raw_history_max_chars": 9999999,
        }
    )

    assert policy["create_followup_artifact"] is False
    assert policy["retrieval_document_max_chars"] == 400
    assert policy["raw_history_max_chars"] == 100000


def test_result_memory_handles_missing_optional_fields_without_silent_inconsistency():
    entry = ResultMemoryService().record_worker_result_memory(
        task_id=None,
        goal_id=None,
        trace_id=None,
        worker_job_id=None,
        title=None,
        output="",
        artifact_refs=None,
        retrieval_tags=None,
        metadata=None,
        policy={"create_followup_artifact": False},
    )

    saved = memory_entry_repo.get_by_id(entry.id)
    assert saved is not None
    assert saved.task_id is None
    assert saved.summary is None
    assert saved.artifact_refs == []
    assert saved.retrieval_tags == []
    assert (saved.memory_metadata or {}).get("followup_artifact") is None
    assert (saved.memory_metadata or {}).get("memory_format") == "worker_result_compact_v3"


def test_verification_missing_task_returns_none_and_duplicate_failures_update_single_record():
    service = get_verification_service()
    assert service.create_or_update_record("missing-task", trace_id="trace-missing", output="", exit_code=0) is None

    task_repo.save(TaskDB(id="memory-verify-dup", title="Verify duplicate", status="assigned", task_kind="coding"))
    first = service.create_or_update_record(
        "memory-verify-dup",
        trace_id="trace-dup",
        output="no evidence",
        exit_code=1,
        gate_results={"passed": False},
    )
    second = service.create_or_update_record(
        "memory-verify-dup",
        trace_id="trace-dup",
        output="still no evidence",
        exit_code=1,
        gate_results={"passed": False},
    )

    stored = verification_record_repo.get_by_task_id("memory-verify-dup")
    assert first is not None and second is not None
    assert first.id == second.id
    assert len(stored) == 1
    assert stored[0].retry_count == 2
