from agent.hybrid_orchestrator import ContextChunk
from agent.services.retrieval_query_builder import (
    rerank_candidates,
    source_priority_rules,
    task_profile_for_fusion,
)


def _chunk(source_type: str, *, score: float = 1.0, content: str = "shared query terms") -> ContextChunk:
    return ContextChunk(
        engine="knowledge_index",
        source=f"{source_type}/doc.md",
        content=content,
        score=score,
        metadata={"source_type": source_type},
    )


def test_research_intent_boosts_open_notebook():
    profile = task_profile_for_fusion("research", "notebook research question")
    weights = profile["source_type_weights"]
    assert weights["open_notebook"] >= 1.3
    assert weights["open_notebook"] > weights["repo"]


def test_notes_intent_boosts_open_notebook_even_for_generic_kind():
    profile = task_profile_for_fusion(None, "review notes")
    weights = profile["source_type_weights"]
    assert weights["open_notebook"] >= 1.3


def test_code_change_keeps_repo_above_open_notebook():
    for kind in ("code_change", "api_contract", "bugfix", "implement"):
        profile = task_profile_for_fusion(kind, None)
        weights = profile["source_type_weights"]
        assert weights["repo"] > weights["open_notebook"], kind


def test_default_profile_keeps_open_notebook_neutral_or_lower():
    weights = task_profile_for_fusion(None, None)["source_type_weights"]
    assert weights["open_notebook"] <= 1.0


def test_rerank_orders_open_notebook_first_for_research_query():
    profile = task_profile_for_fusion("research", "notebook research")
    chunks = [_chunk("repo"), _chunk("open_notebook")]
    reranked, meta = rerank_candidates(chunks=chunks, query="shared query terms", profile=profile)
    assert reranked[0].metadata["source_type"] == "open_notebook"
    assert meta["source_type_weights"]["open_notebook"] >= 1.3


def test_rerank_orders_repo_first_for_code_query():
    profile = task_profile_for_fusion("code_change", "fix api contract")
    chunks = [_chunk("open_notebook"), _chunk("repo")]
    reranked, _meta = rerank_candidates(chunks=chunks, query="shared query terms", profile=profile)
    assert reranked[0].metadata["source_type"] == "repo"


def test_mixed_query_keeps_both_types_with_explainable_rules():
    profile = task_profile_for_fusion("analysis", "research architecture overview")
    chunks = [_chunk("repo"), _chunk("open_notebook"), _chunk("artifact")]
    reranked, meta = rerank_candidates(chunks=chunks, query="shared query terms", profile=profile)
    types_present = {chunk.metadata["source_type"] for chunk in reranked}
    assert {"repo", "open_notebook", "artifact"} <= types_present

    rules = source_priority_rules(
        task_kind="analysis",
        retrieval_intent="research architecture overview",
        source_type_weights=meta["source_type_weights"],
    )
    by_type = {rule["source_type"]: rule for rule in rules["rules"]}
    assert by_type["open_notebook"]["reason"] == "user_research_relevance"
    assert by_type["open_notebook"]["rank"] < by_type["repo"]["rank"]


def test_priority_rules_explain_code_truth_priority():
    rules = source_priority_rules(
        task_kind="code_change",
        retrieval_intent=None,
        source_type_weights=task_profile_for_fusion("code_change", None)["source_type_weights"],
    )
    by_type = {rule["source_type"]: rule for rule in rules["rules"]}
    assert by_type["open_notebook"]["reason"] == "user_research_below_code_truth"
    assert by_type["repo"]["rank"] < by_type["open_notebook"]["rank"]
