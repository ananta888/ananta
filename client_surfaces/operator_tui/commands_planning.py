"""Planning and diff3 command handlers for the Ananta operator TUI.

Extracted from client_surfaces/operator_tui/commands.py (SPLIT-002).
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
from agent.repository import goal_repo, task_repo
from agent.services.planning_track_pipeline_service import persist_planning_track_result
from agent.services.planning_track_planner_service import build_planner_context_envelope, render_track_planning_prompt
from agent.services.planning_track_task_integration_service import PlanningTrackTaskIntegrationService
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
from client_surfaces.operator_tui.models import CommandResult, OperatorMode, OperatorState, PanelState


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


def _build_mock_planning_track_payload(goal_id: str) -> dict[str, object]:
    tasks = [
        {
            "id": "T01",
            "title": "Analyse Ziel und Grenzen",
            "status": "todo",
            "priority": "P1",
            "risk": "medium",
            "type": "analysis",
            "acceptance_criteria": ["Anforderungen sind präzise und testbar erfasst."],
        },
        {
            "id": "T02",
            "title": "Implementiere Kernänderung",
            "status": "todo",
            "priority": "P1",
            "risk": "medium",
            "type": "coding",
            "acceptance_criteria": ["Kernfunktion ist implementiert und liefert erwartetes Ergebnis."],
        },
        {
            "id": "T03",
            "title": "Führe Verifikation aus",
            "status": "todo",
            "priority": "P1",
            "risk": "low",
            "type": "test",
            "acceptance_criteria": ["Tests/Checks laufen erfolgreich."],
        },
        {
            "id": "T04",
            "title": "Review und Übergabe",
            "status": "todo",
            "priority": "P2",
            "risk": "low",
            "type": "review",
            "acceptance_criteria": ["Änderungen sind dokumentiert und übergabefähig."],
        },
        {
            "id": "T05",
            "title": "Plan zusammenfassen",
            "status": "todo",
            "priority": "P2",
            "risk": "low",
            "type": "docs",
            "acceptance_criteria": ["Track-Status ist konsistent und nachvollziehbar."],
        },
    ]
    payload = {
        "version": "1.0",
        "owner": "operator_tui",
        "track": f"goal-{goal_id}-planning-track",
        "goal": f"Goal {goal_id}",
        "status_scale": ["todo", "in_progress", "partial", "blocked", "done"],
        "priority_scale": ["P1", "P2", "P3"],
        "risk_scale": ["low", "medium", "high"],
        "milestones": [
            {"id": "M01", "title": "Planung", "task_ids": ["T01", "T02"], "status": "todo"},
            {"id": "M02", "title": "Umsetzung", "task_ids": ["T03", "T04", "T05"], "status": "todo"},
        ],
        "tasks": tasks,
        "critical_path_tasks": ["T01", "T02", "T03", "T04"],
    }
    recomputed, _ = PlanningSummaryEngine().recompute(payload)
    return recomputed


def _task_matches_filters(task: dict[str, object], filters: dict[str, str]) -> bool:
    if filters.get("status") and str(task.get("status") or "") != str(filters.get("status") or ""):
        return False
    if filters.get("priority") and str(task.get("priority") or "") != str(filters.get("priority") or ""):
        return False
    if filters.get("risk") and str(task.get("risk") or "") != str(filters.get("risk") or ""):
        return False
    if filters.get("type") and str(task.get("type") or "") != str(filters.get("type") or ""):
        return False
    return True


def _build_plan_task_diff(left_payload: dict[str, object], right_payload: dict[str, object]) -> dict[str, object]:
    left_tasks = {
        str(item.get("id") or "").strip(): dict(item)
        for item in list(left_payload.get("tasks") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    right_tasks = {
        str(item.get("id") or "").strip(): dict(item)
        for item in list(right_payload.get("tasks") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    new_ids = sorted([task_id for task_id in right_tasks if task_id not in left_tasks])
    removed_ids = sorted([task_id for task_id in left_tasks if task_id not in right_tasks])
    changed_ids = sorted(
        [
            task_id
            for task_id in right_tasks
            if task_id in left_tasks and left_tasks[task_id] != right_tasks[task_id]
        ]
    )
    return {
        "new_tasks": [{"id": task_id, "title": str(right_tasks[task_id].get("title") or "")} for task_id in new_ids],
        "removed_tasks": [{"id": task_id, "title": str(left_tasks[task_id].get("title") or "")} for task_id in removed_ids],
        "changed_tasks": [
            {
                "id": task_id,
                "before": {
                    "title": str(left_tasks[task_id].get("title") or ""),
                    "status": str(left_tasks[task_id].get("status") or ""),
                    "priority": str(left_tasks[task_id].get("priority") or ""),
                },
                "after": {
                    "title": str(right_tasks[task_id].get("title") or ""),
                    "status": str(right_tasks[task_id].get("status") or ""),
                    "priority": str(right_tasks[task_id].get("priority") or ""),
                },
            }
            for task_id in changed_ids
        ],
    }


def _build_planning_track_payload(*, goal_id: str, game: dict[str, object]) -> dict[str, object]:
    service = GoalArtifactService()
    graph = service.get_goal_graph(goal_id)
    provenance_items = [dict(item) for item in list(dict(graph.get("extensions") or {}).get("execution_provenance") or []) if isinstance(item, dict)]
    provenance_by_id = {
        str(item.get("provenance_id") or ""): item
        for item in provenance_items
        if str(item.get("provenance_id") or "").strip()
    }
    outputs = [dict(item) for item in list(graph.get("output_artifacts") or []) if isinstance(item, dict)]
    planning_outputs = [item for item in outputs if str(item.get("artifact_type") or "") == "planning_track"]
    planning_outputs.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    goal = goal_repo.get_by_id(goal_id)
    prefs = dict(goal.execution_preferences or {}) if goal is not None else {}

    rows: list[dict[str, object]] = []
    for output in planning_outputs:
        ext = dict(output.get("extensions") or {})
        payload = dict(ext.get("payload") or {}) if isinstance(ext.get("payload"), dict) else {}
        rows.append(
            {
                "output_artifact_id": str(output.get("output_artifact_id") or ""),
                "created_at": str(output.get("created_at") or ""),
                "status": str(output.get("status") or ""),
                "verification_status": str(output.get("verification_status") or ""),
                "provenance_id": str(output.get("provenance_id") or ""),
                "active_plan_candidate": bool(ext.get("active_plan_candidate", False)),
                "quality_gate_warnings": list(ext.get("quality_gate_warnings") or []),
                "validation_issues": list(ext.get("validation_issues") or []),
                "repair_attempt_count": int(ext.get("repair_attempt_count") or 0),
                "summary_recalculation_status": str(ext.get("summary_recalculation_status") or "not_needed"),
                "repaired_fields": list(ext.get("repaired_fields") or []),
                "old_summary_hash": str(ext.get("old_summary_hash") or ""),
                "new_summary_hash": str(ext.get("new_summary_hash") or ""),
                "source_references": list(ext.get("source_references") or []),
                "context_references": list(ext.get("context_references") or []),
                "context_hash": str(ext.get("context_hash") or ""),
                "task_mapping": dict(ext.get("task_mapping") or {}),
                "provenance": dict(provenance_by_id.get(str(output.get("provenance_id") or ""), {})),
                "payload": payload,
            }
        )

    selected_output_id = str(game.get("planning_track_selected_output_id") or "")
    if not selected_output_id and rows:
        selected_output_id = str(rows[0].get("output_artifact_id") or "")
    selected_row = next((item for item in rows if str(item.get("output_artifact_id") or "") == selected_output_id), rows[0] if rows else None)
    selected_payload = dict(selected_row.get("payload") or {}) if isinstance(selected_row, dict) else {}

    task_filters = dict(game.get("planning_track_filters") or {}) if isinstance(game.get("planning_track_filters"), dict) else {}
    filtered_tasks = [
        dict(task)
        for task in list(selected_payload.get("tasks") or [])
        if isinstance(task, dict) and _task_matches_filters(task, {k: str(v) for k, v in task_filters.items()})
    ]
    selected_payload_with_filters = dict(selected_payload)
    selected_payload_with_filters["tasks_filtered"] = filtered_tasks
    selected_task_mapping = {}
    if isinstance(selected_row, dict):
        selected_payload_with_filters["quality_gate_warnings"] = list(selected_row.get("quality_gate_warnings") or [])
        selected_payload_with_filters["validation_issues"] = list(selected_row.get("validation_issues") or [])
        selected_payload_with_filters["verification_status"] = str(selected_row.get("verification_status") or "")
        selected_payload_with_filters["source_references"] = list(selected_row.get("source_references") or [])
        selected_payload_with_filters["context_references"] = list(selected_row.get("context_references") or [])
        selected_payload_with_filters["provenance"] = dict(selected_row.get("provenance") or {})
        selected_task_mapping = dict(selected_row.get("task_mapping") or {})
        selected_payload_with_filters["task_mapping"] = selected_task_mapping
        selected_payload_with_filters["summary_recalculation_status"] = str(selected_row.get("summary_recalculation_status") or "not_needed")
        selected_payload_with_filters["repaired_fields"] = list(selected_row.get("repaired_fields") or [])
        selected_payload_with_filters["old_summary_hash"] = str(selected_row.get("old_summary_hash") or "")
        selected_payload_with_filters["new_summary_hash"] = str(selected_row.get("new_summary_hash") or "")
    selected_output_task_states: dict[str, str] = {}
    selected_output_id = str(selected_output_id or "")
    if selected_output_id:
        for task in task_repo.get_by_goal_id(goal_id):
            if str(task.plan_id or "") != selected_output_id:
                continue
            if str(task.plan_node_id or "").strip():
                selected_output_task_states[str(task.plan_node_id)] = str(task.status or "")
    if selected_output_task_states:
        selected_payload_with_filters["internal_task_status"] = selected_output_task_states

    diff_state = dict(game.get("planning_track_diff") or {}) if isinstance(game.get("planning_track_diff"), dict) else {}
    return {
        "planning_track_mode": True,
        "goal_id": goal_id,
        "planning_status": str(game.get("planning_track_status") or "idle"),
        "planning_lifecycle": list(game.get("planning_track_lifecycle") or []),
        "status_hint": str(game.get("planning_track_status_hint") or ""),
        "status_issues": list(game.get("planning_track_status_issues") or []),
        "track_rows": rows,
        "selected_output_id": selected_output_id,
        "selected_track": selected_payload_with_filters,
        "task_filters": task_filters,
        "active_output_id": str(prefs.get("active_planning_track_output_id") or game.get("active_planning_track_output_id") or ""),
        "rejected_output_ids": list(prefs.get("rejected_planning_track_output_ids") or game.get("rejected_planning_track_output_ids") or []),
        "task_mapping": selected_task_mapping,
        "internal_task_status": selected_output_task_states,
        "plan_diff": diff_state,
    }


def _recompute_planning_track_output(*, goal_id: str, output_artifact_id: str) -> dict[str, object]:
    service = GoalArtifactService()
    repository = GoalArtifactRepository()
    graph = service.get_goal_graph(goal_id)
    outputs = [dict(item) for item in list(graph.get("output_artifacts") or []) if isinstance(item, dict)]
    changed = False
    result_payload: dict[str, object] = {}
    for index, output in enumerate(outputs):
        if str(output.get("output_artifact_id") or "") != str(output_artifact_id):
            continue
        ext = dict(output.get("extensions") or {})
        payload = dict(ext.get("payload") or {})
        if not payload:
            raise ValueError("planning_track_payload_missing")
        old_summary_hash = str(dict(payload.get("derived_summary_metadata") or {}).get("source_hash") or "")
        recomputed, _ = PlanningSummaryEngine().recompute(payload)
        new_summary_hash = str(dict(recomputed.get("derived_summary_metadata") or {}).get("source_hash") or "")
        ext["payload"] = recomputed
        ext["summary_recalculation_status"] = "recalculated" if old_summary_hash != new_summary_hash else "not_needed"
        ext["old_summary_hash"] = old_summary_hash
        ext["new_summary_hash"] = new_summary_hash
        ext["repaired_fields"] = [
            key
            for key in (
                "tasks_status_summary",
                "tasks_type_summary",
                "progress_summary",
                "weighted_progress_summary",
                "milestone_progress_summary",
                "derived_summary_metadata",
            )
            if dict(payload.get(key) or {}) != dict(recomputed.get(key) or {})
        ]
        output["extensions"] = ext
        outputs[index] = output
        result_payload = {
            "summary_recalculation_status": ext["summary_recalculation_status"],
            "repaired_fields": list(ext.get("repaired_fields") or []),
            "old_summary_hash": old_summary_hash,
            "new_summary_hash": new_summary_hash,
        }
        changed = True
        break
    if not changed:
        raise ValueError("planning_track_output_not_found")
    graph["output_artifacts"] = outputs
    graph["updated_at"] = _now_iso()
    repository.save_graph(graph)
    return result_payload




def handle_diff3_command(args: list[str], state: OperatorState) -> CommandResult:
    """Dispatch :diff3 subcommands."""
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


def handle_plan_command(args: list[str], state: OperatorState) -> CommandResult:
    """Dispatch :plan subcommands."""
    if not args:
        return CommandResult(state, "plan track [--from-goal <goal-id>] | plan summary doctor|fix|recompute", handled=False)
    if str(args[0]).lower() == "summary":
        sub = str(args[1]).lower() if len(args) > 1 else ""
        if sub == "doctor":
            if len(args) < 3:
                return CommandResult(state, "plan summary doctor <file>", handled=False)
            path = str(args[2]).strip()
            result = doctor_file(path)
            message = json.dumps(result, ensure_ascii=False)
            return CommandResult(state.with_updates(status_message=f"plan summary doctor {result.get('format')}"), message, handled=True)
        if sub == "fix":
            if len(args) < 3:
                return CommandResult(state, "plan summary fix <file>", handled=False)
            path = str(args[2]).strip()
            result = fix_file(path, write=True)
            payload = {k: v for k, v in result.items() if k != "payload"}
            message = json.dumps(payload, ensure_ascii=False)
            return CommandResult(
                state.with_updates(status_message=f"plan summary fix changed={bool(result.get('changed'))}"),
                message,
                handled=True,
            )
        if sub == "recompute":
            game = dict(state.header_logo_game or {})
            goal_id = str(game.get("active_goal_id") or "").strip()
            if not goal_id:
                return CommandResult(state, "plan summary recompute requires active goal", handled=False)
            output_id = str(game.get("planning_track_selected_output_id") or game.get("active_planning_track_output_id") or "").strip()
            if not output_id:
                return CommandResult(state, "plan summary recompute requires selected planning output", handled=False)
            try:
                recompute_result = _recompute_planning_track_output(goal_id=goal_id, output_artifact_id=output_id)
            except ValueError as exc:
                return CommandResult(state, f"plan summary recompute blocked reason={str(exc)}", handled=False)
            refreshed = _build_planning_track_payload(goal_id=goal_id, game=game)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = refreshed
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            return CommandResult(
                state.with_updates(
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message=f"plan summary recompute {output_id}",
                ),
                json.dumps({"output_artifact_id": output_id, **recompute_result, "payload": refreshed}, ensure_ascii=False),
                handled=True,
            )
        return CommandResult(state, "plan summary doctor <file> | fix <file> | recompute", handled=False)
    if str(args[0]).lower() != "track":
        return CommandResult(state, "plan track [--from-goal <goal-id>] | plan summary doctor|fix|recompute", handled=False)
    game = dict(state.header_logo_game or {})
    tail = list(args[1:])
    explicit_goal_id = ""
    if "--from-goal" in [str(item).lower() for item in tail]:
        lowered = [str(item).lower() for item in tail]
        idx = lowered.index("--from-goal")
        explicit_goal_id = str(tail[idx + 1]).strip() if idx + 1 < len(tail) else ""
    goal_id = explicit_goal_id or str(game.get("active_goal_id") or "").strip()
    if not goal_id:
        return CommandResult(state, "plan track requires active goal or --from-goal <goal-id>", handled=False)
    if explicit_goal_id:
        game["active_goal_id"] = goal_id

    sub = str(tail[0]).lower() if tail and not str(tail[0]).startswith("--") else ""
    service = GoalArtifactService()
    integration = PlanningTrackTaskIntegrationService(goal_artifact_service=service)

    if sub == "filter":
        filters = dict(game.get("planning_track_filters") or {})
        for token in tail[1:]:
            text = str(token).strip()
            if "=" not in text:
                continue
            key, value = text.split("=", 1)
            if key.strip() in {"status", "priority", "risk", "type"}:
                if value.strip():
                    filters[key.strip()] = value.strip()
        game["planning_track_filters"] = filters
        payload = _build_planning_track_payload(goal_id=goal_id, game=game)
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
                status_message=f"plan track filter {goal_id}",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "clear-filter":
        game["planning_track_filters"] = {}
        payload = _build_planning_track_payload(goal_id=goal_id, game=game)
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
                status_message=f"plan track clear-filter {goal_id}",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "adopt":
        if len(tail) < 2:
            return CommandResult(state, "plan track adopt <output-artifact-id>", handled=False)
        output_id = str(tail[1]).strip()
        try:
            materialized = integration.adopt_track(goal_id=goal_id, output_artifact_id=output_id)
        except ValueError as exc:
            return CommandResult(state, f"plan track adopt blocked output={output_id} reason={str(exc)}", handled=False)
        game["active_planning_track_output_id"] = output_id
        game["planning_track_selected_output_id"] = output_id
        service.upsert_execution_provenance(
            goal_id=goal_id,
            provenance={
                "schema": "execution_provenance.v1",
                "provenance_id": f"prov-{hashlib.sha1(f'{goal_id}:{output_id}:adopt'.encode('utf-8')).hexdigest()[:16]}",
                "goal_id": goal_id,
                "task_id": f"plan-adopt:{output_id}",
                "execution_id": f"exec-{hashlib.sha1(f'{goal_id}:{output_id}:adopt:exec'.encode('utf-8')).hexdigest()[:14]}",
                "worker_id": "operator_tui",
                "worker_kind": "operator",
                "runtime_target_ref": {"runtime_type": "operator-tui", "location": "local"},
                "model_ref": {"provider_id": "none", "model_id": "manual"},
                "config_refs": {
                    "worker_config_ref": "cfg:operator_tui",
                    "runtime_config_ref": "cfg:operator_tui",
                    "model_config_ref": "cfg:none",
                    "policy_config_ref": "cfg:operator_tui_policy",
                },
                "prompt_refs": {"no_prompt_reason": "manual_plan_adopt"},
                "input_usage_refs": [],
                "output_artifact_refs": [output_id],
                "created_at": _now_iso(),
                "extensions": {
                    "materialized_task_count": len(list(materialized.get("materialized_task_ids") or [])),
                    "plan_task_to_internal_task": dict(materialized.get("plan_task_to_internal_task") or {}),
                },
            },
        )
        payload = _build_planning_track_payload(goal_id=goal_id, game=game)
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
                status_message=f"plan track adopted {output_id}",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "reject":
        if len(tail) < 2:
            return CommandResult(state, "plan track reject <output-artifact-id>", handled=False)
        output_id = str(tail[1]).strip()
        rejected_result = integration.reject_track(goal_id=goal_id, output_artifact_id=output_id)
        game["rejected_planning_track_output_ids"] = list(rejected_result.get("rejected_output_ids") or [])
        if str(game.get("active_planning_track_output_id") or "") == output_id:
            game["active_planning_track_output_id"] = ""
        payload = _build_planning_track_payload(goal_id=goal_id, game=game)
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
                status_message=f"plan track rejected {output_id}",
            ),
            json.dumps(payload, ensure_ascii=False),
        )
    if sub == "diff":
        if len(tail) < 3:
            return CommandResult(state, "plan track diff <left-output-id> <right-output-id>", handled=False)
        left_id = str(tail[1]).strip()
        right_id = str(tail[2]).strip()
        payload = _build_planning_track_payload(goal_id=goal_id, game=game)
        rows = list(payload.get("track_rows") or [])
        left_row = next((item for item in rows if str(item.get("output_artifact_id") or "") == left_id), None)
        right_row = next((item for item in rows if str(item.get("output_artifact_id") or "") == right_id), None)
        if not isinstance(left_row, dict) or not isinstance(right_row, dict):
            return CommandResult(state, "plan track diff requires two existing planning track outputs", handled=False)
        left_payload = dict(left_row.get("payload") or {})
        right_payload = dict(right_row.get("payload") or {})
        diff = _build_plan_task_diff(left_payload, right_payload)
        game["planning_track_diff"] = {"left_output_id": left_id, "right_output_id": right_id, **diff}
        refreshed = _build_planning_track_payload(goal_id=goal_id, game=game)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = refreshed
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"plan track diff {left_id}->{right_id}",
            ),
            json.dumps(refreshed, ensure_ascii=False),
        )
    if sub == "execute-next":
        output_id = str(game.get("active_planning_track_output_id") or "").strip()
        if not output_id:
            return CommandResult(state, "plan track execute-next requires an adopted output", handled=False)
        try:
            execution = integration.execute_next_plan_task(
                goal_id=goal_id,
                output_artifact_id=output_id,
                worker_id="operator_tui",
            )
        except ValueError as exc:
            return CommandResult(state, f"plan track execute-next blocked reason={str(exc)}", handled=False)
        game["planning_track_last_execution"] = execution
        refreshed = _build_planning_track_payload(goal_id=goal_id, game=game)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = refreshed
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"plan track execute-next {execution.get('plan_task_id')}->{execution.get('internal_task_id')}",
            ),
            json.dumps(refreshed, ensure_ascii=False),
        )
    if sub == "sync-status":
        if len(tail) < 3:
            return CommandResult(state, "plan track sync-status <plan-task-id> <todo|in_progress|blocked|completed|failed>", handled=False)
        output_id = str(game.get("active_planning_track_output_id") or "").strip()
        if not output_id:
            return CommandResult(state, "plan track sync-status requires an adopted output", handled=False)
        plan_task_id = str(tail[1]).strip()
        internal_status = str(tail[2]).strip().lower()
        try:
            integration.sync_plan_status_from_internal_task(
                goal_id=goal_id,
                output_artifact_id=output_id,
                plan_task_id=plan_task_id,
                internal_status=internal_status,
            )
        except ValueError as exc:
            return CommandResult(state, f"plan track sync-status blocked reason={str(exc)}", handled=False)
        refreshed = _build_planning_track_payload(goal_id=goal_id, game=game)
        section_payloads = dict(state.section_payloads or {})
        section_payloads["artifacts"] = refreshed
        panel_states = dict(state.panel_states or {})
        panel_states["artifacts"] = PanelState.HEALTHY
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                section_id="artifacts",
                selected_index=0,
                section_payloads=section_payloads,
                panel_states=panel_states,
                status_message=f"plan track sync-status {plan_task_id}={internal_status}",
            ),
            json.dumps(refreshed, ensure_ascii=False),
        )

    # Default: run planner track generation (mock worker path in tests/dev).
    graph = service.get_goal_graph(goal_id)
    source_grants = [dict(item) for item in list(graph.get("source_grants") or []) if isinstance(item, dict)]
    available_artifacts = [{"source_ref": str(item.get("artifact_ref") or "").strip()} for item in source_grants if str(item.get("artifact_ref") or "").strip()]
    allowed_source_refs = [str(item.get("artifact_ref") or "").strip() for item in source_grants if str(item.get("artifact_ref") or "").strip()]
    context_envelope = build_planner_context_envelope(
        goal_id=goal_id,
        goal_text=f"Goal {goal_id}",
        constraints=[],
        available_artifacts=available_artifacts,
        allowed_source_refs=allowed_source_refs,
        codecompass_refs=[],
    )
    final_prompt = render_track_planning_prompt(goal_text=f"Goal {goal_id}", context_envelope=context_envelope)
    raw_output = str(game.get("planner_mock_output") or "").strip()
    if not raw_output:
        raw_output = json.dumps(_build_mock_planning_track_payload(goal_id), ensure_ascii=False)
    game["planning_track_status"] = "pending"
    lifecycle = ["pending", "validating"]
    result = persist_planning_track_result(
        goal_id=goal_id,
        task_id=f"plan-track:{goal_id}",
        worker_id="ananta-worker/planner-mock",
        raw_output=raw_output,
        prompt_template_ref="prompt:planning/track_planning",
        final_prompt=final_prompt,
        model_ref={"provider_id": "mock", "model_id": "planner-track-mock"},
        config_refs={
            "worker_config_ref": "cfg:planning-track",
            "runtime_config_ref": "cfg:planning-track",
            "model_config_ref": "cfg:planning-track",
            "policy_config_ref": "cfg:planning-track",
        },
        available_artifacts=available_artifacts,
        goal_artifact_service=service,
    )
    if int(result.get("repair_attempt_count") or 0) > 0:
        lifecycle.append("repaired")
    lifecycle.append(str(result.get("status") or "failed"))
    game["planning_track_status"] = str(result.get("status") or "failed")
    game["planning_track_lifecycle"] = lifecycle
    game["planning_track_status_hint"] = f"source_usage_refs={len(list(result.get('source_usage_refs') or []))}"
    game["planning_track_status_issues"] = list(result.get("issues") or [])
    output_artifact = dict(result.get("output_artifact") or {})
    selected_output_id = str(output_artifact.get("output_artifact_id") or "")
    if selected_output_id:
        game["planning_track_selected_output_id"] = selected_output_id
    refreshed = _build_planning_track_payload(goal_id=goal_id, game=game)
    section_payloads = dict(state.section_payloads or {})
    section_payloads["artifacts"] = refreshed
    panel_states = dict(state.panel_states or {})
    panel_states["artifacts"] = PanelState.HEALTHY
    issue_preview = list(result.get("issues") or [])[:3]
    issue_label = (
        "; ".join(f"{item.get('path')}:{item.get('reason_code')}" for item in issue_preview if isinstance(item, dict))
        if issue_preview
        else "none"
    )
    return CommandResult(
        state.with_updates(
            header_logo_game=game,
            section_id="artifacts",
            selected_index=0,
            section_payloads=section_payloads,
            panel_states=panel_states,
            status_message=f"plan track {goal_id} {result.get('status')} issues={issue_label}",
        ),
        json.dumps(refreshed, ensure_ascii=False),
    )
