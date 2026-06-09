from __future__ import annotations

import json

from agent.artifacts.goal_artifact_service import GoalArtifactService
from client_surfaces.operator_tui import chat_state as chat_state_utils
from client_surfaces.operator_tui.actions import dispatch_action, parse_action
from client_surfaces.operator_tui.ai_snake_config_view import chat_model_option_label
from client_surfaces.operator_tui.ai_snake_context import explain_goal_artifact_graph, get_ai_context
from client_surfaces.operator_tui.ai_snake_learning import apply_prediction_feedback, event_for_prediction_feedback
from client_surfaces.operator_tui.ai_snake_training_store import (
    append_behavior_event,
    build_training_bundle,
    compact_training_data,
    data_path_status,
    data_show_status,
    delete_events,
    delete_patterns,
    pattern_detail,
    patterns_status_lines,
    read_active_profile,
    read_patterns,
    reset_training_data,
    save_active_profile,
    save_patterns,
)
from client_surfaces.operator_tui.ai_snake_training_import_export import (
    export_training_bundle_to_path,
    export_training_markdown,
    import_training_bundle,
)
from client_surfaces.operator_tui.browser import browser_fallback_url
from client_surfaces.operator_tui.models import CommandResult, OperatorMode, OperatorState
from client_surfaces.operator_tui.sections import section_ids

