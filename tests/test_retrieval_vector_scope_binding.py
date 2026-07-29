from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.hybrid_orchestrator import ContextChunk
from agent.models import TaskCreateRequest, TaskUpdateRequest
from agent.services import task_management_service as task_management_module
from agent.services.rag_service import RagService
from agent.services.retrieval_service import RetrievalService
from agent.services.retrieval_vector_runtime_scope_service import (
    RetrievalVectorRuntimeScope,
)
from agent.services.retrieval_vector_scope_binding_service import (
    RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY,
    RETRIEVAL_VECTOR_SCOPE_SCHEMA,
    DeploymentRetrievalVectorScopeBindingProvider,
    HubTaskRetrievalVectorScopeBinder,
    HubTaskRetrievalVectorScopeResolver,
    RetrievalVectorScopeBinding,
)
from agent.services.retrieval_vector_scope_ingress_policy import (
    RESERVED_RETRIEVAL_VECTOR_SCOPE_REASON,
    preserve_hub_retrieval_vector_scope,
)
from agent.services.task_management_service import TaskManagementService
from agent.services.worker_job_service import WorkerJobService


@dataclass
class _Task:
    id: str
    status: str = "todo"
    worker_execution_context: dict[str, Any] = field(default_factory=dict)
    verification_status: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0


class _TaskRepository:
    def __init__(self, *tasks: _Task) -> None:
        self.tasks = {task.id: task for task in tasks}
        self.save_calls = 0

    def get_by_id(self, task_id: str):
        return self.tasks.get(task_id)

    def save(self, task):
        self.tasks[task.id] = task
        self.save_calls += 1
        return task


class _MutationLocks:
    @contextmanager
    def mutation_locks(self, _task_ids):
        yield True


class _StaticBindingProvider:
    def __init__(self, binding: RetrievalVectorScopeBinding) -> None:
        self.binding = binding

    def binding_for_task(self, *, task_id, task):
        del task_id, task
        return self.binding


class _FakeContextManager:
    policy_version = "v1"

    def rerank(
        self,
        *,
        chunks,
        query,
        max_chunks,
        max_chars,
        max_tokens,
    ):
        del query, max_chars, max_tokens
        return list(chunks)[:max_chunks]

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, len(text.split()))


class _ScopedOrchestrator:
    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        self.context_manager = _FakeContextManager()

    @staticmethod
    def _redact(value: str) -> str:
        return value

    def get_relevant_context(
        self,
        query: str,
        *,
        domain_scope=None,
    ) -> dict[str, object]:
        del domain_scope
        return {
            "query": query,
            "strategy": {"repository_map": 1},
            "policy_version": "v1",
            "chunks": [
                {
                    "engine": "repository_map",
                    "source": f"{self.workspace_id}/README.md",
                    "content": f"repo:{self.workspace_id}",
                    "score": 1.0,
                    "metadata": {"source_type": "repo"},
                }
            ],
            "context_text": f"repo:{self.workspace_id}",
            "token_estimate": 1,
        }


class _Knowledge:
    def search(
        self,
        query,
        *,
        source_scopes,
        **_kwargs,
    ):
        del query
        if set(source_scopes or ()) != {"wiki"}:
            return []
        return [
            ContextChunk(
                engine="knowledge_index",
                source="wiki/trusted",
                content="wiki trusted",
                score=1.0,
                metadata={
                    "source_type": "wiki",
                    "record_id": "wiki-record",
                },
            )
        ]


class _Memory:
    @staticmethod
    def get_by_task(_task_id):
        return []

    @staticmethod
    def get_by_goal(_goal_id):
        return []


class _RuntimeResolver:
    def __init__(
        self,
        scope: RetrievalVectorRuntimeScope,
    ) -> None:
        self.scope = scope

    def cache_signature(self, **_kwargs):
        return (
            self.scope.workspace_id,
            self.scope.codecompass_repository_id,
            self.scope.wiki_source_id,
            self.scope.profile_name,
        )


class _RuntimeFactory:
    def __init__(self) -> None:
        self.codecompass_scopes: list[RetrievalVectorRuntimeScope] = []
        self.wiki_scopes: list[RetrievalVectorRuntimeScope] = []

    def codecompass_resolver(
        self,
        scope: RetrievalVectorRuntimeScope,
    ):
        self.codecompass_scopes.append(scope)
        return _RuntimeResolver(scope)

    def wiki_resolver(
        self,
        scope: RetrievalVectorRuntimeScope,
    ):
        self.wiki_scopes.append(scope)
        return _RuntimeResolver(scope)


