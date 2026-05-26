from __future__ import annotations

import json

from client_surfaces.operator_tui.ai_snake_learning import apply_prediction_feedback, compact_event_log, mine_patterns_from_events
from client_surfaces.operator_tui.ai_snake_training_recorder import AiSnakeTrainingRecorder


def test_training_recorder_writes_jsonl_event(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    recorder = AiSnakeTrainingRecorder(enabled=True, max_bytes=1024 * 1024)
    wrote = recorder.record_event(
        event_type="section_visit",
        value_norm="dashboard",
        refs=["section:dashboard"],
        privacy_class="public_ui",
    )
    assert wrote is True
    line = recorder.events_path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["schema_version"] == "ai_snake_behavior_event.v1"
    assert payload["event_type"] == "section_visit"
    assert payload["privacy_class"] == "public_ui"


def test_training_recorder_redacts_private_value_norm(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    recorder = AiSnakeTrainingRecorder(enabled=True)
    recorder.record_event(
        event_type="notes_usage",
        value_norm="my secret note content",
        refs=["notes:self"],
        privacy_class="private_local",
    )
    payload = json.loads(recorder.events_path.read_text(encoding="utf-8").strip())
    assert payload["privacy_class"] == "private_local"
    assert payload["value_norm"] == "notes_active=true"


def test_pattern_miner_builds_sequence_rule_for_repeated_artifact_focus() -> None:
    events = []
    for idx in range(4):
        events.append(
            {
                "event_id": f"evt-s-{idx}",
                "event_type": "section_visit",
                "value_norm": "artifacts",
                "refs": ["section:artifacts"],
            }
        )
        events.append(
            {
                "event_id": f"evt-a-{idx}",
                "event_type": "artifact_focus",
                "value_norm": "README.md",
                "refs": ["section:artifacts", "README.md"],
            }
        )
    patterns = mine_patterns_from_events(events=events, min_cases=3)
    assert patterns
    assert patterns[0]["pattern_type"] == "sequence_rule"
    assert patterns[0]["human_explanation"]
    assert float(patterns[0]["confidence"]) > 0.3


def test_pattern_miner_ignores_random_noise_below_threshold() -> None:
    events = [
        {"event_id": "evt-1", "event_type": "artifact_focus", "value_norm": "A", "refs": ["A"]},
        {"event_id": "evt-2", "event_type": "artifact_focus", "value_norm": "B", "refs": ["B"]},
        {"event_id": "evt-3", "event_type": "artifact_focus", "value_norm": "C", "refs": ["C"]},
    ]
    assert mine_patterns_from_events(events=events, min_cases=3) == []


def test_prediction_feedback_updates_pattern_confidence() -> None:
    pattern = {
        "pattern_id": "pat-artifact-x",
        "pattern_type": "sequence_rule",
        "conditions": {"all": [{"field": "recent_event", "op": "contains", "value": "artifact:README.md"}]},
        "predicted_intent": "artifact_explain",
        "confidence": 0.5,
        "evidence": {"source_event_ids": ["evt-1", "evt-2", "evt-3"], "sample_size": 3, "counter_refs": ["counter:pat-artifact-x"]},
        "counters": {"hits": 3, "misses": 0, "positives": 3, "negatives": 0},
        "last_seen_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-02-01T00:00:00Z",
        "status": "draft",
        "human_explanation": "x",
        "ai_hint": "y",
    }
    up, changed_up = apply_prediction_feedback(patterns=[pattern], target_ref="README.md", positive=True)
    down, changed_down = apply_prediction_feedback(patterns=up, target_ref="README.md", positive=False)
    assert changed_up is True
    assert changed_down is True
    assert int(down[0]["counters"]["positives"]) >= 4
    assert int(down[0]["counters"]["negatives"]) >= 1


def test_compact_event_log_creates_backup_and_limits_size(tmp_path) -> None:
    log = tmp_path / "events.jsonl"
    payload = "\n".join(
        json.dumps(
            {
                "schema_version": "ai_snake_behavior_event.v1",
                "event_id": f"evt-{i}",
                "event_type": "movement_vector",
                "occurred_at": "2026-01-01T00:00:00Z",
                "context_ref": "operator_tui",
                "target_ref": "",
                "value_norm": "right",
                "refs": [],
                "privacy_class": "public_ui",
                "retention_hint": "rolling_7d",
                "source": {"component": "operator_tui", "mode": "training"},
                "extensions": {},
            }
        )
        for i in range(200)
    )
    log.write_text(payload + "\n", encoding="utf-8")
    result = compact_event_log(events_path=log, max_bytes=1024, keep_last_lines=20, backup=True)
    assert result["before_bytes"] > result["after_bytes"]
    assert result["after_bytes"] <= 1024
    assert log.with_suffix(".jsonl.bak").exists()


def test_private_notes_content_not_written_into_pattern_evidence() -> None:
    secret = "super-secret-note-content"
    events = [
        {"event_id": "evt-1", "event_type": "section_change", "normalized_value": "notes", "refs": {"section_ref": "section:notes"}},
        {"event_id": "evt-2", "event_type": "notes_state", "normalized_value": "notes_active", "refs": {"section_ref": "section:notes"}},
        {"event_id": "evt-3", "event_type": "artifact_selected", "normalized_value": "README.md", "refs": {"artifact_ref": "README.md"}},
        {"event_id": "evt-4", "event_type": "artifact_selected", "normalized_value": "README.md", "refs": {"artifact_ref": "README.md"}},
        {"event_id": "evt-5", "event_type": "artifact_selected", "normalized_value": "README.md", "refs": {"artifact_ref": "README.md"}},
        {"event_id": "evt-6", "event_type": "custom:note_raw", "normalized_value": secret, "refs": {"section_ref": "section:notes"}},
    ]
    patterns = mine_patterns_from_events(events=events, min_cases=3)
    dumped = json.dumps(patterns, ensure_ascii=False)
    assert secret not in dumped
