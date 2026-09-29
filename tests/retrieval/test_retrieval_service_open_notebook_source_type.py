import pytest

from agent.config import settings
from agent.hybrid_orchestrator import ContextChunk
from agent.services.retrieval_service import RetrievalService


class _FakeContextManager:
    policy_version = "v1"

    def rerank(self, *, chunks, query, max_chunks, max_chars, max_tokens):
        del query, max_chars, max_tokens
        return sorted(chunks, key=lambda chunk: chunk.score, reverse=True)[:max_chunks]

    def estimate_tokens(self, text: str) -> int:
        return len(text.split())


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.context_manager = _FakeContextManager()

    def _redact(self, text: str) -> str:
        return text

    def get_relevant_context(self, query: str, *, domain_scope=None) -> dict[str, object]:
        return {
            "query": query,
            "strategy": {"repository_map": 1},
            "policy_version": "v1",
            "chunks": [
                {
                    "engine": "repository_map",
                    "source": "README.md",
                    "score": 1.0,
                    "content": "repo context",
                    "metadata": {},
                }
            ],
            "context_text": "[repository_map] README.md\nrepo context",
            "token_estimate": 4,
        }


class _ScopeRecordingKnowledgeService:
    """Returns chunks shaped like real knowledge-index output per scope."""

    def __init__(self) -> None:
        self.scope_calls: list[set[str]] = []

    def search(self, query: str, *, top_k: int, task_kind=None, retrieval_intent=None, source_scopes=None):
        del query, top_k, task_kind, retrieval_intent
        scopes = set(source_scopes or [])
        self.scope_calls.append(scopes)
        if scopes == {"open_notebook"}:
            return [
                ContextChunk(
                    engine="knowledge_index",
                    source="open-notebook/context-budget.md",
                    content="context budget heuristics from notebook",
                    score=2.0,
                    metadata={
                        "source_scope": "open_notebook",
                        "record_kind": "open_notebook_source_chunk",
                        "import_metadata": {
                            "source_scope": "open_notebook",
                            "source_system": "open_notebook",
                            "source_type": "open_notebook",
                            "registry_source_id": "open-notebook-abc123def456",
                            "open_notebook_source_id": "src-text-context-budget",
                            "snapshot_id": "snap_1234567890abcdef",
                            "artifact_id": "art-1",
                            "record_kind": "primary_source",
                        },
                    },
                )
            ]
        if scopes == {"artifact"}:
            return [
                ContextChunk(
                    engine="knowledge_index",
                    source="docs/artifact.md",
                    content="artifact knowledge",
                    score=1.5,
                    metadata={"knowledge_index_id": "idx-1", "source_scope": "artifact"},
                )
            ]
        return []


class _FakeMemoryEntryRepo:
    def get_by_task(self, task_id: str):
        return []

    def get_by_goal(self, goal_id: str):
        return []


def _service(monkeypatch, *, open_notebook_enabled=True):
    monkeypatch.setattr(settings, "rag_source_open_notebook_enabled", open_notebook_enabled)
    knowledge = _ScopeRecordingKnowledgeService()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge,
        memory_entry_repository=_FakeMemoryEntryRepo(),
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()
    return service, knowledge


def test_build_source_adapters_contains_open_notebook_key(monkeypatch):
    service, _knowledge = _service(monkeypatch)
    assert "open_notebook" in service._source_adapters


def test_explicit_open_notebook_source_type_calls_only_open_notebook_adapter(monkeypatch):
    service, knowledge = _service(monkeypatch)
    payload = service.retrieve_context("context budget", source_types=["open_notebook"])

    assert knowledge.scope_calls == [{"open_notebook"}]
    chunk_types = {chunk["metadata"]["source_type"] for chunk in payload["chunks"]}
    assert chunk_types == {"open_notebook"}
    policy = payload["strategy"]["source_policy"]
    assert policy["requested"] == ["open_notebook"]
    assert policy["effective"] == ["open_notebook"]


def test_default_source_types_do_not_include_open_notebook(monkeypatch):
    service, knowledge = _service(monkeypatch)
    payload = service.retrieve_context("context budget")

    assert {"open_notebook"} not in knowledge.scope_calls
    assert "open_notebook" not in payload["strategy"]["source_policy"]["effective"]


def test_disabled_flag_rejects_explicit_open_notebook_request(monkeypatch):
    service, _knowledge = _service(monkeypatch, open_notebook_enabled=False)
    with pytest.raises(ValueError, match="no_retrieval_source_enabled"):
        service.retrieve_context("context budget", source_types=["open_notebook"])


def test_unknown_source_type_is_still_rejected(monkeypatch):
    service, _knowledge = _service(monkeypatch)
    with pytest.raises(ValueError, match="invalid_source_type"):
        service.retrieve_context("context budget", source_types=["surprise"])


def test_combined_repo_artifact_open_notebook_retrieval(monkeypatch):
    service, knowledge = _service(monkeypatch)
    payload = service.retrieve_context(
        "context budget", source_types=["repo", "artifact", "open_notebook"]
    )

    assert {"artifact"} in knowledge.scope_calls
    assert {"open_notebook"} in knowledge.scope_calls
    engines = {chunk["engine"] for chunk in payload["chunks"]}
    assert "repository_map" in engines
    source_types = {chunk["metadata"].get("source_type") for chunk in payload["chunks"]}
    assert "open_notebook" in source_types
    contributions = payload["strategy"]["fusion"]["source_type_contributions_final"]
    assert contributions.get("open_notebook", 0) >= 1


def test_config_signature_invalidates_on_flag_change(monkeypatch):
    service, _knowledge = _service(monkeypatch, open_notebook_enabled=True)
    signature_enabled = service._config_signature()
    monkeypatch.setattr(settings, "rag_source_open_notebook_enabled", False)
    assert service._config_signature() != signature_enabled
