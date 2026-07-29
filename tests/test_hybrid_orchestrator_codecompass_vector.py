from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import hybrid_orchestrator
from agent.config import settings
from agent.hybrid_orchestrator import HybridOrchestrator
from agent.services.codecompass_vector_runtime_service import (
    HUB_DIRECT_QDRANT_READ_EXECUTION,
    HubCodeCompassVectorRuntimeResolver,
    build_default_codecompass_vector_runtime_resolver,
)
from agent.services.vector_store_rollout_service import (
    InMemoryVectorStoreRolloutStore,
    VectorStoreRolloutService,
)
from worker.retrieval.vector_store_config import (
    VectorStoreConfig,
    VectorStoreProvider,
)
from worker.retrieval.vector_store_contract import (
    VectorScope,
    VectorStoreError,
)
from worker.retrieval.vector_store_factory import VectorStoreFactory


class _FakeVectorService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._diagnostic = {"status": "ready", "reason": "fixture"}
        self.close_calls = 0

    def search(self, *, query: str, top_k: int = 10, allowed_paths: list[str] | None = None):
        self.calls.append({"query": query, "top_k": top_k, "allowed_paths": allowed_paths})
        rows = [
            {
                "engine": "codecompass_vector",
                "source": "src/vector_payment.py",
                "content": "payment timeout retry vector candidate",
                "score": 0.9,
                "metadata": {
                    "record_id": "emb-1",
                    "record_kind": "python_function",
                    "file": "src/vector_payment.py",
                    "vector_score": 0.9,
                    "model_name": "hash-v1",
                    "source_manifest_hash": "mh-1",
                },
            }
        ]
        if allowed_paths == []:
            return []
        if allowed_paths:
            return [row for row in rows if any(row["source"].startswith(f"{path}/") for path in allowed_paths)]
        return rows

    def last_diagnostic(self):
        return dict(self._diagnostic)

    def close(self) -> None:
        self.close_calls += 1


class _StaticVectorStoreFactory:
    def __init__(self) -> None:
        self.store = object()

    def create(self, *_args, **_kwargs):
        return self.store


def test_context_manager_route_includes_codecompass_vector_quotas(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_route_quota_codecompass_vector", 5, raising=False)
    monkeypatch.setattr(settings, "rag_route_quota_codecompass_vector_default", 2, raising=False)
    monkeypatch.setattr(settings, "rag_route_quota_codecompass_vector_docs", 1, raising=False)

    manager = hybrid_orchestrator.ContextManager()

    assert manager.route("python service bug")["codecompass_vector"] == 5
    assert manager.route("plain request")["codecompass_vector"] == 2
    assert manager.route("documentation readme")["codecompass_vector"] == 1


def test_hybrid_orchestrator_collects_codecompass_vector_chunks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_route_quota_codecompass_vector", 3, raising=False)
    service = _FakeVectorService()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "payment.py").write_text("class PaymentService: pass\n", encoding="utf-8")

    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[],
        codecompass_vector_service=service,
        max_context_chars=4000,
    )
    result = orchestrator.get_relevant_context("python payment timeout service")

    assert service.calls
    assert service.calls[0]["top_k"] == 3
    assert any(chunk["engine"] == "codecompass_vector" for chunk in result["chunks"])
    assert result["retrieval_diagnostics"]["codecompass_vector"]["status"] == "ready"


def test_codecompass_vector_quota_zero_does_not_call_service(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_route_quota_codecompass_vector", 0, raising=False)
    monkeypatch.setattr(settings, "rag_route_quota_codecompass_vector_default", 0, raising=False)
    service = _FakeVectorService()
    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[],
        codecompass_vector_service=service,
    )

    orchestrator.get_relevant_context("python service bug")

    assert service.calls == []


def test_hybrid_orchestrator_close_delegates_once(
    tmp_path: Path,
) -> None:
    service = _FakeVectorService()
    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[],
        codecompass_vector_service=service,
    )

    orchestrator.close()
    orchestrator.close()

    assert service.close_calls == 1


