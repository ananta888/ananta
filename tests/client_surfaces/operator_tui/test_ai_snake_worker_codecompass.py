from __future__ import annotations

import time

from client_surfaces.operator_tui.ai_snake_context import (
    build_context_envelope_ref,
    default_ai_context,
    relevance_refs_for_intent,
)
from client_surfaces.operator_tui.ai_snake_worker_client import AiSnakeWorkerClient


def test_build_request_contract_contains_required_fields() -> None:
    client = AiSnakeWorkerClient()
    payload = client.build_request(
        mode="predict_intent",
        observation_summary={"facts": ["section=artifacts"]},
        quick_prediction={"predicted_intent": "artifact_explain", "confidence": 0.62},
        context_envelope_ref={"context_hash": "ctx-operator-tui-snake-v1"},
    )
    assert payload["request_id"]
    assert payload["mode"] == "predict_intent"
    assert "observation_summary" in payload
    assert "quick_prediction" in payload
    assert "context_envelope_ref" in payload
    assert payload["budget"]["token_budget"] >= 64


def test_predict_request_is_single_inflight() -> None:
    def slow_dispatch(payload: dict) -> dict:
        time.sleep(0.2)
        return {
            "predicted_intent": "chat",
            "confidence": 0.6,
            "target_ref": "ai:tutor",
            "answer_text": "ok",
            "context_refs": [],
            "follow_mode_update": "lurking_follow",
            "expires_at": 999.0,
        }

    client = AiSnakeWorkerClient(dispatch=slow_dispatch)
    first = client.submit(client.build_request(mode="predict_intent", observation_summary={}, quick_prediction={}, context_envelope_ref={}))
    second = client.submit(client.build_request(mode="predict_intent", observation_summary={}, quick_prediction={}, context_envelope_ref={}))
    assert first is not None
    assert second is None


def test_context_envelope_and_relevance_refs() -> None:
    ctx = default_ai_context()
    artifact = {
        "context_hash": "ctx-operator-tui-snake-v1",
        "refs": [
            {"ref": "client_surfaces/operator_tui/renderer.py", "reason": "artifact view", "score": 0.7},
            {"ref": "client_surfaces/operator_tui/chat_state.py", "reason": "chat transport", "score": 0.5},
        ],
    }
    envelope = build_context_envelope_ref(ctx, codecompass_artifact=artifact, selected_artifact_ref={"path": "x.py"})
    assert envelope["context_hash"] == "ctx-operator-tui-snake-v1"
    refs = relevance_refs_for_intent(intent="artifact_explain", codecompass_artifact=artifact, max_refs=12)
    assert refs
    assert refs[0]["score"] >= refs[-1]["score"]
