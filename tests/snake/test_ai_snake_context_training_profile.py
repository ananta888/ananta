from __future__ import annotations

from client_surfaces.operator_tui.ai_snake_context import training_profile_envelope
from client_surfaces.operator_tui.ai_snake_training_store import save_patterns


def _pattern(pattern_id: str, *, intent: str, confidence: float, status: str = "active", expires_at: str = "2030-01-01T00:00:00Z") -> dict:
    return {
        "pattern_id": pattern_id,
        "pattern_type": "sequence_rule",
        "conditions": {"all": [{"field": "recent_event", "op": "contains", "value": "artifact:README.md"}]},
        "predicted_intent": intent,
        "confidence": confidence,
        "evidence": {"source_event_ids": [f"evt-{pattern_id}"], "sample_size": 1, "counter_refs": [f"counter:{pattern_id}"]},
        "counters": {"hits": 1, "misses": 0, "positives": 1, "negatives": 0},
        "last_seen_at": "2026-01-01T00:00:00Z",
        "expires_at": expires_at,
        "status": status,
        "human_explanation": "exp",
        "ai_hint": "hint",
    }


def test_training_profile_envelope_selects_only_active_non_expired(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    save_patterns(
        [
            _pattern("pat-a", intent="artifact_explain", confidence=0.6, status="active"),
            _pattern("pat-b", intent="artifact_explain", confidence=0.9, status="disabled"),
            _pattern("pat-c", intent="navigate", confidence=0.8, status="active", expires_at="2020-01-01T00:00:00Z"),
            _pattern("pat-d", intent="artifact_explain", confidence=0.7, status="active"),
        ]
    )
    env = training_profile_envelope(intent="artifact_explain", max_patterns=8)
    refs = env["active_pattern_refs"]
    ids = [row["pattern_id"] for row in refs]
    assert "pat-a" in ids
    assert "pat-d" in ids
    assert "pat-b" not in ids
    assert "pat-c" not in ids


def test_training_profile_envelope_limits_budget(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    save_patterns([_pattern(f"pat-{i}", intent="navigate", confidence=0.4 + (i * 0.01)) for i in range(20)])
    env = training_profile_envelope(intent="navigate", max_patterns=8)
    assert len(env["active_pattern_refs"]) == 8
    assert env["training_profile_ref"]["profile_id"]