def _resolve_chat_ask_timeout_seconds(game: dict[str, object]) -> float:
    configured = game.get("chat_ask_timeout_s")
    if isinstance(configured, (int, float)):
        return max(3.0, min(180.0, float(configured)))
    if isinstance(configured, str) and configured.strip():
        try:
            return max(3.0, min(180.0, float(configured.strip())))
        except ValueError:
            pass
    timeout_raw = str(__import__("os").environ.get("ANANTA_TUI_CHAT_ASK_TIMEOUT") or __import__("os").environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT") or "45").strip()
    try:
        timeout_s = float(timeout_raw)
    except ValueError:
        timeout_s = 45.0
    return max(3.0, min(180.0, timeout_s))



def handle_ai_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = str(args[0]).lower() if args else "status"
    game = dict(state.header_logo_game or {})
    ai_mode = str(game.get("ai_snake_mode") or "lurking_follow")
    if sub == "explain" and len(args) > 1 and str(args[1]).lower() == "artifact-graph":
        goal_id = str(game.get("active_goal_id") or "").strip()
        if not goal_id:
            return CommandResult(state, "ai explain artifact-graph requires active goal", handled=False)
        graph = GoalArtifactService().get_goal_graph(goal_id)
        text = explain_goal_artifact_graph(graph)
        chat = chat_state_utils.get_chat_state(game)
        chat_state_utils.append_artifact_graph_explanation(chat, text=text, goal_id=goal_id)
        game["chat_state"] = chat
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"ai explain artifact-graph {goal_id}"),
            text,
        )
    if sub in {"follow", "lurk", "quiet", "explain", "off"}:
        mapping = {
            "follow": "follow",
            "lurk": "lurking",
            "quiet": "quiet",
            "explain": "point_to_target",
            "off": "off",
        }
        ai_mode = mapping[sub]
        game["ai_snake_mode"] = ai_mode
        if sub == "explain":
            game["ai_force_question"] = True
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"ai mode: {ai_mode}"),
            f"ai mode {ai_mode}",
        )
    if sub == "ctx":
        ctx = get_ai_context(game)
        env = game.get("ai_snake_context_envelope")
        ctx_hash = str((env or {}).get("context_hash") or "missing")
        refs = list((env or {}).get("retrieval_refs") or [])
        preview = ", ".join(str(item.get("ref") or "") for item in refs[:3] if isinstance(item, dict))
        if len(refs) > 3:
            preview += f" +{len(refs) - 3}"
        detail = preview or "degraded/missing"
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                status_message=f"ctx: codecompass:{ctx_hash} {detail} src={ctx.get('context_sources_display') or 'none'}",
            ),
            "ai ctx",
        )
    if sub == "context":
        scope = str(args[1]).lower() if len(args) > 1 else ""
        opt = str(args[2]).lower() if len(args) > 2 else ""
        if scope == "training":
            released = opt == "on"
            game["ai_training_context_released"] = released
            label = "on" if released else "off"
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=f"ai context training {label}"),
                f"ai context training {label}",
            )
        return CommandResult(state, "ai context training on|off", handled=False)
    if sub == "status":
        prediction = game.get("ai_snake_prediction") if isinstance(game.get("ai_snake_prediction"), dict) else {}
        debug = game.get("ai_snake_debug") if isinstance(game.get("ai_snake_debug"), dict) else {}
        trace = debug.get("last_prediction_trace") if isinstance(debug.get("last_prediction_trace"), dict) else {}
        active_patterns = list(debug.get("active_pattern_refs") or []) if isinstance(debug.get("active_pattern_refs"), list) else []
        learned = "yes" if active_patterns else "no"
        last_pattern = "-"
        if active_patterns and isinstance(active_patterns[0], dict):
            last_pattern = str(active_patterns[0].get("pattern_id") or "-")
        source = str(debug.get("prediction_source") or "quick")
        pred_intent = str(prediction.get("predicted_intent") or "unknown")
        pred_conf = float(prediction.get("confidence") or 0.0)
        runtime = str(game.get("ai_snake_runtime_status") or "idle")
        trace_id = str(trace.get("prediction_id") or "none")
        cache_state = str(trace.get("cache_state") or "-")
        provider_ref = str(trace.get("provider_ref") or "-")
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                status_message=(
                    f"ai:{ai_mode}/{runtime} pred={pred_intent} conf={pred_conf:.2f} source={source} "
                    f"learned={learned} patterns={len(active_patterns)} last_pattern={last_pattern} "
                    f"trace={trace_id} cache={cache_state} provider={provider_ref}"
                ),
            ),
            "ai status",
        )
    if sub == "why":
        prediction = game.get("ai_snake_prediction") if isinstance(game.get("ai_snake_prediction"), dict) else {}
        debug = game.get("ai_snake_debug") if isinstance(game.get("ai_snake_debug"), dict) else {}
        trace = debug.get("last_prediction_trace") if isinstance(debug.get("last_prediction_trace"), dict) else {}
        refs = list(trace.get("used_refs") or []) if isinstance(trace, dict) else []
        source = str(debug.get("prediction_source") or "quick")
        active = list(debug.get("active_pattern_refs") or []) if isinstance(debug.get("active_pattern_refs"), list) else []
        matched = str(debug.get("matched_pattern_id") or "")
        evidence = []
        if matched:
            for item in active:
                if isinstance(item, dict) and str(item.get("pattern_id") or "") == matched:
                    evidence.append(str(item.get("ai_hint") or "")[:160])
                    break
        ref_preview = ", ".join(str(x) for x in refs[:3]) if refs else "none"
        msg = (
            f"why: source={source} intent={prediction.get('predicted_intent') or 'unknown'} "
            f"conf={float(prediction.get('confidence') or 0.0):.2f} "
            f"pattern={matched or '-'} refs={ref_preview}"
        )
        if evidence:
            msg += f" evidence={evidence[0]}"
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=msg[:240]),
            msg,
        )
    if sub == "data":
        action = str(args[1]).lower() if len(args) > 1 else "path"
        if action == "path":
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=data_path_status()),
                "ai data path",
            )
        if action == "show":
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=data_show_status()),
                "ai data show",
            )
        if action == "export":
            tail = [str(token).strip() for token in args[2:]]
            options = {token.lower() for token in tail}
            fmt = "json"
            if "--format" in options:
                try:
                    idx = [item.lower() for item in tail].index("--format")
                    fmt = str(tail[idx + 1]).lower() if idx + 1 < len(tail) else ""
                except ValueError:
                    fmt = ""
            if fmt != "json":
                return CommandResult(state, "ai data export supports --format json", handled=False)
            include_events = "--include-events" in options
            export_target = ""
            positional = [token for token in tail if not token.startswith("--")]
            if positional and "--format" in options:
                # ignore format value in positional list
                lowered = [token.lower() for token in tail]
                fidx = lowered.index("--format")
                format_value = tail[fidx + 1] if fidx + 1 < len(tail) else ""
                positional = [token for token in positional if token != format_value]
            if positional:
                export_target = positional[0]
            try:
                if "--stdout" in options or not export_target:
                    bundle = build_training_bundle(include_events=include_events)
                    manifest = bundle.get("privacy_manifest") if isinstance(bundle.get("privacy_manifest"), dict) else {}
                    warn = ""
                    if int(manifest.get("private_local") or 0) > 0:
                        warn = " warning=private_local_data"
                    return CommandResult(
                        state.with_updates(
                            header_logo_game=game,
                            status_message=f"ai data export stdout{warn}",
                        ),
                        json.dumps(bundle, ensure_ascii=False),
                    )
                target = export_training_bundle_to_path(output_path=export_target, include_events=include_events)
            except ValueError as exc:
                return CommandResult(
                    state.with_updates(header_logo_game=game, status_message=f"ai data export failed: {exc}"),
                    "ai data export failed",
                    handled=False,
                )
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=f"ai data export file={target}"),
                f"ai data export {target}",
            )
        if action == "export-md":
            if len(args) < 3:
                return CommandResult(state, "ai data export-md requires <path>", handled=False)
            md_path = str(args[2]).strip()
            json_ref = ""
            if "--json-ref" in [str(x).lower() for x in args[3:]]:
                tail = [str(x) for x in args[3:]]
                idx = [str(x).lower() for x in tail].index("--json-ref")
                json_ref = tail[idx + 1] if idx + 1 < len(tail) else ""
            target = export_training_markdown(output_path=md_path, json_ref=json_ref)
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=f"ai data export-md file={target}"),
                f"ai data export-md {target}",
            )
        if action == "import":
            if len(args) < 3:
                return CommandResult(
                    state,
                    "ai data import <path> [--preview] [--disabled] [--conflict keep_higher_confidence|overwrite|keep_local|merge_counters|import_disabled_copy] [--ignore-checksum]",
                    handled=False,
                )
            source = str(args[2]).strip()
            flags = [str(x).strip() for x in args[3:]]
            lowered = [x.lower() for x in flags]
            preview = "--preview" in lowered
            disabled = "--disabled" in lowered
            ignore_checksum = "--ignore-checksum" in lowered or "--unsafe" in lowered
            strategy = "keep_higher_confidence"
            if "--conflict" in lowered:
                idx = lowered.index("--conflict")
                strategy = str(flags[idx + 1]).strip() if idx + 1 < len(flags) else strategy
            try:
                result = import_training_bundle(
                    input_path=source,
                    preview=preview,
                    disabled=disabled,
                    conflict_strategy=strategy,
                    ignore_checksum=ignore_checksum,
                )
            except ValueError as exc:
                return CommandResult(
                    state.with_updates(header_logo_game=game, status_message=f"ai data import failed: {exc}"),
                    "ai data import failed",
                    handled=False,
                )
            if str(result.get("status") or "") == "degraded":
                return CommandResult(
                    state.with_updates(
                        header_logo_game=game,
                        status_message=(
                            f"ai data import degraded readonly reason={result.get('reason')} "
                            f"schema={result.get('schema_version')}"
                        ),
                    ),
                    "ai data import degraded",
                    handled=False,
                )
            mode = "preview" if preview else "applied"
            checksum = result.get("checksum_state") if isinstance(result.get("checksum_state"), dict) else {}
            warning = str(checksum.get("warning") or "")
            warning_suffix = f" warning={warning}" if warning else ""
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    status_message=(
                        f"ai data import {mode} profile={result.get('profile_name')} "
                        f"patterns={result.get('patterns_result')} conflicts={result.get('conflicts')} "
                        f"strategy={result.get('conflict_resolution')}{warning_suffix}"
                    ),
                ),
                json.dumps(result, ensure_ascii=False),
            )
        if action == "compact":
            result = compact_training_data()
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    status_message=(
                        "ai data compact "
                        f"patterns={result['patterns_total']} "
                        f"events={result['event_before_bytes']}->{result['event_after_bytes']}"
                    ),
                ),
                "ai data compact",
            )
        if action == "delete":
            if len(args) < 3:
                return CommandResult(state, "ai data delete: events | patterns", handled=False)
            target = str(args[2]).lower()
            if target == "events":
                delete_events(backup=True)
                return CommandResult(
                    state.with_updates(header_logo_game=game, status_message="ai data delete events"),
                    "ai data delete events",
                )
            if target == "patterns":
                delete_patterns(backup=True)
                return CommandResult(
                    state.with_updates(header_logo_game=game, status_message="ai data delete patterns"),
                    "ai data delete patterns",
                )
            return CommandResult(state, "ai data delete: events | patterns", handled=False)
        if action == "reset":
            reset_training_data(backup=True)
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message="ai data reset"),
                "ai data reset",
            )
        return CommandResult(
            state,
            "ai data: path | show | export ... | export-md <path> | import <path> ... | compact | delete ... | reset",
            handled=False,
        )
    if sub == "prediction":
        if len(args) < 2:
            return CommandResult(state, "ai prediction: good | bad [reason]", handled=False)
        action = str(args[1]).lower()
        prediction = game.get("ai_snake_prediction") if isinstance(game.get("ai_snake_prediction"), dict) else {}
        target_ref = str(prediction.get("target_ref") or "")
        if not target_ref:
            return CommandResult(state, "ai prediction: no active target", handled=False)
        positive = action == "good"
        if action not in {"good", "bad"}:
            return CommandResult(state, "ai prediction: good | bad [reason]", handled=False)
        patterns = read_patterns()
        updated, changed = apply_prediction_feedback(patterns=patterns, target_ref=target_ref, positive=positive)
        if changed:
            save_patterns(updated, backup=True)
        reason = " ".join(args[2:]).strip()
        event = event_for_prediction_feedback(target_ref=target_ref, positive=positive, reason=reason)
        append_behavior_event(
            event_type=str(event.get("event_type") or "prediction_feedback"),
            value_norm=str(event.get("value_norm") or ""),
            refs=list(event.get("refs") or []),
            privacy_class=str(event.get("privacy_class") or "workspace"),
            retention_hint=str(event.get("retention_hint") or "rolling_30d"),
            reason=str(event.get("reason") or ""),
        )
        label = "good" if positive else "bad"
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"ai prediction {label}"),
            f"ai prediction {label}",
        )
    if sub == "patterns":
        lines = patterns_status_lines(max_items=8)
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=("patterns: " + " | ".join(lines))[:240]),
            "\n".join(lines),
        )
    if sub == "pattern":
        if len(args) < 2:
            return CommandResult(state, "ai pattern: <id> | explain <id> | enable <id> | disable <id> | delete <id>", handled=False)
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
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=detail[:240]),
                detail,
            )
        patterns = read_patterns()
        found = False
        updated: list[dict[str, object]] = []
        for item in patterns:
            copied = dict(item)
            if str(copied.get("pattern_id") or "") != pattern_id:
                updated.append(copied)
                continue
            found = True
            if op == "delete":
                continue
            copied["status"] = "active" if op == "enable" else "disabled"
            updated.append(copied)
        if not found:
            return CommandResult(state, f"pattern not found: {pattern_id}", handled=False)
        save_patterns(updated, backup=True)
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"ai pattern {op} {pattern_id}"),
            f"ai pattern {op} {pattern_id}",
        )
    if sub == "learning":
        action = str(args[1]).lower() if len(args) > 1 else "status"
        profile = read_active_profile()
        learning = dict(profile.get("learning_settings") or {})
        if action == "on":
            learning["enabled"] = True
            learning["paused"] = False
            profile["learning_settings"] = learning
            save_active_profile(profile, backup=True)
            game["ai_learning_session_paused"] = False
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message="ai learning on"),
                "ai learning on",
            )
        if action == "off":
            learning["enabled"] = False
            learning["paused"] = False
            profile["learning_settings"] = learning
            save_active_profile(profile, backup=True)
            game["ai_learning_session_paused"] = False
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message="ai learning off"),
                "ai learning off",
            )
        if action == "pause":
            game["ai_learning_session_paused"] = True
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message="ai learning paused"),
                "ai learning paused",
            )
        if action == "status":
            enabled = bool(learning.get("enabled"))
            paused = bool(learning.get("paused")) or bool(game.get("ai_learning_session_paused"))
            mode = "paused" if paused else ("active" if enabled else "off")
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=f"ai learning {mode} enabled={enabled}"),
                f"ai learning status: mode={mode} enabled={enabled}",
            )
        return CommandResult(state, "ai learning: on | off | pause | status", handled=False)
    return CommandResult(
        state,
        "ai: follow | lurk | quiet | explain | off | status | why | ctx | context training on|off | data ... | patterns | pattern ... | prediction ... | learning ...",
        handled=False,
    )
