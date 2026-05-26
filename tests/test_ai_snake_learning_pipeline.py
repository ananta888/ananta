from __future__ import annotations

import json

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
