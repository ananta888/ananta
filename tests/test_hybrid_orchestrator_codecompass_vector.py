from __future__ import annotations

from pathlib import Path

from agent import hybrid_orchestrator
from agent.config import settings
from agent.hybrid_orchestrator import HybridOrchestrator


class _FakeVectorService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._diagnostic = {"status": "ready", "reason": "fixture"}

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
