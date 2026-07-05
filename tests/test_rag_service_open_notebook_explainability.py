from agent.config import settings
from agent.services.rag_service import RagService


class _FakeRetrievalService:
    def retrieve_context(self, query, **kwargs):
        return {"query": query, "strategy": {}, "chunks": []}


class _FakeContextBundleService:
    def build_bundle(self, *, query, context_payload, **kwargs):
        del context_payload, kwargs
        return {
            "query": query,
            "chunks": [
                {
                    "engine": "knowledge_index",
                    "source": "open-notebook/context-budget.md",
                    "content": "context budget heuristics",
                    "metadata": {
                        "source_type": "open_notebook",
                        "source_id": "open-notebook-abc123def456",
                        "chunk_id": "open_notebook:abc",
                        "snapshot_id": "snap_1234567890abcdef",
                        "artifact_id": "art-1",
                        "record_kind": "primary_source",
                        "collection_names": ["Autonomous Agents Research"],
                        "api_key_hint": "sk-secret1234567890abc",
                    },
                },
                {
                    "engine": "repository_map",
                    "source": "README.md",
                    "content": "repo readme",
                    "metadata": {"source_type": "repo", "source_id": "README.md", "chunk_id": "repo:1"},
                },
            ],
            "explainability": {},
        }


def _bundle(provenance_visibility=None):
    service = RagService(
        retrieval_service=_FakeRetrievalService(),
        context_bundle_service=_FakeContextBundleService(),
    )
    return service.retrieve_context_bundle("budget", provenance_visibility=provenance_visibility)


def test_source_types_and_counts_include_open_notebook():
    bundle = _bundle()
    explainability = bundle["explainability"]
    assert "open_notebook" in explainability["source_types"]
    assert explainability["source_type_counts"]["open_notebook"] == 1
    assert explainability["source_type_counts"]["repo"] == 1
    assert "primary_source" in explainability["chunk_types"]
    assert "Autonomous Agents Research" in explainability["collection_names"]


def test_standard_visibility_hides_ids_but_shows_source_and_type():
    bundle = _bundle()
    open_notebook_rows = [
        row for row in bundle["explainability"]["sources"] if row["source_type"] == "open_notebook"
    ]
    assert open_notebook_rows
    row = open_notebook_rows[0]
    assert row["source"] == "open-notebook/context-budget.md"
    assert "source_id" not in row
    assert "chunk_id" not in row
    assert "snapshot_id" not in row


def test_admin_visibility_exposes_source_chunk_and_snapshot_ids():
    bundle = _bundle(provenance_visibility="admin")
    row = [r for r in bundle["explainability"]["sources"] if r["source_type"] == "open_notebook"][0]
    assert row["source_id"] == "open-notebook-abc123def456"
    assert row["chunk_id"] == "open_notebook:abc"
    assert row["snapshot_id"] == "snap_1234567890abcdef"
    assert bundle["provenance_policy"]["visibility_level"] == "admin"


def test_redaction_masks_secrets_in_explainability(monkeypatch):
    monkeypatch.setattr(settings, "rag_redact_sensitive", True)
    bundle = _bundle(provenance_visibility="admin")
    serialized = str(bundle["explainability"])
    assert "sk-secret1234567890abc" not in serialized
