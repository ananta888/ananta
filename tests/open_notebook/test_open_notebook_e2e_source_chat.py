from agent.services.source_chat_context_service import SourceChatContextService
from agent.services.source_chat_service import SourceChatService


class _Registry:
    def get_source(self, source_id):
        return {"source_id": source_id, "enabled": True}


class _Rag:
    def retrieve_context_bundle(self, query, **_kwargs):
        return {
            "chunks": [
                {
                    "engine": "knowledge_index",
                    "source": "source.md",
                    "content": "Primary grounded fact",
                    "score": 3,
                    "metadata": {
                        "source_type": "open_notebook",
                        "registry_source_id": "open-notebook-e2e",
                        "open_notebook_source_id": "source-1",
                        "snapshot_id": "snap_1234567890abcdef",
                        "chunk_id": "onb:source-1",
                        "content_hash": "hash",
                        "record_kind": "primary_source",
                        "source_title": "Primary",
                    },
                },
                {
                    "engine": "knowledge_index",
                    "source": "note.md",
                    "content": "Unapproved note",
                    "score": 1,
                    "metadata": {
                        "source_type": "open_notebook",
                        "registry_source_id": "open-notebook-e2e",
                        "snapshot_id": "snap_1234567890abcdef",
                        "chunk_id": "onb:note-1",
                        "record_kind": "note",
                        "source_title": "Note",
                    },
                },
            ],
            "explainability": {"source_type_counts": {"open_notebook": 2}},
        }


def test_source_chat_uses_budgeted_grounded_prompt_and_excludes_notes(monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "default_provider", "ollama")
    context = SourceChatContextService(
        rag_service=_Rag(),
        source_registry=_Registry(),
        grounded_prompt_builder=lambda *, prompt, context_text, chunks: f"{prompt}|{context_text}|{len(chunks)}",
    )
    prompts = []
    service = SourceChatService(context_service=context, llm_call=lambda prompt: prompts.append(prompt) or "answer")
    result = service.answer(
        source_ref="open-notebook-e2e",
        prompt="question",
        include_insights=True,
        include_notes=False,
    )
    assert "Primary grounded fact" in prompts[0]
    assert "Unapproved note" not in prompts[0]
    assert result["source_references"]
    assert result["context_hash"]
