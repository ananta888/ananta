from __future__ import annotations

from agent.services.candidate_scoring_service import CandidateScoringService
from agent.services.codecompass_ranking_config_service import CodeCompassRankingConfig


def test_candidate_scoring_weighting_and_trace() -> None:
    cfg = CodeCompassRankingConfig.from_config({
        "codecompass_ranking": {
            "score_weights": {
                "embedding_score": 0.5,
                "graph_score": 0.0,
                "symbol_score": 0.0,
                "transformer_rerank_score": 0.5,
                "policy_penalty": -0.2,
            }
        }
    })
    ranked = CandidateScoringService(config=cfg).rank(
        [{"path": "b.py", "record_id": "b", "embedding_score": 0.2}],
        transformer_scores={"b": {"score": 1.0, "model_id": "m", "engine": "mock"}},
    )

    assert ranked[0].final_score == 0.6
    assert ranked[0].trace.model_id == "m"


def test_candidate_scoring_stable_tie_break() -> None:
    ranked = CandidateScoringService().rank([
        {"path": "b.py", "record_id": "2", "embedding_score": 0.5},
        {"path": "a.py", "record_id": "1", "embedding_score": 0.5},
    ])

    assert [item.path for item in ranked] == ["a.py", "b.py"]
