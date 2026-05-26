from __future__ import annotations

from client_surfaces.operator_tui.ai_snake_prediction_cache import PredictionCache


def test_prediction_cache_hit_miss_and_expiry() -> None:
    cache = PredictionCache(ttl_seconds=15)
    key = cache.make_key(
        section="Artifacts",
        target_ref="client_surfaces/operator_tui/renderer.py",
        intent_kind="artifact_explain",
        context_hash="ctx-1",
    )
    assert cache.get(key, now=10.0) is None
    cache.set(key, {"predicted_intent": "artifact_explain"}, now=10.0)
    assert cache.get(key, now=20.0) == {"predicted_intent": "artifact_explain"}
    assert cache.get(key, now=26.0) is None


def test_prediction_cache_key_normalization() -> None:
    cache = PredictionCache(ttl_seconds=30)
    key_a = cache.make_key(section="  Artifacts ", target_ref="Foo", intent_kind="CHAT", context_hash=" X ")
    key_b = cache.make_key(section="artifacts", target_ref="foo", intent_kind="chat", context_hash="x")
    assert key_a == key_b
