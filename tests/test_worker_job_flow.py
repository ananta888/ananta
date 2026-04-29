from agent.db_models import AgentInfoDB, ContextBundleDB, RetrievalRunDB, TaskDB, WorkerJobDB
from agent.config import settings
from agent.repository import context_bundle_repo, retrieval_run_repo, task_repo, worker_job_repo, worker_result_repo, agent_repo
from agent.services.worker_job_service import WorkerJobService


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
            def create_context_bundle(self, *, query, parent_task_id=None, goal_id=None, context_policy=None):
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
                        context_text="" if (context_policy or {}).get("include_context_text") is False else "selected worker context",
                        chunks=[{"engine": "repository_map", "source": "README.md", "content": "ctx", "score": 1.0, "metadata": {}}],
                        token_estimate=12,
                        bundle_metadata={"query": query, "policy_version": "v1", "context_policy": dict(context_policy or {})},
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
                        job_metadata={
                            "tooling_capabilities": {"sgpt": {"tool": "sgpt"}},
                            **dict(kwargs.get("metadata") or {}),
                        },
                    )
                )

        class FakeWorkerContractService:
            def build_routing_decision(
                self,
                *,
                agent_url,
                selected_by_policy,
                task_kind,
                required_capabilities,
                selection=None,
                preferred_backend=None,
            ):
                return {
                    "worker_url": agent_url,
                    "selected_by_policy": selected_by_policy,
                    "strategy": getattr(selection, "strategy", "manual_override"),
                    "reasons": list(getattr(selection, "reasons", []) or []),
                    "matched_capabilities": list(getattr(selection, "matched_capabilities", []) or []),
                    "matched_roles": list(getattr(selection, "matched_roles", []) or []),
                    "task_kind": task_kind,
                    "required_capabilities": list(required_capabilities or []),
                    "preferred_backend": preferred_backend,
                }

            def build_execution_context(
                self,
                *,
                instructions,
                context_bundle,
                context_policy,
                workspace,
                artifact_sync,
                allowed_tools,
                expected_output_schema,
                routing_decision,
            ):
                return {
                    "version": "v1",
                    "kind": "worker_execution_context",
                    "instructions": instructions,
                    "context_bundle_id": context_bundle.id,
                    "context": {
                        "context_text": context_bundle.context_text,
                        "chunks": list(context_bundle.chunks or []),
                        "token_estimate": int(context_bundle.token_estimate or 0),
                        "bundle_metadata": dict(context_bundle.bundle_metadata or {}),
                    },
                    "context_policy": dict(context_policy or {}),
                    "workspace": dict(workspace or {}),
                    "artifact_sync": dict(artifact_sync or {}),
                    "allowed_tools": list(allowed_tools or []),
                    "expected_output_schema": dict(expected_output_schema or {}),
                    "routing": dict(routing_decision or {}),
                }

            def build_job_metadata(self, *, routing_decision, task_kind, required_capabilities, context_policy=None, extra_metadata=None):
                return {
                    **dict(extra_metadata or {}),
                    "routing_decision": dict(routing_decision or {}),
                    "task_kind": task_kind,
                    "required_capabilities": list(required_capabilities or []),
                    "context_policy": dict(context_policy or {}),
                }

            def build_worker_todo_contract(self, **kwargs):
                return {
                    "schema": "worker_todo_contract.v1",
                    "task_id": kwargs["task_id"],
                    "goal_id": kwargs["goal_id"],
                    "trace_id": kwargs["trace_id"],
                    "worker": {
                        "executor_kind": kwargs["executor_kind"],
                        "worker_profile": kwargs["worker_profile"],
                        "profile_source": kwargs["profile_source"],
                    },
                    "todo": {
                        "version": kwargs.get("todo_version", "1.0"),
                        "track": kwargs.get("track", "worker_subplan"),
                        "tasks": list(kwargs.get("tasks") or []),
                    },
                    "execution": {
                        "mode": kwargs.get("mode", "assistant_execute"),
                        "allowed_tools": list(kwargs.get("allowed_tools") or []),
                        "enforce_artifacts": bool(kwargs.get("enforce_artifacts", True)),
                        "max_steps": int(kwargs.get("max_steps") or 20),
                    },
                    "control_manifest": {
                        "trace_id": kwargs["trace_id"],
                        "capability_id": kwargs["capability_id"],
                        "context_hash": kwargs["context_hash"],
                    },
                    "expected_result_schema": "worker_todo_result.v1",
                }

        class FakeAgentRegistryService:
            def build_directory_entry(self, *, agent, timeout, now=None):
                return {
                    **agent.model_dump(),
                    "available_for_routing": True,
                    "liveness": {"status": agent.status, "available_for_routing": True},
                }

        forwarded_payload = {}

        monkeypatch.setattr(
            "agent.routes.tasks.orchestration._services",
            lambda: type(
                "Svc",
                (),
                {
                    "task_orchestration_service": __import__(
                        "agent.services.task_orchestration_service", fromlist=["task_orchestration_service"]
                    ).task_orchestration_service,
                    "worker_job_service": FakeWorkerJobService(),
                    "worker_contract_service": FakeWorkerContractService(),
                    "agent_registry_service": FakeAgentRegistryService(),
                    "task_runtime_service": type(
                        "TaskRuntimeStub",
                        (),
                        {
                            "get_local_task_status": staticmethod(
                                __import__("agent.services.task_runtime_service", fromlist=["get_local_task_status"]).get_local_task_status
                            )
                        },
                    )(),
                    "result_memory_service": type(
                        "ResultMemoryStub",
                        (),
                        {"record_worker_result_memory": staticmethod(lambda **_kwargs: None)},
                    )(),
                    "verification_service": type(
                        "VerificationStub",
                        (),
                        {"create_or_update_record": staticmethod(lambda *args, **kwargs: None)},
                    )(),
                },
            )(),
        )
        monkeypatch.setattr(
            "agent.services.task_orchestration_service.forward_to_worker",
            lambda worker_url, endpoint, data, token=None: (
                forwarded_payload.update({"worker_url": worker_url, "endpoint": endpoint, "data": dict(data), "token": token})
                or {"status": "success", "data": {"accepted": True, "task_id": data["id"]}}
            ),
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
        assert bundle.bundle_metadata["context_policy"]["mode"] == "full"
        assert bundle.bundle_metadata["context_policy"]["retrieval_intent"] == "execution_focused_context"
        assert bundle.bundle_metadata["context_policy"]["required_context_scope"] == "task_and_direct_neighbors"
        assert bundle.bundle_metadata["context_policy"]["preferred_bundle_mode"] == "standard"
        assert run is not None
        assert run.task_id == "parent-job-1"

        assert job is not None
        assert job.parent_task_id == "parent-job-1"
        assert job.subtask_id == payload["subtask_id"]
        assert job.context_bundle_id == bundle.id
        assert job.allowed_tools == ["sgpt", "codex"]
        assert job.expected_output_schema["required"] == ["summary"]
        assert "sgpt" in job.job_metadata["tooling_capabilities"]
        assert job.job_metadata["routing_decision"]["selected_by_policy"] is True
        assert job.job_metadata["routing_decision"]["matched_capabilities"] == ["planning"]
        assert job.job_metadata["context_policy"]["mode"] == "full"

        assert task.context_bundle_id == bundle.id
        assert task.current_worker_job_id == job.id
        assert task.worker_execution_context["allowed_tools"] == ["sgpt", "codex"]
        assert task.worker_execution_context["context_bundle_id"] == bundle.id
        assert task.worker_execution_context["kind"] == "worker_execution_context"
        assert task.worker_execution_context["version"] == "v1"
        assert task.worker_execution_context["todo_contract"]["schema"] == "worker_todo_contract.v1"
        assert task.worker_execution_context["todo_contract_generation"]["enabled"] is True
        assert task.worker_execution_context["context_policy"]["mode"] == "full"
        assert task.worker_execution_context["routing"]["matched_roles"] == ["planner"]
        workspace_meta = task.worker_execution_context["workspace"]
        assert workspace_meta["scope_mode"] == "goal_worker"
        assert workspace_meta["scope_key"].endswith(":goal-1")
        assert workspace_meta["session_scope_kind"] == "workspace"
        assert workspace_meta["session_scope_key"].startswith("workspace:")
        assert payload["retrieval_hints"]["retrieval_intent"] == "execution_focused_context"
        assert payload["retrieval_hints"]["required_context_scope"] == "task_and_direct_neighbors"
        assert payload["retrieval_hints"]["preferred_bundle_mode"] == "standard"
        assert (payload.get("workspace_scope") or {}).get("mode") == "goal_worker"
        assert str((payload.get("workspace_scope") or {}).get("scope_key") or "").endswith(":goal-1")
        assert "task_neighborhood" in payload
        assert isinstance(payload["task_neighborhood"]["neighbor_task_ids"], list)
        assert forwarded_payload["data"]["retrieval_intent"] == "execution_focused_context"
        assert forwarded_payload["data"]["required_context_scope"] == "task_and_direct_neighbors"
        assert forwarded_payload["data"]["preferred_bundle_mode"] == "standard"
        assert isinstance((forwarded_payload["data"]["context_bundle_policy"] or {}).get("neighbor_task_ids"), list)

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


def test_worker_job_service_persists_selection_trace_and_budget_metadata():
    class FakeRagService:
        def retrieve_context_bundle(self, *_args, **_kwargs):
            return {
                "bundle_type": "retrieval_context",
                "policy_version": "v1",
                "chunks": [
                    {"engine": "knowledge_index", "source": "docs/abc.md", "content": "ctx", "score": 2.1, "metadata": {}}
                ],
                "token_estimate": 17,
                "strategy": {"fusion": {"candidate_counts": {"all": 3, "final": 1}}},
                "context_policy": {"mode": "standard", "window_profile": "standard_32k"},
                "budget": {"total_tokens": 32000, "retrieval_utilization": 0.4},
                "why_this_context": {"summary": "task_kind=implement | selected_chunks=1 | mode=standard"},
                "explainability": {"engines": ["knowledge_index"], "sources": [{"source": "docs/abc.md", "score": 2.1}]},
                "selection_trace": {
                    "fusion": {"candidate_counts": {"all": 3, "final": 1}},
                    "knowledge_index_reason": "query_overlap",
                    "result_memory_reason": "neighbor_task_match",
                },
            }

    service = WorkerJobService(rag_service=FakeRagService())
    bundle = service.create_context_bundle(
        query="implement worker flow",
        parent_task_id="T-parent",
        goal_id="G-1",
        context_policy={"mode": "standard", "task_kind": "implement", "window_profile": "standard_32k"},
    )
    metadata = dict(bundle.bundle_metadata or {})
    assert (metadata.get("selection_trace") or {}).get("knowledge_index_reason") == "query_overlap"
    assert (metadata.get("selection_trace") or {}).get("result_memory_reason") == "neighbor_task_match"
    assert (metadata.get("budget") or {}).get("total_tokens") == 32000

    run = retrieval_run_repo.get_by_id(bundle.retrieval_run_id)
    assert run is not None
    assert (run.run_metadata or {}).get("window_profile") == "standard_32k"
    assert (run.run_metadata or {}).get("total_budget_tokens") == 32000