def test_hybrid_orchestrator_injects_hub_resolved_vector_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import agent.services.codecompass_vector_retrieval_service as service_module

    captured: dict[str, object] = {}
    task_service = object()
    scope = VectorScope(
        "workspace-a",
        "repo-a",
        "default",
        "codecompass",
    )
    config = VectorStoreConfig.from_mapping({"provider": "qdrant", "qdrant": {}})

    class RuntimeResolver:
        def resolve(self, *, repo_root):
            captured["repo_root"] = repo_root
            factory = _StaticVectorStoreFactory()
            captured["factory"] = factory
            return SimpleNamespace(
                vector_store_config=config,
                trusted_scope=scope,
                index_task_service=task_service,
                secret_resolver=object(),
                vector_store_factory=factory,
                observer=object(),
                read_execution=HUB_DIRECT_QDRANT_READ_EXECUTION,
            )

    class Service:
        def __init__(self, **kwargs):
            captured["service_kwargs"] = kwargs

        def last_diagnostic(self):
            return {"status": "ready", "reason": "fixture"}

    monkeypatch.setattr(settings, "codecompass_vector_enabled", True)
    monkeypatch.setattr(
        service_module,
        "CodeCompassVectorRetrievalService",
        Service,
    )

    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[],
        codecompass_vector_runtime_resolver=RuntimeResolver(),
    )

    kwargs = captured["service_kwargs"]
    assert captured["repo_root"] == tmp_path.resolve()
    assert kwargs["vector_store_config"] == config
    assert kwargs["trusted_scope"] == scope
    assert kwargs["index_task_service"] is task_service
    assert kwargs["vector_store_observer"] is not None
    assert kwargs["vector_search_port"] is captured["factory"].store
    assert kwargs["vector_index_writer"] is captured["factory"].store
    assert orchestrator.codecompass_vector_service is not None


def test_hybrid_orchestrator_fails_closed_for_unscoped_qdrant_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import agent.services.codecompass_vector_retrieval_service as service_module

    calls: list[dict] = []
    config = VectorStoreConfig.from_mapping({"provider": "qdrant", "qdrant": {}})

    class RuntimeResolver:
        def resolve(self, *, repo_root):
            del repo_root
            return SimpleNamespace(
                vector_store_config=config,
                trusted_scope=None,
                index_task_service=object(),
                secret_resolver=None,
            )

    class Service:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(settings, "codecompass_vector_enabled", True)
    monkeypatch.setattr(
        service_module,
        "CodeCompassVectorRetrievalService",
        Service,
    )

    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[],
        codecompass_vector_runtime_resolver=RuntimeResolver(),
    )

    assert calls == []
    assert orchestrator.codecompass_vector_service is None
    assert orchestrator._retrieval_diagnostics()["codecompass_vector"] == {
        "status": "degraded",
        "reason": "vector_scope_required",
    }


def test_qdrant_runtime_constructor_failure_degrades_without_secret_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import agent.services.codecompass_vector_retrieval_service as service_module

    config = VectorStoreConfig.from_mapping({"provider": "qdrant", "qdrant": {}})

    class RuntimeResolver:
        def resolve(self, *, repo_root):
            del repo_root
            return SimpleNamespace(
                vector_store_config=config,
                trusted_scope=VectorScope(
                    "workspace-a",
                    "repo-a",
                    "default",
                    "codecompass",
                ),
                index_task_service=object(),
                secret_resolver=object(),
                vector_store_factory=_StaticVectorStoreFactory(),
                observer=object(),
                read_execution=HUB_DIRECT_QDRANT_READ_EXECUTION,
            )

    class Service:
        def __init__(self, **_kwargs):
            raise RuntimeError("private-api-key-value")

    monkeypatch.setattr(settings, "codecompass_vector_enabled", True)
    monkeypatch.setattr(
        service_module,
        "CodeCompassVectorRetrievalService",
        Service,
    )

    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[],
        codecompass_vector_runtime_resolver=RuntimeResolver(),
    )

    diagnostic = orchestrator._retrieval_diagnostics()["codecompass_vector"]
    assert orchestrator.codecompass_vector_service is None
    assert diagnostic["reason"] == "vector_runtime_initialization_failed"
    assert "private-api-key-value" not in str(diagnostic)


