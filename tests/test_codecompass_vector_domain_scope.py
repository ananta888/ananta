from __future__ import annotations

from pathlib import Path

from agent.codecompass.domain_scope import ResolvedDomainScope
from agent.hybrid_orchestrator import HybridOrchestrator


class _ScopeAwareVectorService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search(self, *, query: str, top_k: int = 10, allowed_paths: list[str] | None = None):
        self.calls.append({"allowed_paths": allowed_paths})
        rows = [
            {
                "engine": "codecompass_vector",
                "source": "orders/service.py",
                "content": "order invoice vector result",
                "score": 0.9,
                "metadata": {"record_id": "orders-1", "vector_score": 0.9},
            },
            {
                "engine": "codecompass_vector",
                "source": "catalog/service.py",
                "content": "catalog invoice vector result",
                "score": 0.95,
                "metadata": {"record_id": "catalog-1", "vector_score": 0.95},
            },
        ]
        if allowed_paths is None:
            return rows
        if not allowed_paths:
            return []
        return [row for row in rows if any(row["source"].startswith(f"{path}/") for path in allowed_paths)]

    def last_diagnostic(self):
        return {"status": "ready", "reason": "fixture"}


def test_codecompass_vector_respects_domain_allowed_paths(tmp_path: Path) -> None:
    service = _ScopeAwareVectorService()
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "service.py").write_text("class OrderService: pass\n", encoding="utf-8")
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog" / "service.py").write_text("class CatalogService: pass\n", encoding="utf-8")
    scope = ResolvedDomainScope(
        active=True,
        strict=True,
        selected_domain_ids=["orders"],
        allowed_read_paths=["orders"],
        allowed_write_paths=["orders"],
    )
    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[],
        codecompass_vector_service=service,
        max_context_chars=4000,
    )

    result = orchestrator.get_relevant_context("invoice service", domain_scope=scope)

    assert service.calls[0]["allowed_paths"] == ["orders"]
    sources = [chunk["source"] for chunk in result["chunks"]]
    assert any(source.startswith("orders/") for source in sources)
    assert all(not source.startswith("catalog/") for source in sources)


def test_codecompass_vector_empty_scope_does_not_global_search(tmp_path: Path) -> None:
    service = _ScopeAwareVectorService()
    scope = ResolvedDomainScope(
        active=True,
        strict=True,
        selected_domain_ids=["empty"],
        allowed_read_paths=[],
        allowed_write_paths=[],
    )
    orchestrator = HybridOrchestrator(
        repo_root=tmp_path,
        data_roots=[],
        codecompass_vector_service=service,
        max_context_chars=4000,
    )

    result = orchestrator.get_relevant_context("invoice service", domain_scope=scope)

    assert service.calls[0]["allowed_paths"] == []
    assert result["chunks"] == []
