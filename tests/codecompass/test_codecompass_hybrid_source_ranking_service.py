from types import SimpleNamespace

from agent.services.codecompass_hybrid_source_ranking_service import (
    CodeCompassHybridSourceRankingService,
)


def test_hybrid_ranking_prioritizes_relevant_source_and_preserves_admitted_chunks() -> None:
    chunks = [
        SimpleNamespace(source="docs/general.md", content="general", score=400.0, metadata={}),
        SimpleNamespace(
            source="agent/services/codecompass_context_service.py",
            content="Symbols: CodeCompassContextService resolve_context",
            score=40.0,
            metadata={},
        ),
    ]

    ordered, trace = CodeCompassHybridSourceRankingService().rank(
        query="erkläre mir codecompass",
        chunks=chunks,
    )

    assert ordered[0].source == "agent/services/codecompass_context_service.py"
    assert {item.source for item in ordered} == {item.source for item in chunks}
    assert trace["ranking_version"] == "universal-source-ranking.v1"
    assert trace["strategy"] == "universal_hybrid_final"