class _WikiVectorRuntime:
    def __init__(self, scope: RetrievalVectorRuntimeScope) -> None:
        self.scope = scope

    @staticmethod
    def hybrid_search(_query, *, top_k):
        del top_k
        return [
            {
                "record_id": "wiki-record",
                "hybrid_score": 1.0,
            }
        ]


def _binding() -> RetrievalVectorScopeBinding:
    return RetrievalVectorScopeBinding(
        workspace_id="workspace-trusted",
        codecompass_repository_id="repo-trusted",
        wiki_source_id="wiki-trusted",
        profile_name="production",
    )


def _bound_context(
    task_id: str,
    binding: RetrievalVectorScopeBinding,
) -> dict[str, Any]:
    return {
        RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY: (
            binding.as_task_context(
                task_id=task_id,
                bound_at=1.0,
            )
        )
    }


def test_deployment_provider_builds_one_exact_combined_scope() -> None:
    provider = DeploymentRetrievalVectorScopeBindingProvider(
        environ={
            "ANANTA_CODECOMPASS_VECTOR_WORKSPACE_ID": "workspace-a",
            "ANANTA_CODECOMPASS_VECTOR_REPOSITORY_ID": "repo-a",
            "ANANTA_CODECOMPASS_VECTOR_PROFILE_NAME": "production",
            "ANANTA_WIKI_VECTOR_WORKSPACE_ID": "workspace-a",
            "ANANTA_WIKI_VECTOR_SOURCE_ID": "wiki-a",
            "ANANTA_WIKI_VECTOR_PROFILE_NAME": "production",
        }
    )

    binding = provider.binding_for_task(
        task_id="task-a",
        task=None,
    )

    assert binding is not None
    assert binding.as_runtime_scope() == RetrievalVectorRuntimeScope(
        workspace_id="workspace-a",
        codecompass_repository_id="repo-a",
        wiki_source_id="wiki-a",
        profile_name="production",
    )


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        (
            {
                "ANANTA_WIKI_VECTOR_WORKSPACE_ID": "workspace-b",
            },
            "retrieval_vector_scope_workspace_conflict",
        ),
        (
            {
                "ANANTA_WIKI_VECTOR_PROFILE_NAME": "wiki-profile",
            },
            "retrieval_vector_scope_profile_conflict",
        ),
    ],
)
def test_deployment_provider_rejects_ambiguous_combined_scope(
    override,
    reason,
) -> None:
    values = {
        "ANANTA_CODECOMPASS_VECTOR_WORKSPACE_ID": "workspace-a",
        "ANANTA_CODECOMPASS_VECTOR_REPOSITORY_ID": "repo-a",
        "ANANTA_CODECOMPASS_VECTOR_PROFILE_NAME": "production",
        "ANANTA_WIKI_VECTOR_WORKSPACE_ID": "workspace-a",
        "ANANTA_WIKI_VECTOR_SOURCE_ID": "wiki-a",
        "ANANTA_WIKI_VECTOR_PROFILE_NAME": "production",
        **override,
    }

    with pytest.raises(ValueError, match=f"^{reason}$"):
        DeploymentRetrievalVectorScopeBindingProvider(environ=values).binding_for_task(task_id="task-a", task=None)


def test_hub_binder_persists_idempotent_task_bound_scope() -> None:
    task = _Task(id="task-a")
    repository = _TaskRepository(task)
    binder = HubTaskRetrievalVectorScopeBinder(
        task_repository=repository,
        binding_provider=_StaticBindingProvider(_binding()),
        mutation_lock_port=_MutationLocks(),
    )
    resolver = HubTaskRetrievalVectorScopeResolver(task_repository=repository)

    first = binder.bind_task_scope("task-a")
    second = binder.bind_task_scope("task-a")

    assert first == second == _binding().as_runtime_scope()
    assert repository.save_calls == 1
    assert resolver.resolve_task_scope("task-a") == first
    context = task.worker_execution_context[RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY]
    assert context["schema"] == RETRIEVAL_VECTOR_SCOPE_SCHEMA
    assert context["bound_task_id"] == "task-a"


def test_resolver_keeps_vector_workspace_separate_from_execution_sandbox() -> None:
    binding = _binding()
    task = _Task(
        id="task-a",
        worker_execution_context=_bound_context(
            "task-a",
            binding,
        ),
        verification_status={
            "execution_scope": {
                "workspace_id": "workspace-attacker",
            }
        },
    )

    resolved = HubTaskRetrievalVectorScopeResolver(task_repository=_TaskRepository(task)).resolve_task_scope("task-a")

    assert resolved == binding.as_runtime_scope()


