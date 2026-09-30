"""Steps of the AI-snake prediction tick that need no TUI instance.

``SnakeTickMixin._tick_ai_snake_prediction`` orchestrates one prediction
step; the pure or game-dict-only parts live here: movement classification,
the context envelope, privacy-policy payloads, the worker gate, follow-state
stepping, worker overrides, the runtime status, pattern matching and implicit
target feedback.
"""
from __future__ import annotations

from typing import Any, cast

from client_surfaces.operator_tui.ai_snake_context import (
    build_context_envelope_ref,
    default_ai_context,
    relevance_refs_for_intent,
    set_ai_context,
    training_profile_envelope,
)
from client_surfaces.operator_tui.ai_snake_follow import (
    apply_worker_follow_update,
    make_follow_state,
    step_follow_state,
)
from client_surfaces.operator_tui.ai_snake_learning import apply_prediction_feedback
from client_surfaces.operator_tui.ai_snake_policy import apply_policy_to_payload
from client_surfaces.operator_tui.ai_snake_training_store import (
    append_behavior_event,
    read_patterns,
    save_patterns,
)


def notes_context_released(game: dict[str, object]) -> bool:
    chat_state = game.get("chat_state")
    return bool((chat_state or {}).get("notes_context_released")) if isinstance(chat_state, dict) else False


def movement_direction(vx: float, vy: float) -> str:
    if abs(vx) >= abs(vy):
        return "right" if vx > 0.25 else ("left" if vx < -0.25 else "idle")
    return "down" if vy > 0.25 else ("up" if vy < -0.25 else "idle")


def prediction_context_envelope(
    game: dict[str, object], prediction: dict, *, codecompass: Any, artifact_ref: Any
) -> dict:
    """Context envelope of the prediction: CodeCompass relevance refs plus the training profile."""
    ai_ctx = default_ai_context()
    set_ai_context(game, ai_ctx)
    envelope = build_context_envelope_ref(ai_ctx, codecompass_artifact=codecompass, selected_artifact_ref=artifact_ref)
    intent = str(prediction.get("predicted_intent") or "unknown")
    envelope["retrieval_refs"] = relevance_refs_for_intent(intent=intent, codecompass_artifact=codecompass, max_refs=12)
    training_ctx = training_profile_envelope(
        intent=str(prediction.get("predicted_intent") or "unknown"),
        max_patterns=int(game.get("ai_snake_training_max_patterns") or 8),
    )
    envelope["training_profile_ref"] = training_ctx.get("training_profile_ref")
    envelope["active_pattern_refs"] = training_ctx.get("active_pattern_refs")
    return envelope


def ai_policy_payloads(game: dict[str, object], prediction: dict, envelope: dict, summary: Any, *, artifact_ref: Any):
    """Apply the privacy policy to the worker request and the LM Studio prompt payloads."""
    selected_allowed = isinstance(artifact_ref, dict) or str(prediction.get("target_ref") or "").startswith("section:")
    notes_released = notes_context_released(game)
    notes_context = (game.get("chat_state") or {}).get("notes_context")
    policy_kwargs = {
        "notes_released": notes_released,
        "selected_artifact_allowed": selected_allowed,
        "external_provider": False,
        "training_context_allowed": bool(game.get("ai_training_context_released")),
    }
    worker_payload, worker_policy = apply_policy_to_payload(
        {
            "mode": str(game.get("ai_snake_mode") or "lurking_follow"),
            "quick_prediction": prediction,
            "context_envelope_ref": envelope,
            "observation_summary": summary,
            "notes_context": notes_context,
        },
        boundary="worker_request",
        **policy_kwargs,
    )
    prompt_payload, prompt_policy = apply_policy_to_payload(
        {
            "quick_prediction": prediction,
            "observation_summary": summary,
            "notes_context": (game.get("chat_state") or {}).get("notes_context"),
        },
        boundary="lmstudio_prompt",
        **policy_kwargs,
    )
    return worker_payload, worker_policy, prompt_payload, prompt_policy


def may_request_worker(game: dict[str, object], gate_decision: Any, cache_hit: bool, worker_policy: Any,
                        worker_payload: Any) -> bool:
    return bool(
        gate_decision.allow_worker_request
        and not cache_hit
        and worker_policy.allowed
        and str(game.get("ai_snake_mode") or "lurking_follow") != "off"
        and isinstance(worker_payload, dict)
        and not bool(worker_payload.get("blocked"))
    )


def append_worker_timeout_notice(game: dict[str, object]) -> None:
    from client_surfaces.operator_tui.chat_state import (
        ChannelType,
        DeliveryState,
        SenderKind,
        append_message,
        make_message,
    )

    msg = make_message(
        channel_id="ai:tutor",
        channel_type=ChannelType.AI,
        sender_id="system",
        sender_kind=SenderKind.SYSTEM,
        text="* [system] AI worker timeout – nutze lokale Prediction.",
        delivery_state=DeliveryState.RECEIVED,
    )
    append_message(cast(dict[str, Any], game["chat_state"]), msg)


