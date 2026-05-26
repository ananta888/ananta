from __future__ import annotations

from client_surfaces.operator_tui.ai_snake_lm_budget import AiSnakeLmBudget
from client_surfaces.operator_tui.ai_snake_worker_client import parse_worker_response, repair_and_parse_response


def test_lm_budget_rate_size_and_reset() -> None:
    budget = AiSnakeLmBudget(predict_window_seconds=3.0, max_predict_in_window=1, max_prompt_chars=32)
    allowed, reason = budget.allow_predict(prompt="hello", now=10.0)
    assert allowed is True
    denied, denied_reason = budget.allow_predict(prompt="again", now=10.5)
    assert denied is False
    assert denied_reason == "predict_rate_limited"
    reset_allowed, _ = budget.allow_predict(prompt="ok", now=14.2)
    assert reset_allowed is True
    too_large, too_large_reason = budget.allow_predict(prompt="x" * 120, now=20.0)
    assert too_large is False
    assert too_large_reason == "prompt_too_large"


def test_parse_worker_response_handles_fenced_and_invalid_json() -> None:
    fenced = {
        "response_text": """```json
{"predicted_intent":"chat","confidence":0.6,"target_ref":"ai:tutor","answer_text":"ok","context_refs":[],"follow_mode_update":"lurking_follow","expires_at":123.0}
```"""
    }
    parsed = parse_worker_response(fenced)
    assert parsed["status"] == "ok"
    assert parsed["predicted_intent"] == "chat"

    invalid = {"response_text": "not-json"}
    degraded = parse_worker_response(invalid)
    assert degraded["status"] == "degraded"


def test_repair_path_runs_once() -> None:
    broken = "intent=chat confidence=0.4"

    def repair(_: str) -> str:
        return '{"predicted_intent":"chat","confidence":0.4,"target_ref":"ai:tutor","answer_text":"ok","context_refs":[],"follow_mode_update":"lurking_follow","expires_at":123.0}'

    parsed = repair_and_parse_response(broken, repair_fn=repair)
    assert parsed["status"] == "ok"
    assert parsed["confidence"] == 0.4
