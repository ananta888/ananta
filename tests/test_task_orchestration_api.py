def test_orchestration_ingest_and_read_model(client, auth_header):
    r = client.post(
        "/tasks/orchestration/ingest",
        json={"title": "T1", "description": "implement feature", "source": "ui", "created_by": "tester"},
        headers=auth_header,
    )
    assert r.status_code == 200
    task_id = r.json["data"]["id"]

    rm = client.get("/tasks/orchestration/read-model", headers=auth_header)
    assert rm.status_code == 200
    data = rm.json["data"]
    assert data["queue"]["todo"] >= 1
    assert data["by_source"]["ui"] >= 1
    assert any(t["id"] == task_id for t in data["recent_tasks"])
    assert "worker_execution_reconciliation" in data
    assert "artifact_flow" in data


def test_orchestration_claim_and_complete(client, auth_header):
    r = client.post(
        "/tasks/orchestration/ingest",
        json={"description": "fix bug", "source": "agent", "created_by": "alpha"},
        headers=auth_header,
    )
    tid = r.json["data"]["id"]

    claim = client.post(
        "/tasks/orchestration/claim",
        json={"task_id": tid, "agent_url": "http://alpha:5001", "lease_seconds": 60, "idempotency_key": "k1"},
        headers=auth_header,
    )
    assert claim.status_code == 200
    assert claim.json["data"]["claimed"] is True

    done = client.post(
        "/tasks/orchestration/complete",
        json={"task_id": tid, "actor": "http://alpha:5001", "gate_results": {"passed": True}, "trace_id": "tr-1"},
        headers=auth_header,
    )
    assert done.status_code == 200
    assert done.json["data"]["status"] == "completed"


def test_orchestration_ingest_uses_central_task_ingestion_fields(client, auth_header):
    dep = client.post(
        "/tasks/orchestration/ingest",
        json={
            "id": "DEP-1",
            "description": "dependency seed",
            "source": "agent",
            "created_by": "tester",
        },
        headers=auth_header,
    )
    assert dep.status_code == 200

    res = client.post(
        "/tasks/orchestration/ingest",
        json={
            "id": "ING-CENTRAL-1",
            "title": "Derived Task",
            "description": "central ingestion payload",
            "status": "created",
            "source": "agent",
            "created_by": "tester",
            "parent_task_id": "PARENT-1",
            "source_task_id": "SRC-1",
            "derivation_reason": "derived_from_parent",
            "derivation_depth": 2,
            "depends_on": ["DEP-1"],
            "required_capabilities": ["research"],
            "worker_execution_context": {"allowed_tools": ["list_teams"]},
        },
        headers=auth_header,
    )
    assert res.status_code == 200
    assert res.json["data"]["ingested"] is True

    task_res = client.get("/tasks/ING-CENTRAL-1", headers=auth_header)
    assert task_res.status_code == 200
    task = task_res.json["data"]
    assert task["parent_task_id"] == "PARENT-1"
    assert task["source_task_id"] == "SRC-1"
    assert task["derivation_reason"] == "derived_from_parent"
    assert task["derivation_depth"] == 2
    assert task["depends_on"] == ["DEP-1"]
    assert task["required_capabilities"] == ["research"]
    assert (task.get("worker_execution_context") or {}).get("allowed_tools") == ["list_teams"]
    ingested = next((item for item in (task.get("history") or []) if item.get("event_type") == "task_ingested"), None)
    assert ingested is not None
    assert (ingested.get("details") or {}).get("channel") == "central_task_management"


def test_orchestration_read_model_includes_artifact_flow_details(client, auth_header):
    from agent.db_models import ContextBundleDB, MemoryEntryDB, TaskDB, WorkerJobDB, WorkerResultDB
    from agent.repository import context_bundle_repo, memory_entry_repo, task_repo, worker_job_repo, worker_result_repo

    task = task_repo.save(
        TaskDB(
            id="FLOW-TASK-1",
            title="Flow Task",
            description="Validate sent and returned artifacts",
            status="in_progress",
        )
    )
    bundle = context_bundle_repo.save(
        ContextBundleDB(
            task_id=task.id,
            chunks=[{"metadata": {"artifact_id": "art-sent-1"}}],
        )
    )
    task.context_bundle_id = bundle.id
    task.current_worker_job_id = "FLOW-JOB-1"
    task_repo.save(task)
    job = worker_job_repo.save(
        WorkerJobDB(
            id="FLOW-JOB-1",
            parent_task_id=task.id,
            subtask_id=task.id,
            worker_url="http://alpha:5001",
            context_bundle_id=bundle.id,
            status="completed",
        )
    )
    worker_result_repo.save(
        WorkerResultDB(
            worker_job_id=job.id,
            task_id=task.id,
            worker_url=job.worker_url,
            status="completed",
            output="done",
        )
    )
    memory_entry_repo.save(
        MemoryEntryDB(
            task_id=task.id,
            worker_job_id=job.id,
            title="Worker Output",
            content="Produced artifact",
            artifact_refs=[{"kind": "generated_file", "artifact_id": "art-returned-1"}],
        )
    )

    res = client.get(
        "/tasks/orchestration/read-model?artifact_flow_rag_enabled=0&artifact_flow_max_tasks=5",
        headers=auth_header,
    )
    assert res.status_code == 200
    artifact_flow = (res.json.get("data") or {}).get("artifact_flow") or {}
    assert artifact_flow.get("enabled") is True
    assert (artifact_flow.get("config") or {}).get("rag_enabled") is False
    items = artifact_flow.get("items") or []
    target = next((item for item in items if item.get("task_id") == task.id), None)
    assert target is not None
    assert "art-sent-1" in (target.get("sent_artifact_ids") or [])
    assert "art-returned-1" in (target.get("returned_artifact_ids") or [])
    jobs = target.get("worker_jobs") or []
    assert jobs and jobs[0].get("worker_job_id") == "FLOW-JOB-1"
