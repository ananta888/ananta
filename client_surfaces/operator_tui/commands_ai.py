from __future__ import annotations

import os
from typing import Any, Callable

from agent.artifacts.goal_artifact_service import GoalArtifactService
from client_surfaces.operator_tui import chat_state as chat_state_utils
from client_surfaces.operator_tui.ai_snake_context import explain_goal_artifact_graph, get_ai_context
from client_surfaces.operator_tui.ai_snake_learning import apply_prediction_feedback, event_for_prediction_feedback
from client_surfaces.operator_tui.ai_snake_training_store import (
    append_behavior_event,
    pattern_detail,
    patterns_status_lines,
    read_active_profile,
    read_patterns,
    save_active_profile,
    save_patterns,
)
from client_surfaces.operator_tui.commands_ai_data import handle_ai_data_command
from client_surfaces.operator_tui.models import CommandResult, OperatorState


def _resolve_chat_ask_timeout_seconds(game: dict[str, object]) -> float:
    configured = game.get("chat_ask_timeout_s")
    if isinstance(configured, (int, float)):
        return max(3.0, min(180.0, float(configured)))
    if isinstance(configured, str) and configured.strip():
        try:
            return max(3.0, min(180.0, float(configured.strip())))
        except ValueError:
            pass
    timeout_raw = str(
        os.environ.get("ANANTA_TUI_CHAT_ASK_TIMEOUT") or os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT") or "45"
    ).strip()
    try:
        timeout_s = float(timeout_raw)
    except ValueError:
        timeout_s = 45.0
    return max(3.0, min(180.0, timeout_s))


AiSubcommandHandler = Callable[[list[str], OperatorState, dict[str, Any]], CommandResult]

AI_USAGE = (
    "ai: follow | lurk | quiet | explain | off | status | why | ctx | context training on|off | data ... | "
    "patterns | pattern ... | prediction ... | learning ..."
)

_AI_MODE_BY_SUBCOMMAND = {
    "follow": "follow",
    "lurk": "lurking",
    "quiet": "quiet",
    "explain": "point_to_target",
    "off": "off",
}


def _game_reply(state: OperatorState, game: dict[str, Any], status_message: str, output: str) -> CommandResult:
    return CommandResult(state.with_updates(header_logo_game=game, status_message=status_message), output)