def test_worker_job_composition_reaches_codecompass_and_wiki_with_bound_scope(
    monkeypatch,
) -> None:
    binding = _binding()
    task = _Task(id="task-e2e")
    repository = _TaskRepository(task)
    binder = HubTaskRetrievalVectorScopeBinder(
        task_repository=repository,
        binding_provider=_StaticBindingProvider(binding),
        mutation_lock_port=_MutationLocks(),
    )
    scope_resolver = HubTaskRetrievalVectorScopeResolver(task_repository=repository)
    runtime_factory = _RuntimeFactory()
    retrieval = RetrievalService(
        knowledge_index_retrieval_service=_Knowledge(),
        memory_entry_repository=_Memory(),
        vector_runtime_resolver_factory=runtime_factory,
    )
    monkeypatch.setattr(
        retrieval,
        "_build_orchestrator",
        lambda resolver: _ScopedOrchestrator(resolver.scope.workspace_id),
    )

    def _build_wiki(**kwargs):
        return _WikiVectorRuntime(kwargs["runtime_resolver"].scope)

    monkeypatch.setattr(
        "agent.services.retrieval_service.build_wiki_retrieval_index_service",
        _build_wiki,
    )
    worker_jobs = WorkerJobService(
        rag_service=RagService(
            retrieval_service=retrieval,
            vector_scope_resolver=scope_resolver,
        ),
        retrieval_vector_scope_binder=binder,
    )

    repo_bundle = worker_jobs.create_context_bundle(
        query="repository architecture",
        parent_task_id="task-e2e",
        context_policy={"source_types": ["repo"]},
    )
    wiki_bundle = worker_jobs.create_context_bundle(
        query="wiki architecture",
        parent_task_id="task-e2e",
        context_policy={"source_types": ["wiki"]},
    )

    expected = binding.as_runtime_scope()
    assert repository.save_calls == 1
    assert runtime_factory.codecompass_scopes
    assert all(scope == expected for scope in runtime_factory.codecompass_scopes)
    assert runtime_factory.wiki_scopes
    assert all(scope == expected for scope in runtime_factory.wiki_scopes)
    assert any(chunk["source"].startswith("workspace-trusted/") for chunk in repo_bundle.chunks)
    assert any(chunk["source"] == "wiki/trusted" for chunk in wiki_bundle.chunks)


def test_task_bound_rag_rejects_supplied_cross_scope_and_payload_hints() -> None:
    binding = _binding()
    task = _Task(
        id="task-a",
        worker_execution_context=_bound_context(
            "task-a",
            binding,
        ),
    )
    resolver = HubTaskRetrievalVectorScopeResolver(task_repository=_TaskRepository(task))

    class _CaptureRetrieval:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def retrieve_context(self, query, **kwargs):
            self.kwargs = dict(kwargs)
            return {
                "query": query,
                "strategy": {},
                "policy_version": "v1",
                "chunks": [],
                "context_text": "",
                "token_estimate": 0,
            }

    retrieval = _CaptureRetrieval()
    rag = RagService(
        retrieval_service=retrieval,
        vector_scope_resolver=resolver,
    )
    attacker_scope = RetrievalVectorRuntimeScope(
        workspace_id="workspace-attacker",
        codecompass_repository_id="repo-attacker",
    )

    with pytest.raises(
        ValueError,
        match="^retrieval_vector_scope_task_mismatch$",
    ):
        rag.retrieve_context_bundle(
            "query",
            task_id="task-a",
            vector_runtime_scope=attacker_scope,
        )

    rag.retrieve_context_bundle(
        "query",
        task_id="task-a",
        retrieval_profile={
            "workspace_id": "workspace-attacker",
            "repository_id": "repo-attacker",
            "wiki_source_id": "wiki-attacker",
        },
    )
    assert retrieval.kwargs["vector_runtime_scope"] == binding.as_runtime_scope()

    with pytest.raises(
        ValueError,
        match="^retrieval_vector_scope_task_id_required$",
    ):
        rag.retrieve_context_bundle(
            "query",
            vector_runtime_scope=binding.as_runtime_scope(),
        )


