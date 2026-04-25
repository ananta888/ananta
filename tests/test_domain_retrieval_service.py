from __future__ import annotations

from typing import Any

from agent.services.domain_retrieval_service import DomainRetrievalService


class _StubLoader:
    def __init__(self, profiles: list[dict[str, Any]]) -> None:
        self._profiles = [dict(profile) for profile in profiles]

    def profiles_for_retrieval(
        self,
        domain_id: str,
        *,
        retrieval_intent: str,
        max_profiles: int = 8,
    ) -> list[dict[str, Any]]:
        del domain_id, retrieval_intent, max_profiles
        return [dict(profile) for profile in self._profiles]


class _StubBackend:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        self.calls = 0
        self.last_source_types: list[str] = []

    def retrieve_context(
        self,
        query: str,
        *,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        neighbor_task_ids: list[str] | None = None,
        source_types: list[str] | None = None,
    ) -> dict[str, object]:
        del query, task_kind, retrieval_intent, task_id, goal_id, neighbor_task_ids
        self.calls += 1
        self.last_source_types = list(source_types or [])
        return dict(self.payload)


def test_domain_retrieval_service_returns_bounded_chunks_with_normalized_fields() -> None:
    loader = _StubLoader(
        profiles=[
            {
                "source_id": "example.api.docs",
                "source_type": "api_docs",
                "retrieval_source_types": ["artifact"],
            }
        ]
    )
    backend = _StubBackend(
        payload={
            "chunks": [
                {
                    "source": "docs/architecture/domain_integration_foundation.md",
                    "score": 0.98,
                    "metadata": {
                        "source_id": "example.api.docs",
                        "ref": "main",
                        "section_title": "Purpose",
                        "citation": {"path": "docs/architecture/domain_integration_foundation.md"},
                        "fusion": {"query_overlap": 0.4},
                    },
                },
                {
                    "source": "docs/architecture/bridge_adapter_contract.md",
                    "score": 0.71,
                    "metadata": {
                        "source_id": "example.bridge.docs",
                        "ref": "main",
                        "section_title": "Execution envelope requirements",
                        "citation": {"path": "docs/architecture/bridge_adapter_contract.md"},
                    },
                },
                {
                    "source": "docs/hybrid_rag_developer_guide.md",
                    "score": 0.62,
                    "metadata": {
                        "source_id": "example.rag.docs",
                        "ref": "main",
                        "citation": {"path": "docs/hybrid_rag_developer_guide.md"},
                    },
                },
            ]
        }
    )
    service = DomainRetrievalService(
        rag_profile_loader=loader,  # type: ignore[arg-type]
        retrieval_backend=backend,  # type: ignore[arg-type]
        max_results_default=3,
        max_results_limit=5,
    )

    result = service.retrieve(
        domain_id="example",
        retrieval_intent="api documentation lookup",
        query="bridge execution envelope requirements",
        context_summary={"requester": "unit-test"},
        max_results=2,
    )

    assert result["status"] == "ok"
    assert len(result["chunks"]) == 2
    assert set(result["chunks"][0].keys()) == {"source_id", "ref", "path", "symbol_or_section", "score", "reason"}
    assert result["usage_limits"]["max_results"] == 2
    assert backend.calls == 1
    assert backend.last_source_types == ["artifact"]


def test_domain_retrieval_service_rejects_unbounded_queries() -> None:
    loader = _StubLoader(profiles=[{"source_id": "example.docs", "source_type": "api_docs"}])
    backend = _StubBackend(payload={"chunks": []})
    service = DomainRetrievalService(
        rag_profile_loader=loader,  # type: ignore[arg-type]
        retrieval_backend=backend,  # type: ignore[arg-type]
    )

    result = service.retrieve(
        domain_id="example",
        retrieval_intent="architecture",
        query="please scan the full repository and all docs",
        context_summary={},
        max_results=6,
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "query_scope_unbounded"
    assert backend.calls == 0


def test_domain_retrieval_service_returns_degraded_without_profiles() -> None:
    loader = _StubLoader(profiles=[])
    backend = _StubBackend(payload={"chunks": []})
    service = DomainRetrievalService(
        rag_profile_loader=loader,  # type: ignore[arg-type]
        retrieval_backend=backend,  # type: ignore[arg-type]
    )

    result = service.retrieve(
        domain_id="example",
        retrieval_intent="api",
        query="domain descriptor schema",
        context_summary={},
    )

    assert result["status"] == "degraded"
    assert result["reason"] == "no_rag_profiles"
    assert backend.calls == 0