def _dict_field(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    return value if isinstance(value, dict) else {}


def _list_field(container: dict[str, Any], key: str) -> list[Any]:
    value = container.get(key)
    return list(value or []) if isinstance(value, list) else []


def _explain_artifact_graph(state: OperatorState, game: dict[str, Any]) -> CommandResult:
    goal_id = str(game.get("active_goal_id") or "").strip()
    if not goal_id:
        return CommandResult(state, "ai explain artifact-graph requires active goal", handled=False)
    graph = GoalArtifactService().get_goal_graph(goal_id)
    text = explain_goal_artifact_graph(graph)
    chat = chat_state_utils.get_chat_state(game)
    chat_state_utils.append_artifact_graph_explanation(chat, text=text, goal_id=goal_id)
    game["chat_state"] = chat
    return _game_reply(state, game, f"ai explain artifact-graph {goal_id}", text)


def _set_ai_mode(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    sub = str(args[0]).lower()
    ai_mode = _AI_MODE_BY_SUBCOMMAND[sub]
    game["ai_snake_mode"] = ai_mode
    if sub == "explain":
        game["ai_force_question"] = True
    return _game_reply(state, game, f"ai mode: {ai_mode}", f"ai mode {ai_mode}")


def _context_envelope_summary(game: dict[str, Any]) -> tuple[str, str]:
    env = game.get("ai_snake_context_envelope")
    ctx_hash = str((env or {}).get("context_hash") or "missing")
    refs = list((env or {}).get("retrieval_refs") or [])
    preview = ", ".join(str(item.get("ref") or "") for item in refs[:3] if isinstance(item, dict))
    if len(refs) > 3:
        preview += f" +{len(refs) - 3}"
    return ctx_hash, preview or "degraded/missing"


def _show_context(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    ctx = get_ai_context(game)
    ctx_hash, detail = _context_envelope_summary(game)
    status = f"ctx: codecompass:{ctx_hash} {detail} src={ctx.get('context_sources_display') or 'none'}"
    return _game_reply(state, game, status, "ai ctx")


def _training_context(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    scope = str(args[1]).lower() if len(args) > 1 else ""
    opt = str(args[2]).lower() if len(args) > 2 else ""
    if scope != "training":
        return CommandResult(state, "ai context training on|off", handled=False)
    released = opt == "on"
    game["ai_training_context_released"] = released
    label = "on" if released else "off"
    return _game_reply(state, game, f"ai context training {label}", f"ai context training {label}")


def _status(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    ai_mode = str(game.get("ai_snake_mode") or "lurking_follow")
    prediction = _dict_field(game, "ai_snake_prediction")
    debug = _dict_field(game, "ai_snake_debug")
    trace = _dict_field(debug, "last_prediction_trace")
    active_patterns = _list_field(debug, "active_pattern_refs")
    last_pattern = "-"
    if active_patterns and isinstance(active_patterns[0], dict):
        last_pattern = str(active_patterns[0].get("pattern_id") or "-")
    status = (
        f"ai:{ai_mode}/{str(game.get('ai_snake_runtime_status') or 'idle')} "
        f"pred={str(prediction.get('predicted_intent') or 'unknown')} "
        f"conf={float(prediction.get('confidence') or 0.0):.2f} "
        f"source={str(debug.get('prediction_source') or 'quick')} "
        f"learned={'yes' if active_patterns else 'no'} patterns={len(active_patterns)} last_pattern={last_pattern} "
        f"trace={str(trace.get('prediction_id') or 'none')} cache={str(trace.get('cache_state') or '-')} "
        f"provider={str(trace.get('provider_ref') or '-')}"
    )
    return _game_reply(state, game, status, "ai status")


def _matched_pattern_evidence(active: list[Any], matched: str) -> list[str]:
    if not matched:
        return []
    for item in active:
        if isinstance(item, dict) and str(item.get("pattern_id") or "") == matched:
            return [str(item.get("ai_hint") or "")[:160]]
    return []


def _why(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    prediction = _dict_field(game, "ai_snake_prediction")
    debug = _dict_field(game, "ai_snake_debug")
    trace = _dict_field(debug, "last_prediction_trace")
    refs = list(trace.get("used_refs") or [])
    matched = str(debug.get("matched_pattern_id") or "")
    evidence = _matched_pattern_evidence(_list_field(debug, "active_pattern_refs"), matched)
    ref_preview = ", ".join(str(x) for x in refs[:3]) if refs else "none"
    msg = (
        f"why: source={str(debug.get('prediction_source') or 'quick')} "
        f"intent={prediction.get('predicted_intent') or 'unknown'} "
        f"conf={float(prediction.get('confidence') or 0.0):.2f} "
        f"pattern={matched or '-'} refs={ref_preview}"
    )
    if evidence:
        msg += f" evidence={evidence[0]}"
    return _game_reply(state, game, msg[:240], msg)


def _record_prediction_feedback(target_ref: str, positive: bool, reason: str) -> None:
    patterns = read_patterns()
    updated, changed = apply_prediction_feedback(patterns=patterns, target_ref=target_ref, positive=positive)
    if changed:
        save_patterns(updated, backup=True)
    event = event_for_prediction_feedback(target_ref=target_ref, positive=positive, reason=reason)
    append_behavior_event(
        event_type=str(event.get("event_type") or "prediction_feedback"),
        value_norm=str(event.get("value_norm") or ""),
        refs=list(event.get("refs") or []),
        privacy_class=str(event.get("privacy_class") or "workspace"),
        retention_hint=str(event.get("retention_hint") or "rolling_30d"),
        reason=str(event.get("reason") or ""),
    )


def _prediction_feedback(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    if len(args) < 2:
        return CommandResult(state, "ai prediction: good | bad [reason]", handled=False)
    action = str(args[1]).lower()
    target_ref = str(_dict_field(game, "ai_snake_prediction").get("target_ref") or "")
    if not target_ref:
        return CommandResult(state, "ai prediction: no active target", handled=False)
    if action not in {"good", "bad"}:
        return CommandResult(state, "ai prediction: good | bad [reason]", handled=False)
    positive = action == "good"
    _record_prediction_feedback(target_ref, positive, " ".join(args[2:]).strip())
    label = "good" if positive else "bad"
    return _game_reply(state, game, f"ai prediction {label}", f"ai prediction {label}")


def _list_patterns(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    lines = patterns_status_lines(max_items=8)
    return _game_reply(state, game, ("patterns: " + " | ".join(lines))[:240], "\n".join(lines))


def _patterns_after(op: str, pattern_id: str) -> tuple[list[dict[str, object]], bool]:
    """The stored patterns after enabling, disabling or deleting ``pattern_id``."""
    found = False
    updated: list[dict[str, object]] = []
    for item in read_patterns():
        copied = dict(item)
        if str(copied.get("pattern_id") or "") != pattern_id:
            updated.append(copied)
            continue
        found = True
        if op == "delete":
            continue
        copied["status"] = "active" if op == "enable" else "disabled"
        updated.append(copied)
    return updated, found


def _pattern(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    if len(args) < 2:
        return CommandResult(
            state, "ai pattern: <id> | explain <id> | enable <id> | disable <id> | delete <id>", handled=False
        )
    op = str(args[1]).lower()
    if op in {"explain", "enable", "disable", "delete"}:
        if len(args) < 3:
            return CommandResult(state, f"ai pattern {op} requires an id", handled=False)
        pattern_id = str(args[2]).strip()
    else:
        pattern_id = str(args[1]).strip()
        op = "show"
    if op in {"show", "explain"}:
        detail = pattern_detail(pattern_id)
        return _game_reply(state, game, detail[:240], detail)
    updated, found = _patterns_after(op, pattern_id)
    if not found:
        return CommandResult(state, f"pattern not found: {pattern_id}", handled=False)
    save_patterns(updated, backup=True)
    return _game_reply(state, game, f"ai pattern {op} {pattern_id}", f"ai pattern {op} {pattern_id}")


def _switch_learning(enabled: bool, game: dict[str, Any], profile: dict[str, Any], learning: dict[str, Any]) -> None:
    learning["enabled"] = enabled
    learning["paused"] = False
    profile["learning_settings"] = learning
    save_active_profile(profile, backup=True)
    game["ai_learning_session_paused"] = False


def _learning(args: list[str], state: OperatorState, game: dict[str, Any]) -> CommandResult:
    action = str(args[1]).lower() if len(args) > 1 else "status"
    profile = read_active_profile()
    learning = dict(profile.get("learning_settings") or {})
    if action in {"on", "off"}:
        _switch_learning(action == "on", game, profile, learning)
        return _game_reply(state, game, f"ai learning {action}", f"ai learning {action}")
    if action == "pause":
        game["ai_learning_session_paused"] = True
        return _game_reply(state, game, "ai learning paused", "ai learning paused")
    if action == "status":
        enabled = bool(learning.get("enabled"))
        paused = bool(learning.get("paused")) or bool(game.get("ai_learning_session_paused"))
        mode = "paused" if paused else ("active" if enabled else "off")
        return _game_reply(
            state, game, f"ai learning {mode} enabled={enabled}", f"ai learning status: mode={mode} enabled={enabled}"
        )
    return CommandResult(state, "ai learning: on | off | pause | status", handled=False)


AI_SUBCOMMANDS: dict[str, AiSubcommandHandler] = {
    **{sub: _set_ai_mode for sub in _AI_MODE_BY_SUBCOMMAND},
    "ctx": _show_context,
    "context": _training_context,
    "status": _status,
    "why": _why,
    "data": handle_ai_data_command,
    "prediction": _prediction_feedback,
    "patterns": _list_patterns,
    "pattern": _pattern,
    "learning": _learning,
}


def handle_ai_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = str(args[0]).lower() if args else "status"
    game = dict(state.header_logo_game or {})
    if sub == "explain" and len(args) > 1 and str(args[1]).lower() == "artifact-graph":
        return _explain_artifact_graph(state, game)
    handler = AI_SUBCOMMANDS.get(sub)
    if handler is None:
        return CommandResult(state, AI_USAGE, handled=False)
    return handler(args, state, game)
