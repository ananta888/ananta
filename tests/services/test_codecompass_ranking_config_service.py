from __future__ import annotations

from agent.services.codecompass_ranking_config_service import CodeCompassRankingConfig


def test_default_ranking_config_keeps_restricted_rerank_disabled() -> None:
    cfg = CodeCompassRankingConfig.from_config({})

    assert cfg.restricted_inference_rerank_enabled is False
    assert cfg.fallback_without_model is True
    assert cfg.score_weights["transformer_rerank_score"] == 0.0


def test_ranking_config_accepts_known_weights_only() -> None:
    cfg = CodeCompassRankingConfig.from_config({
        "codecompass_ranking": {
            "restricted_inference_rerank_enabled": True,
            "score_weights": {
                "embedding_score": "0.1",
                "unknown": 99,
            },
            "trace_scores": True,
        }
    })

    assert cfg.restricted_inference_rerank_enabled is True
    assert cfg.score_weights["embedding_score"] == 0.1
    assert "unknown" not in cfg.score_weights
    assert cfg.trace_scores is True
