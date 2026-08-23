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
            "override_metadata": {
                "owner": "ranking-test",
                "reason": "deterministic test",
                "scope": "test",
                "version": "test-v1",
                "expires_at": "2099-01-01T00:00:00Z",
            },
            "trace_scores": True,
        }
    })

    assert cfg.restricted_inference_rerank_enabled is True
    assert cfg.score_weights["embedding_score"] == 0.1
    assert "unknown" not in cfg.score_weights
    assert cfg.trace_scores is True
    assert cfg.override_status == "active_experimental_override"


def test_ranking_config_rejects_ungoverned_weights() -> None:
    cfg = CodeCompassRankingConfig.from_config({
        "codecompass_ranking": {"score_weights": {"embedding_score": 0.01}}
    })

    assert cfg.score_weights["embedding_score"] == 0.45
    assert cfg.override_status == "rejected_missing_governance"