def test_qdrant_fail_fast_runtime_initialization_reaches_product_caller(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = VectorStoreConfig.from_mapping(
        {
            "provider": "qdrant",
            "availability": {"on_unavailable": "fail_fast"},
            "qdrant": {},
        }
    )

    class FailingFactory:
        def create(self, *_args, **_kwargs):
            raise VectorStoreError("qdrant_unavailable")

    class RuntimeResolver:
        def resolve(self, *, repo_root):
            del repo_root
            return SimpleNamespace(
                vector_store_config=config,
                trusted_scope=VectorScope(
                    "workspace-a",
                    "repo-a",
                    "default",
                    "codecompass",
                ),
                index_task_service=object(),
                secret_resolver=object(),
                vector_store_factory=FailingFactory(),
                observer=object(),
                read_execution=HUB_DIRECT_QDRANT_READ_EXECUTION,
            )

    monkeypatch.setattr(
        settings,
        "codecompass_vector_enabled",
        True,
    )

    with pytest.raises(
        VectorStoreError,
        match="qdrant_unavailable",
    ):
        HybridOrchestrator(
            repo_root=tmp_path,
            data_roots=[],
            codecompass_vector_runtime_resolver=RuntimeResolver(),
        )


def test_hub_runtime_resolver_uses_explicit_trusted_scope(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    config = VectorStoreConfig.from_mapping({"provider": "qdrant", "qdrant": {}})
    task_service = object()

    class Rollout:
        def resolve(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(config=config.as_dict())

    runtime = HubCodeCompassVectorRuntimeResolver(
        workspace_id="workspace-a",
        repository_id="repo-a",
        profile_name="semantic",
        rollout_service=Rollout(),
        index_task_service=task_service,
        index_input_publisher=object(),
        allow_hub_qdrant_reads=True,
    ).resolve(repo_root=tmp_path.resolve())

    assert calls == [
        {
            "domain": "codecompass",
            "workspace_id": "workspace-a",
            "profile_name": "semantic",
        }
    ]
    assert runtime.trusted_scope == VectorScope(
        "workspace-a",
        "repo-a",
        "semantic",
        "codecompass",
    )
    assert runtime.index_task_service is task_service
    assert runtime.secret_resolver is not None


def test_real_rollout_qdrant_path_requires_explicit_hub_read_opt_in(
    tmp_path: Path,
) -> None:
    rollout = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        global_config={},
        audit=lambda _event, _payload: None,
    )
    rollout.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )
    resolver = HubCodeCompassVectorRuntimeResolver(
        workspace_id="workspace-a",
        repository_id="repo-a",
        rollout_service=rollout,
        index_task_service=object(),
    )

    try:
        resolver.resolve(repo_root=tmp_path.resolve())
    except ValueError as exc:
        assert str(exc) == "vector_store_qdrant_read_execution_not_configured"
    else:
        raise AssertionError("Qdrant Hub read unexpectedly enabled")


def test_runtime_cache_signature_tracks_rollout_changes(
    tmp_path: Path,
) -> None:
    rollout = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        global_config={},
        audit=lambda _event, _payload: None,
    )
    resolver = HubCodeCompassVectorRuntimeResolver(
        workspace_id="workspace-a",
        repository_id="repo-a",
        rollout_service=rollout,
    )
    before = resolver.cache_signature(repo_root=tmp_path.resolve())
    rollout.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )

    assert resolver.cache_signature(repo_root=tmp_path.resolve()) != before


def test_real_rollout_qdrant_path_builds_only_with_explicit_hub_capability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rollout = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        global_config={},
        audit=lambda _event, _payload: None,
    )
    rollout.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )
    built: list[VectorStoreConfig] = []
    build_kwargs: list[dict] = []
    sentinel_store = object()
    factory = VectorStoreFactory(
        {
            "qdrant": lambda config, **kwargs: built.append(config) or build_kwargs.append(kwargs) or sentinel_store,
        }
    )
    resolver = HubCodeCompassVectorRuntimeResolver(
        workspace_id="workspace-a",
        repository_id="repo-a",
        rollout_service=rollout,
        index_task_service=object(),
        index_input_publisher=object(),
        vector_store_factory=factory,
        allow_hub_qdrant_reads=True,
    )
    monkeypatch.setattr(settings, "codecompass_vector_enabled", True)

    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[],
        codecompass_vector_runtime_resolver=resolver,
    )

    assert built and built[0].provider == VectorStoreProvider.QDRANT
    assert build_kwargs[0]["observer"] is not None
    assert orchestrator.codecompass_vector_service is not None


def test_default_runtime_resolver_requires_explicit_complete_scope() -> None:
    assert build_default_codecompass_vector_runtime_resolver(environ={}) is None
    try:
        build_default_codecompass_vector_runtime_resolver(
            environ={"ANANTA_CODECOMPASS_VECTOR_WORKSPACE_ID": "workspace-a"}
        )
    except ValueError as exc:
        assert str(exc) == "codecompass_vector_runtime_scope_incomplete"
    else:
        raise AssertionError("partial runtime scope unexpectedly accepted")
