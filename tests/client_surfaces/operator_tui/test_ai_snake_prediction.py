from __future__ import annotations

from client_surfaces.operator_tui.ai_snake_observation import ObservationBuffer
from client_surfaces.operator_tui.ai_snake_prediction import PredictionGate, quick_predict


def test_observation_ringbuffer_normalizes_and_limits() -> None:
    buffer = ObservationBuffer(max_events=100)
    for idx in range(130):
        buffer.add_event(kind="command", value=f"run token=secret-{idx}")
    events = buffer.events()
    assert len(events) == 100
    assert "secret" not in events[-1].normalized_value.lower()
    summary = buffer.compact_summary()
    assert summary["event_count"] == 100


def test_quick_prediction_prefers_artifact_intent() -> None:
    buffer = ObservationBuffer(max_events=100)
    buffer.add_event(kind="section", value="artifacts")
    artifact_event = buffer.add_event(kind="artifact", value="client_surfaces/operator_tui/renderer.py")
    prediction = quick_predict(buffer.events(), now=1000.0)
    assert prediction.predicted_intent == "artifact_explain"
    assert prediction.target_ref
    assert prediction.confidence >= 0.55
    assert artifact_event.event_id in prediction.evidence_event_ids


def test_prediction_gate_rate_and_stability() -> None:
    buffer = ObservationBuffer(max_events=100)
    buffer.add_event(kind="section", value="chat")
    prediction = quick_predict(buffer.events(), now=10.0)
    gate = PredictionGate(min_interval_seconds=3.0, stable_ms=500)
    first = gate.evaluate(prediction=prediction, signature="chat|ai:tutor|chat", now=10.0, selected_artifact=True)
    assert first.allow_worker_request is True
    second = gate.evaluate(prediction=prediction, signature="chat|ai:tutor|chat", now=11.0, selected_artifact=True)
    assert second.allow_worker_request is False
    assert second.reason == "rate_limited"
