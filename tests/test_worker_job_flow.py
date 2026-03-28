from agent.db_models import AgentInfoDB, ContextBundleDB, RetrievalRunDB, TaskDB, WorkerJobDB
from agent.config import settings
from agent.repository import context_bundle_repo, retrieval_run_repo, task_repo, worker_job_repo, worker_result_repo, agent_repo


class TestWorkerJobFlow:
    def test_delegate_persists_context_bundle_and_worker_job(self, client, admin_auth_header, monkeypatch):
        monkeypatch.setattr(settings, "role", "hub")
        agent_repo.save(
            AgentInfoDB(
                url="http://planner:5000",
                name="planner",
                role="worker",
                worker_roles=["planner"],
                capabilities=["planning"],
                status="online",
            )
        )
        task_repo.save(
            TaskDB(
                id="parent-job-1",
                title="Improve onboarding",
                description="Need implementation split",
                status="todo",
                team_id="team-a",
                goal_id="goal-1",
                goal_trace_id="goal-trace-1",
            )
        )

        class FakeWorkerJobService:
            def create_context_bundle(self, *, query, parent_task_id=None, goal_id=None):
                run = retrieval_run_repo.save(
                    RetrievalRunDB(
                        query=query,
                        task_id=parent_task_id,
                        goal_id=goal_id,
                        strategy={"repository_map": 1},
                        chunk_count=1,
                        token_estimate=12,
                        policy_version="v1",
                    )
                )
                return context_bundle_repo.save(
                    ContextBundleDB(
                        retrieval_run_id=run.id,
                        task_id=parent_task_id,
                        bundle_type="worker_execution_context",
                        context_text="selected worker context",
                        chunks=[{"engine": "repository_map", "source": "README.md", "content": "ctx", "score": 1.0, "metadata": {}}],
                        token_estimate=12,
                        bundle_metadata={"query": query, "policy_version": "v1"},
                    )
                )

            def create_worker_job(self, **kwargs):
                return worker_job_repo.save(
                    WorkerJobDB(
                        parent_task_id=kwargs["parent_task_id"],
                        subtask_id=kwargs["subtask_id"],
                        worker_url=kwargs["worker_url"],
                        context_bundle_id=kwargs["context_bundle_id"],
                        status="delegated",
                        allowed_tools=list(kwargs.get("allowed_tools") or []),
                        expected_output_schema=dict(kwargs.get("expected_output_schema") or {}),
                        job_metadata={"tooling_capabilities": {"sgpt": {"tool": "sgpt"}}},
                    )
                )

        monkeypatch.setattr("agent.routes.tasks.orchestration.get_worker_job_service", lambda: FakeWorkerJobService())
        monkeypatch.setattr(
            "agent.routes.tasks.orchestration._forward_to_worker",
            lambda worker_url, endpoint, data, token=None: {"status": "success", "data": {"accepted": True, "task_id": data["id"]}},
        )

        res = client.post(
            "/tasks/parent-job-1/delegate",
            headers=admin_auth_header,
            json={
                "subtask_description": "Create a concrete implementation plan",
                "task_kind": "planning",
                "required_capabilities": ["planning"],
                "allowed_tools": ["sgpt", "codex"],
                "expected_output_schema": {"type": "object", "required": ["summary"]},
            },
        )
        assert res.status_code == 200
        payload = res.get_json()["data"]

        bundle = context_bundle_repo.get_by_id(payload["context_bundle_id"])
        job = worker_job_repo.get_by_id(payload["worker_job_id"])
        task = task_repo.get_by_id("parent-job-1")
        run = retrieval_run_repo.get_by_id(bundle.retrieval_run_id)

        assert bundle is not None
        assert bundle.bundle_type == "worker_execution_context"
        assert bundle.context_text is not None
        assert run is not None
        assert run.task_id == "parent-job-1"

        assert job is not None
        assert job.parent_task_id == "parent-job-1"
        assert job.subtask_id == payload["subtask_id"]
        assert job.context_bundle_id == bundle.id
        assert job.allowed_tools == ["sgpt", "codex"]
        assert job.expected_output_schema["required"] == ["summary"]
        assert "sgpt" in job.job_metadata["tooling_capabilities"]

        assert task.context_bundle_id == bundle.id
        assert task.current_worker_job_id == job.id
        assert task.worker_execution_context["allowed_tools"] == ["sgpt", "codex"]
        assert task.worker_execution_context["context_bundle_id"] == bundle.id

    def test_complete_task_records_worker_result_for_current_job(self, client, admin_auth_header):
        task_repo.save(
            TaskDB(
                id="worker-result-task",
                title="Execute delegated work",
                description="desc",
                status="assigned",
                current_worker_job_id="job-current-1",
            )
        )
        worker_job_repo.save(
            WorkerJobDB(
                id="job-current-1",
                parent_task_id="parent-x",
                subtask_id="worker-result-task",
                worker_url="http://worker:5000",
                status="delegated",
            )
        )

        res = client.post(
            "/tasks/orchestration/complete",
            headers=admin_auth_header,
            json={
                "task_id": "worker-result-task",
                "actor": "http://worker:5000",
                "output": "done",
                "gate_results": {"passed": True},
                "trace_id": "trace-1",
            },
        )

        assert res.status_code == 200
        results = worker_result_repo.get_by_worker_job("job-current-1")
        assert len(results) == 1
        assert results[0].task_id == "worker-result-task"
        assert results[0].status == "completed"
        assert results[0].output == "done"

        job = worker_job_repo.get_by_id("job-current-1")
        assert job.status == "completed"
