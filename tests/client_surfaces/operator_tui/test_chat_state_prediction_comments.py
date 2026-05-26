from __future__ import annotations

from client_surfaces.operator_tui.chat_state import default_chat_state, maybe_add_prediction_comment


def test_prediction_comment_respects_cooldown_and_quiet_mode() -> None:
    chat = default_chat_state("s-op")
    prediction = {"predicted_intent": "artifact_explain", "target_ref": "renderer.py", "confidence": 0.9}
    assert maybe_add_prediction_comment(chat, prediction=prediction, now=100.0, quiet=False) is True
    assert maybe_add_prediction_comment(chat, prediction=prediction, now=105.0, quiet=False) is False
    assert maybe_add_prediction_comment(chat, prediction=prediction, now=130.0, quiet=True) is False


def test_prediction_comment_forced_question_overrides_quiet_and_confidence() -> None:
    chat = default_chat_state("s-op")
    prediction = {"predicted_intent": "chat", "target_ref": "section:tasks", "confidence": 0.2}
    assert maybe_add_prediction_comment(chat, prediction=prediction, now=200.0, quiet=True, forced=True) is True
