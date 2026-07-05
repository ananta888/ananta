from __future__ import annotations

import pytest


@pytest.fixture
def _chat_service(monkeypatch, tmp_path):
    """Install a SourceChatContextService with fake rag + registry as the route singleton."""
    from agent.services import source_chat_context_service as module
    from agent.services.source_chat_context_service import SourceChatContextService

    chunk = {
        "engine": "knowledge_index",
        "source": "open-notebook/survey.md",
        "content": "hub-centric orchestration keeps workers stateless",
        "score": 2.0,
        "metadata": {
            "source_type": "open_notebook",
            "source_id": "open-notebook-abc123def456",
            "registry_source_id": "open-notebook-abc123def456",
            "snapshot_id": "snap_1234567890abcdef",
            "chunk_id": "onb:survey1",
            "artifact_id": "art-1",
            "record_kind": "primary_source",
            "source_title": "Survey",
            "content_hash": "hash-1",
        },
    }

    class _FakeRag:
        def __init__(self):
            self.raise_disabled = False
            self.last_kwargs = None

        def retrieve_context_bundle(self, query, **kwargs):
            if self.raise_disabled:
                raise ValueError("no_retrieval_source_enabled")
            self.last_kwargs = kwargs
            return {
                "query": query,
                "chunks": [dict(chunk)],
                "explainability": {"source_types": ["open_notebook"], "source_type_counts": {"open_notebook": 1}},
                "provenance_policy": {"visibility_level": kwargs.get("provenance_visibility") or "standard"},
            }

    class _FakeRegistry:
        def get_source(self, source_id):
            if source_id == "open-notebook-abc123def456":
                return {"source_id": source_id, "enabled": True}
            return None

    fake_rag = _FakeRag()
    service = SourceChatContextService(
        rag_service=fake_rag,
        source_registry=_FakeRegistry(),
        grounded_prompt_builder=lambda *, prompt, context_text, chunks: f"GROUNDED::{prompt}::{len(chunks)}",
    )
    monkeypatch.setattr(module, "source_chat_context_service", service)
    return fake_rag


@pytest.fixture
def _fake_llm(monkeypatch):
    import agent.services.chat_partial_summary_service as summary_module

    calls: list[str] = []

    def _fake_call(prompt: str, *, timeout: int = 30) -> str:
        calls.append(prompt)
        return "grounded answer about orchestration"

    monkeypatch.setattr(summary_module, "call_llm_text", _fake_call)
    return calls


def test_source_chat_happy_path(client, admin_auth_header, _chat_service, _fake_llm):
    res = client.post(
        "/sources/open-notebook-abc123def456/chat",
        headers=admin_auth_header,
        json={"prompt": "What keeps workers stateless?"},
    )
    assert res.status_code == 200
    data = res.json["data"]
    assert data["answer"] == "grounded answer about orchestration"
    assert data["context_hash"]
    assert data["source_references"]
    assert data["source_references"][0]["snapshot_id"] == "snap_1234567890abcdef"
    assert data["explainability"]["source_type_counts"]["open_notebook"] == 1
    # LLM received the budgeted grounded prompt, not the raw question only
    assert _fake_llm and _fake_llm[0].startswith("GROUNDED::")


def test_source_chat_missing_source_returns_404(client, admin_auth_header, _chat_service, _fake_llm):
    res = client.post(
        "/sources/unknown-source/chat",
        headers=admin_auth_header,
        json={"prompt": "hello"},
    )
    assert res.status_code == 404


def test_source_chat_disabled_open_notebook_source_type(client, admin_auth_header, _chat_service, _fake_llm):
    _chat_service.raise_disabled = True
    res = client.post(
        "/sources/open-notebook-abc123def456/chat",
        headers=admin_auth_header,
        json={"prompt": "hello"},
    )
    assert res.status_code == 400
    assert "open_notebook_source_disabled" in str(res.json)


def test_source_chat_requires_prompt(client, admin_auth_header, _chat_service, _fake_llm):
    res = client.post(
        "/sources/open-notebook-abc123def456/chat",
        headers=admin_auth_header,
        json={},
    )
    assert res.status_code == 400


def test_source_chat_forwards_provenance_admin(client, admin_auth_header, _chat_service, _fake_llm):
    res = client.post(
        "/sources/open-notebook-abc123def456/chat",
        headers=admin_auth_header,
        json={"prompt": "hello", "provenance_visibility": "admin", "include_notes": True, "max_chunks": 2},
    )
    assert res.status_code == 200
    assert _chat_service.last_kwargs["provenance_visibility"] == "admin"
