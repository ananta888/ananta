from __future__ import annotations

import json
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from agent.artifacts.artifact_access_policy import ArtifactAccessPolicy
from agent.artifacts.artifact_candidate_service import ArtifactCandidateService
from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
from agent.sources.citation_formatter import format_citation
from agent.sources.builtin_sources import load_builtin_source_descriptors
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore
from client_surfaces.operator_tui.actions import dispatch_action, parse_action
from client_surfaces.operator_tui.ai_snake_learning import apply_prediction_feedback, event_for_prediction_feedback
from client_surfaces.operator_tui.browser import browser_fallback_url
from client_surfaces.operator_tui.ai_snake_context import get_ai_context
from client_surfaces.operator_tui.goal_artifact_filters import (
    filter_goal_artifact_view,
    normalize_goal_artifact_filters,
)
from client_surfaces.operator_tui.ai_snake_context import explain_goal_artifact_graph
from client_surfaces.operator_tui.ai_snake_training_import_export import (
    export_training_bundle_to_path,
    export_training_markdown,
    import_training_bundle,
)
from client_surfaces.operator_tui import chat_state as chat_state_utils
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
    save_patterns,
    save_active_profile,
)
from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState, PanelState
from client_surfaces.operator_tui.sections import move_section, normalize_section_id, section_ids
from client_surfaces.operator_tui.diff.ai_diff_dispatch import dispatch_ai_diff_request
from client_surfaces.operator_tui.diff.ai_diff_panel_state import build_ai_diff_panel_state, set_ai_diff_mode
from client_surfaces.operator_tui.diff.diff_engine import DiffEngine
from client_surfaces.operator_tui.diff.diff_source_resolver import DiffSourceResolver
from client_surfaces.operator_tui.diff.diff_sources import build_current_diff_source_ref, build_output_artifact_source_ref
from client_surfaces.operator_tui.diff.three_way_diff_state import (
    build_current_diff_three_panel_session,
    set_panel_state,
    validate_three_way_diff_session,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _active_goal_id(state: OperatorState) -> str:
    game = dict(state.header_logo_game or {})
    return str(game.get("active_goal_id") or "").strip()


def _require_active_goal(state: OperatorState) -> tuple[str | None, CommandResult | None]:
    goal_id = _active_goal_id(state)
    if goal_id:
        return goal_id, None
    return None, CommandResult(state, "goal command requires active goal (:goal use <goal-id>)", handled=False)


def _load_goal_artifact_payload(*, state: OperatorState, goal_id: str) -> dict:
    service = GoalArtifactService()
    graph = service.get_goal_graph(goal_id)
    provenance_items = list(dict(graph.get("extensions") or {}).get("execution_provenance") or [])
    provenance_by_id = {
        str(item.get("provenance_id") or ""): item
        for item in provenance_items
        if isinstance(item, dict) and str(item.get("provenance_id") or "").strip()
    }
    outputs = []
    for row in list(graph.get("output_artifacts") or []):
        item = dict(row) if isinstance(row, dict) else {}
        provenance = provenance_by_id.get(str(item.get("provenance_id") or ""), {})
        prompt_refs = dict(provenance.get("prompt_refs") or {})
        runtime_ref = dict(provenance.get("runtime_target_ref") or {})
        model_ref = dict(provenance.get("model_ref") or {})
        item["prompt_template_ref"] = str(prompt_refs.get("prompt_template_ref") or "")
        item["model_ref"] = str(model_ref.get("model_id") or "")
        item["runtime_ref"] = str(runtime_ref.get("runtime_type") or "")
        item["execution_summary"] = (
            f"task={item.get('task_id') or '-'} worker={item.get('worker_id') or '-'} "
            f"runtime={item.get('runtime_ref') or '-'} model={item.get('model_ref') or '-'} "
            f"prompt={item.get('prompt_template_ref') or '-'}"
        )
        outputs.append(item)
    filters = normalize_goal_artifact_filters(dict((state.header_logo_game or {}).get("goal_artifact_filters") or {}))
    filtered = filter_goal_artifact_view(
        source_grants=list(graph.get("source_grants") or []),
        source_usages=list(graph.get("source_usages") or []),
        output_artifacts=outputs,
        filters=filters,
    )
    return {
        "goal_artifacts_mode": True,
        "goal_id": goal_id,
        "filters": filters,
        **filtered,
    }


def _get_diff3_state(state: OperatorState) -> dict:
    game = dict(state.header_logo_game or {})
    current = game.get("diff3_state")
    if isinstance(current, dict) and not validate_three_way_diff_session(current):
        return dict(current)
    return build_current_diff_three_panel_session(
        session_id="diff3-default",
        goal_id=str(game.get("active_goal_id") or "") or None,
    )


def _build_diff3_payload(*, state: dict, goal_id: str | None) -> dict:
    resolver = DiffSourceResolver(repo_root=Path.cwd(), goal_artifact_service=GoalArtifactService())
    engine = DiffEngine()
    summaries: list[dict[str, object]] = []
    for panel in list(state.get("panels") or []):
        panel_id = str(panel.get("panel_id") or "?")
        panel_type = str(panel.get("panel_type") or "empty")
        render_mode = str(panel.get("render_mode") or "")
        source = panel.get("source_left") if isinstance(panel.get("source_left"), dict) else None
        source_label = str((source or {}).get("display_name") or panel_type)
        status = "empty"
        stats: dict[str, object] = {}
        if source and panel_type == "diff":
            resolved = resolver.resolve(source, goal_id=goal_id)
            if bool(resolved.get("ok")):
                status = "ready"
                doc = engine.build_document(left=resolved, render_mode=render_mode)
                stats = dict(doc.get("stats") or {})
                if str(source.get("source_kind") or "") == "goal_output_artifact":
                    output_ref = str(resolved.get("output_artifact_id") or "")
                    prov = str(resolved.get("provenance_id") or "")
                    source_label = f"{source_label}#{output_ref}" if output_ref else source_label
                    if prov:
                        source_label = f"{source_label} prov={prov}"
            else:
                status = str(resolved.get("reason_code") or "degraded")
        elif panel_type.startswith("ai_"):
            ai_state = dict(dict(state.get("extensions") or {}).get("ai_panel_state") or {})
            status = str(ai_state.get("status") or "ready")
        summaries.append(
            {
                "panel_id": panel_id,
                "panel_type": panel_type,
                "source_label": source_label,
                "render_mode": render_mode,
                "status": status,
                "stats": stats,
                "filters": dict(panel.get("filters") or {}),
            }
        )
    return {
        "diff3_mode": True,
        "goal_id": goal_id,
        "active_panel": str(state.get("active_panel") or "A"),
        "sync_scroll": bool(dict(state.get("extensions") or {}).get("sync_scroll", False)),
        "ai_panel_state": dict(dict(state.get("extensions") or {}).get("ai_panel_state") or {}),
        "panel_summaries": summaries,
        "raw_state": state,
    }


def _state_with_diff3_payload(state: OperatorState, *, game: dict, diff3_state: dict) -> OperatorState:
    goal_id = str(game.get("active_goal_id") or "").strip() or None
    game["diff3_state"] = diff3_state
    payload = _build_diff3_payload(state=diff3_state, goal_id=goal_id)
    section_payloads = dict(state.section_payloads or {})
    section_payloads["artifacts"] = payload
    panel_states = dict(state.panel_states or {})
    panel_states["artifacts"] = PanelState.HEALTHY
    return state.with_updates(
        header_logo_game=game,
        section_id="artifacts",
        selected_index=0,
        section_payloads=section_payloads,
        panel_states=panel_states,
    )


def execute_command(raw_command: str, state: OperatorState) -> CommandResult:
    text = str(raw_command or "").strip()
    if text.startswith(":"):
        text = text[1:].strip()
    if not text:
        return CommandResult(state.with_updates(mode=OperatorMode.NORMAL, command_line=""), "empty command ignored")

    parts = text.split()
    command = parts[0].lower()
    args = parts[1:]

    if command in {"refresh", "r"}:
        return CommandResult(
            state.with_updates(
                mode=OperatorMode.NORMAL,
                command_line="",
                refresh_count=state.refresh_count + 1,
                status_message="refresh requested",
            ),
            "refresh requested",
        )
    if command in {"section", "open", "goto"}:
        if not args:
            return CommandResult(state.with_updates(mode=OperatorMode.COMMAND), "section command requires a section id")
        section_id = normalize_section_id(args[0])
        return CommandResult(
            state.with_updates(
                mode=OperatorMode.NORMAL,
                command_line="",
                section_id=section_id,
                selected_index=0,
                status_message=f"section {section_id}",
            ),
            f"opened section {section_id}",
        )
    if command == "next":
        section_id = move_section(state.section_id, 1)
        return CommandResult(state.with_updates(section_id=section_id, selected_index=0), f"opened section {section_id}")
    if command == "prev":
        section_id = move_section(state.section_id, -1)
        return CommandResult(state.with_updates(section_id=section_id, selected_index=0), f"opened section {section_id}")
    if command == "focus":
        if not args:
            return CommandResult(state, "focus command requires navigation, content, or detail")
        requested = args[0].lower()
        try:
            focus = FocusPane(requested)
        except ValueError:
            return CommandResult(state, f"unknown focus pane: {requested}", handled=False)
        return CommandResult(state.with_updates(focus=focus, status_message=f"focus {focus.value}"), f"focus {focus.value}")
    if command == "mode":
        if not args:
            return CommandResult(state, "mode command requires normal, command, inspect, or edit")
        requested = args[0].lower()
        try:
            mode = OperatorMode(requested)
        except ValueError:
            return CommandResult(state, f"unknown mode: {requested}", handled=False)
        return CommandResult(state.with_updates(mode=mode, status_message=f"mode {mode.value}"), f"mode {mode.value}")
    if command in {"help", "?"}:
        if args:
            sub = args[0].lower()
            if sub == "chat":
                msg = "chat: [c] focus | Esc game | :chat room|ai|@id | :channels | :chat retry"
                return CommandResult(state.with_updates(status_message=msg), "help chat")
            if sub == "notes":
                msg = "notes: :notes | :notes find <t> | :notes pin/unpin/delete <id> | LOCAL ONLY"
                return CommandResult(state.with_updates(status_message=msg), "help notes")
        return CommandResult(state.with_updates(show_help=not state.show_help, status_message="help toggled"), "help toggled")
    if command == "mouse":
        mode = (args[0].strip().lower() if args else "toggle")
        if mode not in {"on", "off", "toggle"}:
            return CommandResult(state, "mouse command requires on, off, or toggle", handled=False)
        game = dict(state.header_logo_game or {})
        current = bool(game.get("mouse_follow_enabled"))
        if mode == "toggle":
            next_value = not current
        else:
            next_value = mode == "on"
        game["mouse_follow_enabled"] = next_value
        game["movement_mode"] = "mouse_follow" if next_value else "keyboard"
        label = "on" if next_value else "off"
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"mouse-follow {label}"),
            f"mouse-follow {label}",
        )
    if command in {"snake-access", "snake_access"}:
        if len(args) < 2:
            return CommandResult(state, "snake-access requires: <snake-id> <cancel|view|full>", handled=False)
        snake_id = str(args[0]).strip()
        level = str(args[1]).strip().lower()
        if not snake_id:
            return CommandResult(state, "snake-access requires a snake id", handled=False)
        if level not in {"cancel", "view", "full"}:
            return CommandResult(state, "snake-access level must be cancel, view, or full", handled=False)
        game = dict(state.header_logo_game or {})
        local_id = str(game.get("local_snake_id") or "s1")
        if snake_id == local_id and level != "full":
            return CommandResult(state, "local snake must remain full", handled=False)
        remote_access_raw = game.get("remote_access")
        remote_access = dict(remote_access_raw) if isinstance(remote_access_raw, dict) else {}
        remote_access[snake_id] = level
        game["remote_access"] = remote_access

        snakes_raw = game.get("snakes")
        if isinstance(snakes_raw, dict):
            snakes = {str(k): dict(v) for k, v in snakes_raw.items() if isinstance(v, dict)}
            snap = dict(snakes.get(snake_id, {"id": snake_id}))
            snap["access_level"] = level
            snakes[snake_id] = snap
            game["snakes"] = snakes

        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"snake-access {snake_id}={level}"),
            f"snake-access {snake_id}={level}",
        )
    if command == "sources":
        action = str(args[0]).lower() if args else "list"
        registry = SourceRegistry()
        snapshots = SourceSnapshotStore()
        cache = refresh_service = None
        for descriptor in load_builtin_source_descriptors():
            source_id = str(descriptor.get("source_id") or "").strip()
            if source_id and registry.get_source(source_id) is None:
                registry.create_source(descriptor)
        refresh_service = SourceRefreshService(registry=registry, snapshots=snapshots)
        cache = refresh_service.cache
        if action == "list":
            items = registry.list_sources(include_disabled=True)
            parts: list[str] = []
            for item in items:
                source_id = str(item.get("source_id") or "")
                latest = snapshots.latest_indexed_snapshot(source_id=source_id) or {}
                status = str(latest.get("status") or "none")
                parts.append(f"{source_id}:{status}")
            msg = "sources: " + (" ".join(parts) if parts else "none")
            return CommandResult(state.with_updates(status_message=msg[:240]), msg)
        if action == "refresh":
            if len(args) < 2:
                return CommandResult(state, "sources refresh <source-id> [--dry-run]", handled=False)
            source_id = str(args[1]).strip()
            dry_run = any(str(x).lower() == "--dry-run" for x in args[2:])
            report = refresh_service.refresh_source(source_id=source_id, dry_run=dry_run)
            status = str(report.get("status") or "unknown")
            reason = str(report.get("reason_code") or "")
            human = str(report.get("human_message") or "")
            msg = f"sources refresh {source_id}: {status}"
            if reason:
                msg += f" reason={reason}"
            if human:
                msg += f" msg={human}"
            return CommandResult(state.with_updates(status_message=msg[:240]), json.dumps(report, ensure_ascii=False))
        if action == "snapshots":
            if len(args) < 2:
                return CommandResult(state, "sources snapshots <source-id>", handled=False)
            source_id = str(args[1]).strip()
            rows = snapshots.list_snapshots(source_id=source_id)
            if not rows:
                return CommandResult(state.with_updates(status_message=f"sources snapshots {source_id}: empty"), "[]")
            preview = " | ".join(
                f"{str(item.get('snapshot_id') or '')}:{str(item.get('status') or '')}" for item in rows[:5]
            )
            return CommandResult(
                state.with_updates(status_message=f"sources snapshots {source_id}: {preview}"[:240]),
                json.dumps(rows, ensure_ascii=False),
            )
        if action == "cite":
            if len(args) < 2:
                return CommandResult(state, "sources cite <source-id>", handled=False)
            source_id = str(args[1]).strip()
            source = registry.get_source(source_id)
            if source is None:
                return CommandResult(state, f"sources: unknown source_id {source_id}", handled=False)
            latest = snapshots.latest_indexed_snapshot(source_id=source_id)
            citation = format_citation(descriptor=source, snapshot=latest, output_format="long")
            rendered = str(citation.get("rendered") or citation.get("long") or "")
            return CommandResult(
                state.with_updates(status_message=f"sources cite {source_id}"[:240]),
                rendered,
            )
        if action == "cache":
            if len(args) < 2:
                return CommandResult(state, "sources cache <source-id> [clear]", handled=False)
            source_id = str(args[1]).strip()
            if registry.get_source(source_id) is None:
                return CommandResult(state, f"sources: unknown source_id {source_id}", handled=False)
            op = str(args[2]).lower() if len(args) > 2 else "status"
            if op == "clear":
                removed = int(cache.clear_source(source_id=source_id))
                stats = cache.stats_for_source(source_id=source_id)
                msg = (
                    f"sources cache {source_id} cleared removed={removed} "
                    f"raw={stats['raw_files']} extracted={stats['extracted_files']} bytes={stats['total_bytes']}"
                )
                return CommandResult(state.with_updates(status_message=msg[:240]), msg)
            stats = cache.stats_for_source(source_id=source_id)
            msg = (
                f"sources cache {source_id} raw={stats['raw_files']} extracted={stats['extracted_files']} "
                f"bytes={stats['total_bytes']}"
            )
            return CommandResult(state.with_updates(status_message=msg[:240]), msg)
        return CommandResult(state, "sources: list | refresh <id> | snapshots <id> | cite <id> | cache <id> [clear]", handled=False)
    if command == "diff3":
        game = dict(state.header_logo_game or {})
        diff3_state = _get_diff3_state(state)
        if not args:
            next_state = _state_with_diff3_payload(state, game=game, diff3_state=diff3_state)
            return CommandResult(next_state.with_updates(status_message="diff3 opened"), json.dumps(next_state.section_payloads["artifacts"], ensure_ascii=False))
        action = str(args[0]).lower()
        if action == "panel":
            if len(args) < 3:
                return CommandResult(state, "diff3 panel <A|B|C> current|output|ai|mode|filter ...", handled=False)
            panel_id = str(args[1]).upper()
            if panel_id not in {"A", "B", "C"}:
                return CommandResult(state, f"invalid panel id: {panel_id}", handled=False)
            sub = str(args[2]).lower()
            if sub == "current":
                mode = "unified"
                for idx, token in enumerate(args):
                    if str(token).lower() == "--mode" and idx + 1 < len(args):
                        mode = str(args[idx + 1]).strip().lower()
                diff3_state = set_panel_state(
                    diff3_state,
                    panel_id=panel_id,
                    panel_type="diff",
                    source_left=build_current_diff_source_ref(),
                    source_right=None,
                    render_mode=mode,
                )
            elif sub == "output":
                if len(args) < 4:
                    return CommandResult(state, "diff3 panel <A|B|C> output <output-artifact-id>", handled=False)
                output_id = str(args[3]).strip()
                if not output_id:
                    return CommandResult(state, "diff3 panel <A|B|C> output <output-artifact-id>", handled=False)
                goal_id = str(game.get("active_goal_id") or "").strip() or None
                diff3_state = set_panel_state(
                    diff3_state,
                    panel_id=panel_id,
                    panel_type="diff",
                    source_left=build_output_artifact_source_ref(output_artifact_id=output_id, goal_id=goal_id),
                    source_right=None,
                    render_mode="unified",
                )
            elif sub == "ai":
                mode = str(args[3]).lower() if len(args) > 3 else "review"
                panel_type_map = {
                    "review": "ai_review",
                    "chat": "ai_review",
                    "explain": "ai_explain",
                    "risk": "ai_review",
                    "tests": "ai_review",
                    "patch": "ai_patch",
                }
                panel_type = panel_type_map.get(mode)
                if panel_type is None:
                    return CommandResult(state, f"invalid ai mode: {mode}", handled=False)
                render_mode = "ai_chat" if mode == "chat" else "ai_review"
                diff3_state = set_panel_state(
                    diff3_state,
                    panel_id=panel_id,
                    panel_type=panel_type,
                    source_left=None,
                    source_right=None,
                    render_mode=render_mode,
                )
                ai_state = build_ai_diff_panel_state(mode=mode, selected_panels=["A", "B"], status="idle")
                extensions = dict(diff3_state.get("extensions") or {})
                extensions["ai_panel_state"] = ai_state
                diff3_state["extensions"] = extensions
            elif sub == "mode":
                if len(args) < 4:
                    return CommandResult(state, "diff3 panel <A|B|C> mode <render-mode>", handled=False)
                mode = str(args[3]).lower()
                panel = next((item for item in list(diff3_state.get("panels") or []) if str(item.get("panel_id") or "") == panel_id), None)
                if panel is None:
                    return CommandResult(state, f"panel not found: {panel_id}", handled=False)
                diff3_state = set_panel_state(
                    diff3_state,
                    panel_id=panel_id,
                    panel_type=str(panel.get("panel_type") or "diff"),
                    source_left=panel.get("source_left"),
                    source_right=panel.get("source_right"),
                    render_mode=mode,
                )
            elif sub == "filter":
                panel = next((item for item in list(diff3_state.get("panels") or []) if str(item.get("panel_id") or "") == panel_id), None)
                if panel is None:
                    return CommandResult(state, f"panel not found: {panel_id}", handled=False)
                filters = dict(panel.get("filters") or {})
                for token in args[3:]:
                    value = str(token).strip()
                    if "=" not in value:
                        continue
                    key, val = value.split("=", 1)
                    if key.strip() in {"path_filter", "status_filter", "hunk_filter", "search_text"}:
                        filters[key.strip()] = val.strip()
                panel["filters"] = filters
                diff3_state["updated_at"] = _now_iso()
            else:
                return CommandResult(state, "diff3 panel <A|B|C> current|output|ai|mode|filter ...", handled=False)
            next_state = _state_with_diff3_payload(state, game=game, diff3_state=diff3_state)
            return CommandResult(next_state.with_updates(status_message=f"diff3 panel {panel_id} updated"), json.dumps(next_state.section_payloads["artifacts"], ensure_ascii=False))
        if action == "focus":
            panel_id = str(args[1]).upper() if len(args) > 1 else ""
            if panel_id not in {"A", "B", "C"}:
                return CommandResult(state, "diff3 focus <A|B|C>", handled=False)
            diff3_state["active_panel"] = panel_id
            next_state = _state_with_diff3_payload(state, game=game, diff3_state=diff3_state)
            return CommandResult(next_state.with_updates(status_message=f"diff3 focus {panel_id}"), f"diff3 focus {panel_id}")
        if action == "sync":
            flag = str(args[1]).lower() if len(args) > 1 else ""
            if flag not in {"on", "off"}:
                return CommandResult(state, "diff3 sync on|off", handled=False)
            extensions = dict(diff3_state.get("extensions") or {})
            extensions["sync_scroll"] = flag == "on"
            diff3_state["extensions"] = extensions
            next_state = _state_with_diff3_payload(state, game=game, diff3_state=diff3_state)
            return CommandResult(next_state.with_updates(status_message=f"diff3 sync {flag}"), f"diff3 sync {flag}")
        if action == "scroll":
            direction = str(args[1]).lower() if len(args) > 1 else ""
            if direction not in {"up", "down", "pageup", "pagedown"}:
                return CommandResult(state, "diff3 scroll up|down|pageup|pagedown", handled=False)
            active = str(diff3_state.get("active_panel") or "A")
            panel = next((item for item in list(diff3_state.get("panels") or []) if str(item.get("panel_id") or "") == active), None)
            if panel is None:
                return CommandResult(state, f"panel not found: {active}", handled=False)
            step = -1 if direction == "up" else 1
            if direction == "pageup":
                step = -20
            if direction == "pagedown":
                step = 20
            scroll = dict(panel.get("scroll_state") or {})
            line = max(0, int(scroll.get("line") or 0) + step)
            scroll["line"] = line
            panel["scroll_state"] = scroll
            diff3_state["updated_at"] = _now_iso()
            next_state = _state_with_diff3_payload(state, game=game, diff3_state=diff3_state)
            return CommandResult(next_state.with_updates(status_message=f"diff3 scroll {direction}"), f"diff3 scroll {direction}")
        if action == "ai":
            mode = str(args[1]).lower() if len(args) > 1 else ""
            if mode == "run":
                extensions = dict(diff3_state.get("extensions") or {})
                current_ai = dict(extensions.get("ai_panel_state") or {})
                current_mode = str(current_ai.get("mode") or "review")
                run_mode = str(args[2]).lower() if len(args) > 2 else current_mode
                if run_mode not in {"review", "explain", "risk", "tests", "patch", "chat"}:
                    return CommandResult(state, "diff3 ai run [review|explain|risk|tests|patch|chat]", handled=False)
                running = (
                    set_ai_diff_mode(current_ai, mode=run_mode, status="running")
                    if current_ai
                    else build_ai_diff_panel_state(mode=run_mode, selected_panels=["A", "B"], status="running")
                )
                extensions["ai_panel_state"] = running
                diff3_state["extensions"] = extensions
                try:
                    result = dispatch_ai_diff_request(
                        goal_id=str(game.get("active_goal_id") or "").strip() or None,
                        diff3_state=diff3_state,
                        mode=run_mode,
                    )
                except TimeoutError:
                    result = {
                        "status": "degraded",
                        "reason_code": "ai_diff_timeout",
                        "response": {
                            "schema": "ai_diff_response.v1",
                            "status": "degraded",
                            "artifact_type": run_mode,
                            "summary": "AI diff request timed out",
                            "findings": [],
                            "risks": [],
                            "suggested_tests": [],
                            "patch_suggestions": [],
                            "source_refs": [],
                            "reason_code": "ai_diff_timeout",
                        },
                        "context_envelope": {},
                        "provenance_id": "",
                        "output_artifact_id": "",
                    }
                except Exception:
                    result = {
                        "status": "degraded",
                        "reason_code": "ai_diff_dispatch_failed",
                        "response": {
                            "schema": "ai_diff_response.v1",
                            "status": "degraded",
                            "artifact_type": run_mode,
                            "summary": "AI diff dispatch failed",
                            "findings": [],
                            "risks": [],
                            "suggested_tests": [],
                            "patch_suggestions": [],
                            "source_refs": [],
                            "reason_code": "ai_diff_dispatch_failed",
                        },
                        "context_envelope": {},
                        "provenance_id": "",
                        "output_artifact_id": "",
                    }
                completed = set_ai_diff_mode(
                    running,
                    mode=run_mode,
                    status="degraded" if str(result.get("status") or "") != "success" else "completed",
                )
                completed["last_response_ref"] = str(result.get("output_artifact_id") or result.get("provenance_id") or "")
                completed["context_refs"] = [f"ctx:{hashlib.sha1(json.dumps(result.get('context_envelope') or {}, sort_keys=True).encode('utf-8')).hexdigest()[:12]}"]
                completed["selected_hunks"] = list((result.get("context_envelope") or {}).get("selected_hunk_refs") or [])
                extensions["ai_panel_state"] = completed
                extensions["ai_last_response"] = dict(result.get("response") or {})
                extensions["ai_last_context"] = dict(result.get("context_envelope") or {})
                extensions["ai_last_findings"] = list(dict(result.get("response") or {}).get("findings") or [])
                diff3_state["extensions"] = extensions
                next_state = _state_with_diff3_payload(state, game=game, diff3_state=diff3_state)
                status_label = "degraded" if str(result.get("status") or "") != "success" else "completed"
                return CommandResult(
                    next_state.with_updates(status_message=f"diff3 ai run {run_mode} {status_label}"),
                    json.dumps(result, ensure_ascii=False),
                    handled=True,
                )
            if mode not in {"review", "explain", "risk", "tests", "patch", "chat"}:
                return CommandResult(state, "diff3 ai review|explain|risk|tests|patch|chat|run [mode]", handled=False)
            extensions = dict(diff3_state.get("extensions") or {})
            current_ai = extensions.get("ai_panel_state")
            if isinstance(current_ai, dict):
                extensions["ai_panel_state"] = set_ai_diff_mode(current_ai, mode=mode, status="idle")
            else:
                extensions["ai_panel_state"] = build_ai_diff_panel_state(mode=mode, selected_panels=["A", "B"], status="idle")
            diff3_state["extensions"] = extensions
            next_state = _state_with_diff3_payload(state, game=game, diff3_state=diff3_state)
            return CommandResult(next_state.with_updates(status_message=f"diff3 ai {mode}"), f"diff3 ai {mode}")
        return CommandResult(state, "diff3: panel ... | focus <A|B|C> | scroll ... | sync on|off | ai ...", handled=False)
    if command == "goal":
        if not args:
            return CommandResult(state, "goal: use <goal-id> | artifacts [filter ...|clear-filter] | sources candidates", handled=False)
        action = str(args[0]).lower()
        game = dict(state.header_logo_game or {})
        service = GoalArtifactService()
        if action == "use":
            if len(args) < 2:
                return CommandResult(state, "goal use <goal-id>", handled=False)
            goal_id = str(args[1]).strip()
            if not goal_id:
                return CommandResult(state, "goal use <goal-id>", handled=False)
            game["active_goal_id"] = goal_id
            payload = _load_goal_artifact_payload(state=state.with_updates(header_logo_game=game), goal_id=goal_id)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = payload
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message=f"active goal {goal_id}",
                ),
                f"goal {goal_id} active",
            )
        goal_id, error = _require_active_goal(state)
        if error is not None or goal_id is None:
            return error or CommandResult(state, "active goal required", handled=False)
        if action == "artifacts":
            filters = dict(game.get("goal_artifact_filters") or {})
            if len(args) >= 2 and str(args[1]).lower() == "clear-filter":
                filters = {}
                game["goal_artifact_filters"] = {}
            elif len(args) >= 2 and str(args[1]).lower() == "filter":
                for token in args[2:]:
                    text = str(token).strip()
                    if "=" not in text:
                        continue
                    key, value = text.split("=", 1)
                    if key.strip() in {"source_id", "artifact_type", "sensitivity", "status", "worker_id", "task_id", "prompt_template_ref", "model_ref"}:
                        if value.strip():
                            filters[key.strip()] = value.strip()
                game["goal_artifact_filters"] = normalize_goal_artifact_filters(filters)
            payload = _load_goal_artifact_payload(state=state.with_updates(header_logo_game=game), goal_id=goal_id)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = payload
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            active_filters = payload.get("filters") or {}
            filter_label = ", ".join(f"{k}={v}" for k, v in active_filters.items()) if active_filters else "none"
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message=f"goal artifacts {goal_id} filters={filter_label}",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        if action == "sources":
            if len(args) < 2:
                return CommandResult(state, "goal sources candidates", handled=False)
            sub = str(args[1]).lower()
            if sub != "candidates":
                return CommandResult(state, "goal sources candidates", handled=False)
            rows = ArtifactCandidateService(goal_artifact_service=service).list_candidates(goal_id=goal_id)
            return CommandResult(
                state.with_updates(status_message=f"goal sources candidates {goal_id}: {len(rows)}"),
                json.dumps({"goal_id": goal_id, "candidates": rows}, ensure_ascii=False),
            )
        if action == "source":
            if len(args) < 2:
                return CommandResult(state, "goal source grant|revoke|detail ...", handled=False)
            sub = str(args[1]).lower()
            if sub == "grant":
                if len(args) < 3:
                    return CommandResult(state, "goal source grant <artifact-ref> --usage use_as_context", handled=False)
                artifact_ref = str(args[2]).strip()
                usage = "use_as_context"
                for idx, token in enumerate(args[3:], start=3):
                    if str(token).lower() == "--usage" and idx + 1 < len(args):
                        usage = str(args[idx + 1]).strip()
                policy = ArtifactAccessPolicy().evaluate(
                    goal_id=goal_id,
                    artifact_sensitivity="internal",
                    requested_usage=usage,
                    worker_kind="general",
                    provider_location="local",
                    data_boundary="project_private",
                )
                if policy.decision != "allow":
                    return CommandResult(state, f"grant denied reason={policy.reason_code}", handled=False)
                grant_id = f"grant-{hashlib.sha1(f'{goal_id}:{artifact_ref}:{usage}'.encode('utf-8')).hexdigest()[:10]}"
                grant_payload = {
                    "schema": "source_artifact_grant.v1",
                    "grant_id": grant_id,
                    "goal_id": goal_id,
                    "artifact_ref": artifact_ref,
                    "granted_by": "operator_tui",
                    "granted_at": _now_iso(),
                    "allowed_usages": sorted(set(["read", usage])),
                    "data_boundary": "project_private",
                    "sensitivity": "internal",
                    "policy_decision_ref": policy.policy_decision_ref,
                }
                try:
                    created = service.create_grant(goal_id=goal_id, grant=grant_payload)
                except GoalArtifactServiceError as exc:
                    return CommandResult(state, f"grant failed reason={exc.reason_code}", handled=False)
                return CommandResult(
                    state.with_updates(status_message=f"goal source granted {grant_id}"),
                    json.dumps(created, ensure_ascii=False),
                )
            if sub == "revoke":
                if len(args) < 3:
                    return CommandResult(state, "goal source revoke <grant-id>", handled=False)
                grant_id = str(args[2]).strip()
                try:
                    revoked = service.revoke_grant(goal_id=goal_id, grant_id=grant_id, revoke_reason="operator_tui_revoke")
                except GoalArtifactServiceError as exc:
                    return CommandResult(state, f"revoke failed reason={exc.reason_code}", handled=False)
                return CommandResult(
                    state.with_updates(status_message=f"goal source revoked {grant_id}"),
                    json.dumps(revoked, ensure_ascii=False),
                )
            if sub == "detail":
                if len(args) < 3:
                    return CommandResult(state, "goal source detail <grant-id>", handled=False)
                grant_id = str(args[2]).strip()
                graph = service.get_goal_graph(goal_id)
                for grant in list(graph.get("source_grants") or []):
                    if str(grant.get("grant_id") or "") != grant_id:
                        continue
                    detail = {
                        "grant_id": grant_id,
                        "artifact_ref": grant.get("artifact_ref"),
                        "data_boundary": grant.get("data_boundary"),
                        "sensitivity": grant.get("sensitivity"),
                        "allowed_usages": grant.get("allowed_usages"),
                        "policy_decision_ref": grant.get("policy_decision_ref"),
                        "expires_at": grant.get("expires_at"),
                        "revoked_at": grant.get("revoked_at"),
                    }
                    return CommandResult(state.with_updates(status_message=f"goal source detail {grant_id}"), json.dumps(detail, ensure_ascii=False))
                return CommandResult(state, f"grant not found: {grant_id}", handled=False)
            return CommandResult(state, "goal source grant|revoke|detail ...", handled=False)
        return CommandResult(state, "goal: use <goal-id> | artifacts [filter ...|clear-filter] | sources candidates", handled=False)
    if command == "artifact":
        if len(args) < 2:
            return CommandResult(state, "artifact provenance|prompt|config <output-artifact-id>", handled=False)
        action = str(args[0]).lower()
        if action not in {"provenance", "prompt", "config"}:
            return CommandResult(state, "artifact provenance|prompt|config <output-artifact-id>", handled=False)
        goal_id, error = _require_active_goal(state)
        if error is not None or goal_id is None:
            return error or CommandResult(state, "active goal required", handled=False)
        output_id = str(args[1]).strip()
        service = GoalArtifactService()
        graph = service.get_goal_graph(goal_id)
        outputs = list(graph.get("output_artifacts") or [])
        output = next((row for row in outputs if str(row.get("output_artifact_id") or "") == output_id), None)
        if output is None:
            return CommandResult(state, f"output artifact not found: {output_id}", handled=False)
        provenance_id = str(output.get("provenance_id") or "")
        provenance = service.get_execution_provenance(goal_id=goal_id, provenance_id=provenance_id) if provenance_id else None
        if action == "prompt":
            prompt_refs = dict((provenance or {}).get("prompt_refs") or {})
            detail = {
                "output_artifact_id": output_id,
                "provenance_id": provenance_id or None,
                "prompt_template_ref": prompt_refs.get("prompt_template_ref"),
                "prompt_template_version": prompt_refs.get("prompt_template_version"),
                "prompt_template_hash": prompt_refs.get("prompt_template_hash"),
                "variables_hash": prompt_refs.get("prompt_variables_hash"),
                "final_prompt_hash": prompt_refs.get("final_prompt_hash"),
                "raw_prompt_status": "raw prompt not stored"
                if not bool(prompt_refs.get("raw_prompt_stored"))
                else "raw prompt stored",
                "reason_code": prompt_refs.get("reason_code") if str(prompt_refs.get("reason_code") or "").strip() else "",
            }
            return CommandResult(
                state.with_updates(status_message=f"artifact prompt {output_id}"),
                json.dumps(detail, ensure_ascii=False),
            )
        if action == "config":
            config_refs = dict((provenance or {}).get("config_refs") or {})
            detail = {
                "output_artifact_id": output_id,
                "provenance_id": provenance_id or None,
                "worker_config_ref": config_refs.get("worker_config_ref"),
                "runtime_config_ref": config_refs.get("runtime_config_ref"),
                "model_config_ref": config_refs.get("model_config_ref"),
                "policy_config_ref": config_refs.get("policy_config_ref"),
            }
            return CommandResult(
                state.with_updates(status_message=f"artifact config {output_id}"),
                json.dumps(detail, ensure_ascii=False),
            )
        usages = list(graph.get("source_usages") or [])
        grants_by_id = {str(row.get("grant_id") or ""): row for row in list(graph.get("source_grants") or [])}
        usage_rows = [row for row in usages if str(row.get("usage_id") or "") in set(list(output.get("input_usage_refs") or []))]
        sources: list[dict[str, object]] = []
        for row in usage_rows:
            grant = grants_by_id.get(str(row.get("grant_id") or ""), {})
            revoked_after_use = bool(grant and grant.get("revoked_at"))
            sources.append(
                {
                    "usage_id": row.get("usage_id"),
                    "artifact_ref": row.get("artifact_ref"),
                    "grant_id": row.get("grant_id"),
                    "revoked_after_use": revoked_after_use,
                    "source_reference": row.get("source_reference"),
                }
            )
        detail = {
            "output_artifact_id": output.get("output_artifact_id"),
            "goal_id": output.get("goal_id"),
            "task_id": output.get("task_id"),
            "worker_id": output.get("worker_id"),
            "worker_kind": (provenance or {}).get("worker_kind"),
            "runtime_target_ref": (provenance or {}).get("runtime_target_ref"),
            "model_ref": (provenance or {}).get("model_ref"),
            "config_refs": (provenance or {}).get("config_refs"),
            "prompt_refs": (provenance or {}).get("prompt_refs"),
            "provenance_id": provenance_id or None,
            "execution_id": output.get("execution_id"),
            "content_hash": output.get("content_hash"),
            "input_usage_refs": output.get("input_usage_refs") or [],
            "output_artifact_refs": list((provenance or {}).get("output_artifact_refs") or []),
            "sources": sources,
            "note": "no input artifacts recorded" if not usage_rows else "",
        }
        return CommandResult(
            state.with_updates(status_message=f"artifact provenance {output_id}"),
            json.dumps(detail, ensure_ascii=False),
        )
    if command == "ai":
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
    if command == "inspect":
        return CommandResult(state.with_updates(mode=OperatorMode.INSPECT, status_message="inspect current selection"), "inspect current selection")
    if command == "browser":
        target = args[0] if args else ""
        url = browser_fallback_url(state.endpoint, state.section_id, target)
        return CommandResult(state.with_updates(browser_fallback_url=url, status_message=f"browser fallback {url}"), f"browser fallback {url}")
    if command == "action":
        if not args:
            return CommandResult(state, "action command requires an action name", handled=False)
        risk = args[1] if len(args) > 1 else "read_only"
        action = parse_action(args[0], risk=risk)
        result = dispatch_action(action)
        pending = (
            {
                "name": result.pending_action.name,
                "target": result.pending_action.target,
                "risk": result.pending_action.risk.value,
                "payload": dict(result.pending_action.payload),
                "requires_confirmation": result.pending_action.requires_confirmation,
            }
            if result.pending_action
            else None
        )
        return CommandResult(
            state.with_updates(
                pending_action=pending,
                audit_context=result.audit_context,
                status_message=result.message,
            ),
            result.message,
            handled=result.accepted or result.pending_action is not None,
        )
    if command == "confirm":
        pending = state.pending_action or {}
        if not pending:
            return CommandResult(state, "no pending action to confirm", handled=False)
        action = parse_action(str(pending.get("name") or ""), str(pending.get("target") or ""), str(pending.get("risk") or "high"))
        result = dispatch_action(action, confirmed=True)
        return CommandResult(
            state.with_updates(pending_action=None, audit_context=result.audit_context, status_message=result.message),
            result.message,
            handled=result.accepted,
        )
    if command in {"cancel", "esc"}:
        return CommandResult(
            state.with_updates(mode=OperatorMode.NORMAL, pending_action=None, command_line="", status_message="cancelled"),
            "cancelled",
        )
    if command == "sections":
        return CommandResult(state.with_updates(status_message="sections: " + ",".join(section_ids())), "sections listed")

    # ── speed ─────────────────────────────────────────────────────────────────
    if command == "speed":
        if not args:
            return CommandResult(state, "speed requires a level 1-5", handled=False)
        try:
            level = int(args[0])
        except ValueError:
            return CommandResult(state.with_updates(status_message="speed: ungültiger Wert (1-5)"), "speed: invalid", handled=False)
        if level < 1 or level > 5:
            return CommandResult(state.with_updates(status_message="speed: Wert muss 1-5 sein"), "speed: out of range", handled=False)
        # Map level 1-5 to TPS: 3, 6, 12, 24, 60
        tps_map = {1: 3, 2: 6, 3: 12, 4: 24, 5: 60}
        tps = tps_map[level]
        game = dict(state.header_logo_game or {})
        game["tps_override"] = tps
        game["speed_level"] = level
        return CommandResult(
            state.with_updates(header_logo_game=game, status_message=f"speed: {level}/5 ({tps} tps)"),
            f"speed {level}/5",
        )

    # ── tutor ─────────────────────────────────────────────────────────────────
    if command == "tutor":
        sub = args[0].lower() if args else ""
        if sub == "mode":
            mode_arg = args[1].lower() if len(args) > 1 else ""
            if mode_arg not in {"overview", "deep", "expert"}:
                return CommandResult(state, "tutor mode erwartet: overview | deep | expert", handled=False)
            try:
                from client_surfaces.operator_tui.snake_persistence import set_tutor_mode
                set_tutor_mode(mode_arg)
            except Exception:
                pass
            game = dict(state.header_logo_game or {})
            game["tutor_depth_mode"] = mode_arg
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message=f"tutor mode: {mode_arg}"),
                f"tutor mode {mode_arg}",
            )
        if sub == "silent":
            try:
                from client_surfaces.operator_tui.snake_persistence import set_tutor_silent
                set_tutor_silent(True)
            except Exception:
                pass
            game = dict(state.header_logo_game or {})
            game["tutor_silent"] = True
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message="tutor: idle-Kommentare deaktiviert"),
                "tutor silent",
            )
        if sub == "active":
            try:
                from client_surfaces.operator_tui.snake_persistence import set_tutor_silent
                set_tutor_silent(False)
            except Exception:
                pass
            game = dict(state.header_logo_game or {})
            game["tutor_silent"] = False
            return CommandResult(
                state.with_updates(header_logo_game=game, status_message="tutor: idle-Kommentare aktiv"),
                "tutor active",
            )
        if sub == "replay":
            section_arg = args[1].lower() if len(args) > 1 else ""
            try:
                from client_surfaces.operator_tui.snake_persistence import load_tutor_config, save_tutor_config
                cfg = load_tutor_config()
                visited = list(cfg.get("visited_sections") or [])
                if section_arg in visited:
                    visited.remove(section_arg)
                    cfg["visited_sections"] = visited
                    save_tutor_config(cfg)
            except Exception:
                pass
            return CommandResult(
                state.with_updates(status_message=f"tutor replay: {section_arg or '(alle)'} zurückgesetzt"),
                f"tutor replay {section_arg}",
            )
        return CommandResult(state, "tutor: mode <overview|deep|expert> | silent | active | replay <section>", handled=False)

    # ── ask ───────────────────────────────────────────────────────────────────
    if command == "ask":
        question = " ".join(args).strip()
        if not question:
            return CommandResult(state.with_updates(status_message="ask: Bitte Frage angeben"), "ask: leer", handled=False)
        game = dict(state.header_logo_game or {})
        game["tutor_ask_question"] = question
        game["tutor_ask_at"] = __import__("time").monotonic()
        game["tutor_ask_answered"] = False
        # pause game while AI answers
        game["paused"] = True
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message=f"ask: {question[:40]}...",
            ),
            f"ask: {question[:40]}",
        )

    # ── tutorial ──────────────────────────────────────────────────────────────
    if command == "tutorial":
        sub = args[0].lower() if args else ""
        if sub == "start":
            name = args[1] if len(args) > 1 else "intro"
            try:
                from client_surfaces.operator_tui.snake_tutorial import make_tutorial_state
                from client_surfaces.operator_tui.snake_persistence import get_tutorial_progress
                start_step = max(0, get_tutorial_progress(name))
                ts = make_tutorial_state(name, start_step=start_step)
            except Exception:
                ts = None
            if ts is None:
                return CommandResult(state.with_updates(status_message=f"tutorial: '{name}' nicht gefunden"), f"tutorial not found: {name}", handled=False)
            game = dict(state.header_logo_game or {})
            game["tutorial_state"] = ts
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"tutorial: {ts['title']} gestartet"),
                f"tutorial start {name}",
            )
        if sub == "stop":
            game = dict(state.header_logo_game or {})
            game["tutorial_state"] = None
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="tutorial: gestoppt"),
                "tutorial stop",
            )
        if sub == "skip":
            game = dict(state.header_logo_game or {})
            ts = dict(game.get("tutorial_state") or {})
            if not ts:
                return CommandResult(state.with_updates(status_message="tutorial: kein aktives Tutorial"), "tutorial: none active", handled=False)
            try:
                from client_surfaces.operator_tui.snake_tutorial import advance_step, get_current_step
                step = get_current_step(ts)
                ts = advance_step(ts, skipped=True)
                game["tutorial_state"] = ts
            except Exception:
                pass
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="tutorial: Step übersprungen"),
                "tutorial skip",
            )
        if sub == "reset":
            game = dict(state.header_logo_game or {})
            ts_raw = game.get("tutorial_state")
            name = str((ts_raw or {}).get("name") or "intro") if isinstance(ts_raw, dict) else "intro"
            try:
                from client_surfaces.operator_tui.snake_tutorial import make_tutorial_state
                from client_surfaces.operator_tui.snake_persistence import reset_tutorial_progress
                reset_tutorial_progress(name)
                ts = make_tutorial_state(name, start_step=0)
                game["tutorial_state"] = ts
            except Exception:
                game["tutorial_state"] = None
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"tutorial: {name} zurückgesetzt"),
                f"tutorial reset {name}",
            )
        if sub == "guided":
            game = dict(state.header_logo_game or {})
            ts_raw = game.get("tutorial_state")
            if isinstance(ts_raw, dict) and ts_raw.get("active"):
                ts = dict(ts_raw)
                ts["guided"] = True
                game["tutorial_state"] = ts
                return CommandResult(
                    state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="tutorial: Guided Mode aktiviert"),
                    "tutorial guided",
                )
            return CommandResult(state.with_updates(status_message="tutorial: erst :tutorial start <name>"), "tutorial: none active", handled=False)
        return CommandResult(state, "tutorial: start <name> | stop | skip | reset | guided", handled=False)

    # ── tutorials ─────────────────────────────────────────────────────────────
    if command == "tutorials":
        try:
            from client_surfaces.operator_tui.snake_tutorial import list_tutorials
            items = list_tutorials()
            names = ", ".join(f"{t['name']} ({t['step_count']} Steps)" for t in items) if items else "(keine)"
        except Exception:
            names = "(Ladefehler)"
        return CommandResult(state.with_updates(status_message=f"tutorials: {names}"), "tutorials listed")

    # ── snakes ────────────────────────────────────────────────────────────────
    if command == "snakes":
        game = state.header_logo_game or {}
        snakes_raw = game.get("snakes")
        snakes = {str(k): dict(v) for k, v in snakes_raw.items() if isinstance(v, dict)} if isinstance(snakes_raw, dict) else {}
        if not snakes:
            return CommandResult(state.with_updates(status_message="snakes: keine aktiven Schlangen"), "snakes: empty")
        parts = []
        for sid, snap in sorted(snakes.items()):
            pseudo = str(snap.get("pseudonym") or sid)
            color = str(snap.get("snake_color") or "mint")
            role = str(snap.get("role") or ("player" if snap.get("local") else "tutor"))
            parts.append(f"{sid}={pseudo}[{color}/{role}]")
        return CommandResult(state.with_updates(status_message="snakes: " + " ".join(parts)), "snakes listed")

    # ── msg ───────────────────────────────────────────────────────────────────
    if command == "msg":
        if len(args) < 2:
            return CommandResult(state, "msg erwartet: <snake-id> <text>", handled=False)
        target_id = args[0].strip()
        text = " ".join(args[1:]).strip()
        if not text:
            return CommandResult(state.with_updates(status_message="msg: leere Nachricht ignoriert"), "msg: empty", handled=False)
        if len(text) > 200:
            return CommandResult(state.with_updates(status_message="msg: max. 200 Zeichen"), "msg: too long", handled=False)
        game = dict(state.header_logo_game or {})
        outbox: list[dict] = list(game.get("snake_outbox") or [])
        outbox.append({
            "to": target_id,
            "from": str(game.get("local_snake_id") or "s1"),
            "text": text,
            "at": __import__("time").monotonic(),
        })
        game["snake_outbox"] = outbox[-20:]  # keep last 20
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"msg → {target_id}: {text[:40]}"),
            f"msg sent to {target_id}",
        )

    # ── chat ──────────────────────────────────────────────────────────────────
    if command == "chat":
        sub = args[0].lower() if args else ""
        if not sub:
            return CommandResult(state, "chat: room | ai | @<snake-id> | retry", handled=False)
        game = dict(state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel, add_direct_channel
        chat = get_chat_state(game)

        if sub == "retry":
            # retry failed outbox messages
            game["chat_retry_requested"] = True
            set_chat_state(game, chat)
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="chat: retry fehlgeschlagene Nachrichten"),
                "chat retry",
            )
        if sub == "room":
            switch_channel(chat, "room:main")
            set_chat_state(game, chat)
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="chat: #room"),
                "chat room",
            )
        if sub == "ai":
            switch_channel(chat, "ai:tutor")
            set_chat_state(game, chat)
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="chat: AI tutor-ai"),
                "chat ai",
            )
        if sub.startswith("@"):
            snake_id = sub[1:].strip()
            if not snake_id:
                return CommandResult(state, "chat @: snake-id erforderlich", handled=False)
            snakes_raw = game.get("snakes") or {}
            snap = snakes_raw.get(snake_id) if isinstance(snakes_raw, dict) else None
            if snap is None:
                return CommandResult(state.with_updates(status_message=f"chat: Snake '{snake_id}' nicht gefunden"), f"chat: unknown snake {snake_id}", handled=False)
            display = str(snap.get("pseudonym") or snake_id) if isinstance(snap, dict) else snake_id
            ch_id = add_direct_channel(chat, snake_id, display)
            switch_channel(chat, ch_id)
            set_chat_state(game, chat)
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"chat: @{display}"),
                f"chat direct {snake_id}",
            )
        return CommandResult(state, f"chat: unbekannte Option '{sub}'", handled=False)

    # ── notes ─────────────────────────────────────────────────────────────────
    if command == "notes":
        sub = args[0].lower() if args else ""
        game = dict(state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel
        chat = get_chat_state(game)

        if not sub or sub == "open":
            switch_channel(chat, "notes:self")
            set_chat_state(game, chat)
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message="notes: NOTES local-only"),
                "notes open",
            )
        if sub == "find":
            query = " ".join(args[1:]).strip()
            game["notes_search_query"] = query
            switch_channel(chat, "notes:self")
            set_chat_state(game, chat)
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"notes: suche '{query}'"),
                f"notes find {query}",
            )
        if sub == "pin" and len(args) > 1:
            note_id = args[1].strip()
            game["notes_pin_id"] = note_id
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"notes: pin {note_id[:12]}"),
                f"notes pin {note_id}",
            )
        if sub == "unpin" and len(args) > 1:
            note_id = args[1].strip()
            game["notes_unpin_id"] = note_id
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"notes: unpin {note_id[:12]}"),
                f"notes unpin {note_id}",
            )
        if sub == "delete" and len(args) > 1:
            note_id = args[1].strip()
            game["notes_delete_id"] = note_id
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"notes: delete {note_id[:12]}"),
                f"notes delete {note_id}",
            )
        return CommandResult(state, "notes: open | find <text> | pin <id> | unpin <id> | delete <id>", handled=False)

    # ── channels ──────────────────────────────────────────────────────────────
    if command == "channels":
        game = state.header_logo_game or {}
        from client_surfaces.operator_tui.chat_state import get_chat_state
        chat = get_chat_state(game)
        channels = chat.get("channels") or {}
        parts = []
        for ch_id, ch in sorted(channels.items()):
            unread = int(ch.get("unread") or 0)
            display = str(ch.get("display_name") or ch_id)
            marker = "*" if unread else " "
            parts.append(f"{marker}{display}({'!' + str(unread) if unread else 'ok'})")
        msg = "channels: " + "  ".join(parts) if parts else "channels: keine"
        return CommandResult(state.with_updates(status_message=msg), "channels listed")

    # ── ai context ────────────────────────────────────────────────────────────
    if command == "ai" and args and args[0].lower() == "context":
        sub = args[1].lower() if len(args) > 1 else ""
        opt = args[2].lower() if len(args) > 2 else ""
        game = dict(state.header_logo_game or {})
        from client_surfaces.operator_tui.ai_snake_context import get_ai_context, set_ai_context, release_notes_context
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, make_message, append_message
        ctx = get_ai_context(game)
        chat = get_chat_state(game)

        if sub == "notes":
            released = opt == "on"
            release_notes_context(ctx, released=released)
            set_ai_context(game, ctx)
            # update chat state notes_context_released flag
            chat["notes_context_released"] = released
            # log to AI channel
            sys_text = f"* [system] Notes-Kontext {'freigegeben' if released else 'gesperrt'}"
            sys_msg = make_message(
                channel_id="ai:tutor", channel_type="ai",
                sender_id="system", sender_kind="system",
                text=sys_text, visibility="ai_context",
                delivery_state="received",
            )
            append_message(chat, sys_msg)
            set_chat_state(game, chat)
            label = "on" if released else "off"
            return CommandResult(
                state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=f"ai context notes {label}"),
                f"ai context notes {label}",
            )
        return CommandResult(state, "ai context notes on|off", handled=False)

    return CommandResult(state.with_updates(status_message=f"unknown command: {command}"), f"unknown command: {command}", handled=False)