def stepped_follow_state(game: dict[str, object], ai_mode: str) -> dict:
    follow_state_raw = game.get("ai_snake_follow_state")
    follow_state = dict(follow_state_raw) if isinstance(follow_state_raw, dict) else make_follow_state(mode=ai_mode)
    local_snake = game.get("snake")
    if not (isinstance(local_snake, list) and local_snake):
        return follow_state
    head = local_snake[0]
    if not (isinstance(head, (list, tuple)) and len(head) == 2):
        return follow_state
    follow_state["mode"] = ai_mode
    return step_follow_state(
        follow_state,
        user_position=(int(head[0]), int(head[1])),
        board_w=max(1, int(game.get("board_w") or 18)),
        board_h=max(1, int(game.get("board_h") or 6)),
    )


def apply_worker_prediction(game: dict[str, object], prediction: dict, follow_state: dict, *, now: float):
    """Let a fresh, confident worker response override the quick prediction; expire stale ones."""
    response = game.get("ai_snake_worker_response")
    if not (isinstance(response, dict) and str(response.get("status") or "ok") == "ok"):
        return prediction, follow_state
    if float(response.get("expires_at") or 0.0) < now:
        game["ai_snake_worker_response"] = {"status": "degraded", "error": "stale_result"}
        return prediction, follow_state
    if float(response.get("confidence") or 0.0) < 0.65:
        return prediction, follow_state
    prediction = {
        **prediction,
        "predicted_intent": str(response.get("predicted_intent") or prediction.get("predicted_intent") or "unknown"),
        "target_ref": str(response.get("target_ref") or prediction.get("target_ref") or ""),
        "confidence": float(response.get("confidence") or prediction.get("confidence") or 0.0),
        "expires_at": float(response.get("expires_at") or prediction.get("expires_at") or now + 20.0),
    }
    follow_state = apply_worker_follow_update(
        follow_state,
        follow_mode_update=str(response.get("follow_mode_update") or ""),
        prediction_target=str(response.get("target_ref") or ""),
        confidence=float(response.get("confidence") or 0.0),
    )
    return prediction, follow_state


def ai_runtime_status(
    ai_mode: str,
    *,
    worker_response: dict,
    worker_pending: bool,
    cache_hit: bool,
    gate_reason: str,
    follow_state: dict,
) -> str:
    """Status shown in the header, in priority order."""
    follow_mode = str(follow_state.get("mode") or "")
    rules = (
        (ai_mode == "off", "off"),
        (str(worker_response.get("status") or "") == "degraded", "degraded"),
        (worker_pending, "thinking"),
        (cache_hit, "context-ready"),
        (gate_reason in {"prediction_not_stable", "rate_limited"}, "predicting"),
        (ai_mode == "quiet", "quiet"),
        (follow_mode == "follow", "following"),
        (follow_mode == "lurking", "lurking"),
    )
    return next((status for matches, status in rules if matches), "idle")


def find_matched_pattern_id(active_pattern_refs: list, prediction: dict) -> str:
    intent = str(prediction.get("predicted_intent") or "").strip().lower()
    for item in active_pattern_refs:
        if isinstance(item, dict) and str(item.get("predicted_intent") or "").strip().lower() == intent:
            return str(item.get("pattern_id") or "")
    if active_pattern_refs and isinstance(active_pattern_refs[0], dict):
        return str(active_pattern_refs[0].get("pattern_id") or "")
    return ""


def resolve_prediction_source(matched_pattern_id: str, worker_response: Any) -> str:
    if isinstance(worker_response, dict) and str(worker_response.get("status") or "") == "ok":
        return "worker_context"
    return "learned_profile" if matched_pattern_id else "local_quick"


def target_reached(target_ref: str, section: str, artifact_ref: Any) -> bool:
    if target_ref.startswith("section:"):
        return target_ref.removeprefix("section:") == section
    if target_ref and isinstance(artifact_ref, dict):
        artifact_path = str(artifact_ref.get("path") or artifact_ref.get("label") or "")
        return bool(artifact_path) and artifact_path in target_ref
    return False


def record_implicit_target_feedback(game: dict[str, object], prediction: dict, *, section: str,
                                     artifact_ref: Any) -> None:
    """Reaching the predicted target counts as positive feedback, once per target and section."""
    target_ref = str(prediction.get("target_ref") or "")
    auto_feedback_key = f"{target_ref}|{section}"
    if not (
        target_reached(target_ref, section, artifact_ref)
        and target_ref
        and str(game.get("ai_last_auto_feedback_key") or "") != auto_feedback_key
    ):
        return
    patterns = read_patterns()
    updated, changed = apply_prediction_feedback(patterns=patterns, target_ref=target_ref, positive=True)
    if changed:
        save_patterns(updated, backup=False)
    append_behavior_event(
        event_type="prediction_feedback",
        value_norm="implicit_good",
        refs=[target_ref],
        privacy_class="workspace",
        retention_hint="rolling_30d",
        reason="target_reached",
    )
    game["ai_last_auto_feedback_key"] = auto_feedback_key