@pytest.mark.parametrize("bound_at", [float("nan"), float("inf"), -1.0])
def test_resolver_rejects_invalid_binding_timestamp(bound_at) -> None:
    binding = _binding()
    context = binding.as_task_context(task_id="task-a", bound_at=1.0)
    context["bound_at"] = bound_at
    task = _Task(
        id="task-a",
        worker_execution_context={RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY: context},
    )

    with pytest.raises(
        ValueError,
        match="^retrieval_vector_scope_bound_at_invalid$",
    ):
        HubTaskRetrievalVectorScopeResolver(task_repository=_TaskRepository(task)).resolve_task_scope("task-a")


def test_external_task_create_rejects_reserved_scope_before_mutation(
    monkeypatch,
) -> None:
    def _unexpected(*_args, **_kwargs):
        pytest.fail("reserved scope reached a mutation dependency")

    monkeypatch.setattr(
        task_management_module,
        "get_repository_registry",
        _unexpected,
    )
    monkeypatch.setattr(
        task_management_module,
        "get_task_queue_service",
        _unexpected,
    )

    result = TaskManagementService().create_task(
        data=TaskCreateRequest(
            description="forged binding",
            worker_execution_context={RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY: {"workspace_id": "workspace-attacker"}},
        ),
        source="ui",
        created_by="external-user",
    )

    assert result["error"] == (RESERVED_RETRIEVAL_VECTOR_SCOPE_REASON)
    assert result["code"] == 403
    assert result["data"]["reserved_field"] == ("worker_execution_context.retrieval_vector_scope")


@pytest.mark.parametrize(
    "endpoint",
    [
        "/tasks",
        "/tasks/orchestration/ingest",
    ],
)
def test_external_task_routes_reject_reserved_scope(
    client,
    admin_auth_header,
    endpoint,
) -> None:
    response = client.post(
        endpoint,
        headers=admin_auth_header,
        json={
            "description": "forged binding",
            "worker_execution_context": {
                RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY: {
                    "workspace_id": "workspace-attacker",
                    "codecompass_repository_id": ("repo-attacker"),
                }
            },
        },
    )

    assert response.status_code == 403
    payload = response.get_json()
    assert payload["message"] == (RESERVED_RETRIEVAL_VECTOR_SCOPE_REASON)
    assert payload["data"]["reserved_field"] == ("worker_execution_context.retrieval_vector_scope")


def test_external_task_patch_rejects_reserved_scope_before_lookup(
    monkeypatch,
) -> None:
    def _unexpected(*_args, **_kwargs):
        pytest.fail("reserved scope reached task lookup")

    monkeypatch.setattr(
        task_management_module,
        "get_local_task_status",
        _unexpected,
    )

    result = TaskManagementService().patch_task(
        task_id="task-a",
        data=TaskUpdateRequest(
            worker_execution_context={RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY: {"workspace_id": "workspace-attacker"}}
        ),
    )

    assert result["error"] == (RESERVED_RETRIEVAL_VECTOR_SCOPE_REASON)
    assert result["code"] == 403


def test_external_context_patch_preserves_hub_binding() -> None:
    binding = _binding().as_task_context(
        task_id="task-a",
        bound_at=1.0,
    )
    update = {"worker_execution_context": {"instruction_context": {"profile_id": "profile-a"}}}

    preserve_hub_retrieval_vector_scope(
        existing_task={
            "worker_execution_context": {
                RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY: binding,
                "other": "old",
            }
        },
        update_data=update,
    )

    assert update["worker_execution_context"][RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY] == binding


def test_task_management_patch_preserves_existing_hub_binding(
    monkeypatch,
) -> None:
    binding = _binding().as_task_context(
        task_id="task-a",
        bound_at=1.0,
    )
    existing = {
        "id": "task-a",
        "status": "todo",
        "worker_execution_context": {
            RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY: binding,
        },
    }
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        task_management_module,
        "get_local_task_status",
        lambda _task_id: existing,
    )
    monkeypatch.setattr(
        task_management_module,
        "update_local_task_status",
        lambda _task_id, _status, **kwargs: captured.update(kwargs),
    )
    service = TaskManagementService()
    monkeypatch.setattr(
        service,
        "actor_username",
        lambda: "external-user",
    )

    result = service.patch_task(
        task_id="task-a",
        data=TaskUpdateRequest(worker_execution_context={"note": "safe"}),
    )

    assert result["data"]["status"] == "updated"
    context = captured["worker_execution_context"]
    assert context["note"] == "safe"
    assert context[RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY] == binding
