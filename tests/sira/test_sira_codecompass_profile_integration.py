from __future__ import annotations

from typing import Any

from ananta_contracts.retrieval import RetrievalRequest
from worker.retrieval.codecompass_retriever import CodeCompassRetriever


class ProfileAwareFtsProvider:
    def __init__(self) -> None:
        self.profile_calls: list[dict[str, Any]] = []
        self.baseline_calls = 0

    def search(self, **kwargs):
        self.baseline_calls += 1
        return []

    def search_profiled(self, **kwargs):
        self.profile_calls.append(dict(kwargs))
        return [
            {
                "record_id": "local-record",
                "path": "src/example.py",
                "content": "local candidate without authoritative SourceRef",
                "score": 1.0,
                "metadata": {"retrieval_profile": "corpus_discriminative_lexical"},
            }
        ]


def test_retrieval_request_selects_profiled_fts_without_new_channel():
    provider = ProfileAwareFtsProvider()
    result = CodeCompassRetriever(channel_providers={"codecompass_fts": provider}).retrieve(
        RetrievalRequest(
            query="find example",
            tenant_id="tenant-a",
            scope="repo-a",
            allowed_source_ids=frozenset(),
            retrieval_profile={
                "name": "corpus_discriminative_lexical",
                "corpus_ready": True,
            },
        )
    )
    assert provider.baseline_calls == 0
    assert len(provider.profile_calls) == 1
    assert provider.profile_calls[0]["retrieval_profile"]["name"] == "corpus_discriminative_lexical"
    assert result.metadata["queried_channels"] == ["codecompass_fts"]
    assert result.sources == ()
    assert "source_id_missing" in result.rejection_reasons
