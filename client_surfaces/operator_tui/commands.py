from __future__ import annotations

import json
import hashlib
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from agent.artifacts.artifact_access_policy import ArtifactAccessPolicy
from agent.artifacts.artifact_candidate_service import ArtifactCandidateService
from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
from agent.repository import goal_repo, task_repo
from agent.services.planning_summary_doctor_service import doctor_file, fix_file
from agent.services.planning_summary_engine import PlanningSummaryEngine
from agent.sources.citation_formatter import format_citation
from agent.sources.builtin_sources import load_builtin_source_descriptors
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_pack_service import SourcePackService
from agent.sources.source_snapshot_store import SourceSnapshotStore
from client_surfaces.operator_tui.actions import dispatch_action, parse_action
from client_surfaces.operator_tui.ai_snake_learning import apply_prediction_feedback, event_for_prediction_feedback
from client_surfaces.operator_tui.browser import browser_fallback_url
from client_surfaces.operator_tui.ai_snake_context import get_ai_context
from client_surfaces.operator_tui.ai_snake_config_view import refresh_chat_backend_models
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
from agent.services.planning_track_pipeline_service import persist_planning_track_result
from agent.services.planning_track_planner_service import build_planner_context_envelope, render_track_planning_prompt
from agent.services.planning_track_task_integration_service import PlanningTrackTaskIntegrationService
from agent.services.helpcenter_contract_service import load_helpcenter_index
from agent.services.helpcenter_ingest_service import ingest_github_failures, StaticGithubWorkflowApiClient
from agent.services.imap_account_service import (
    create_imap_account,
    delete_imap_account,
    disable_imap_account,
    list_imap_accounts,
)
from agent.services.imap_attachment_service import attachment_metadata, download_attachment_securely
from agent.services.imap_export_service import export_mail_payload
from agent.services.imap_feature_flag_service import resolve_imap_runtime_state
from agent.services.imap_mail_artifact_service import get_mail_artifact, list_mail_artifacts, register_mail_artifact
from agent.services.imap_mail_context_envelope_service import build_mail_context_envelope
from agent.services.imap_metadata_store_service import ImapMetadataStore
from agent.services.imap_redaction_pipeline_service import redact_mail_for_worker_context
from agent.services.imap_search_service import search_mail_metadata
from agent.services.imap_snake_assist_service import explain_mail_for_snake_assist
from agent.services.imap_threading_service import annotate_messages_with_thread_counts


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


def _helpcenter_repo_root() -> Path:
    return Path.cwd()


def _mail_repo_root() -> Path:
    return Path.cwd()


def _mail_store(repo_root: Path) -> ImapMetadataStore:
    return ImapMetadataStore(store_path=repo_root / "data" / "imap" / "mail-metadata.json")


def _mail_message_key(row: dict[str, object]) -> str:
    ref = dict(row.get("message_ref") or {})
    message_id = str(ref.get("message_id") or "").strip()
    if message_id:
        return message_id
    return f"{ref.get('account_id')}::{ref.get('mailbox')}::{ref.get('uid')}"


def _build_mail_payload(*, game: dict[str, object], repo_root: Path) -> dict[str, object]:
    accounts = list_imap_accounts(repo_root=repo_root)
    selected_account_id = str(game.get("mail_selected_account_id") or "").strip()
    if not selected_account_id and accounts:
        selected_account_id = str(accounts[0].get("account_id") or "")
        game["mail_selected_account_id"] = selected_account_id
    selected_account = next(
        (item for item in accounts if str(item.get("account_id") or "") == selected_account_id),
        dict(accounts[0]) if accounts else {},
    )
    cfg = dict(game.get("imap_config") or {"imap": {"enabled": True}})
    connected = {str(item) for item in list(game.get("imap_connected_account_ids") or []) if str(item).strip()}
    syncing = {str(item) for item in list(game.get("imap_syncing_account_ids") or []) if str(item).strip()}
    account_status_rows: list[dict[str, object]] = []
    for account in accounts:
        account_id = str(account.get("account_id") or "")
        if not bool(account.get("enabled", True)):
            state_row = {"state": "disabled", "reason_code": "account_disabled"}
        else:
            state_row = resolve_imap_runtime_state(
                cfg,
                has_account=True,
                connected=account_id in connected,
                syncing=account_id in syncing,
            )
        account_status_rows.append(
            {
                "account_id": account_id,
                "display_name": str(account.get("display_name") or ""),
                "enabled": bool(account.get("enabled", True)),
                **state_row,
            }
        )

    store = _mail_store(repo_root)
    rows = store.list_messages()
    mock_rows = [dict(item) for item in list(game.get("mail_mock_messages") or []) if isinstance(item, dict)]
    if mock_rows:
        rows.extend(mock_rows)
    if selected_account_id:
        rows = [item for item in rows if str(dict(item.get("message_ref") or {}).get("account_id") or "") == selected_account_id]
    mailbox_set = sorted(
        {
            str(dict(item.get("message_ref") or {}).get("mailbox") or "").strip()
            for item in rows
            if str(dict(item.get("message_ref") or {}).get("mailbox") or "").strip()
        }
    )
    if not mailbox_set:
        mock_mailboxes = dict(game.get("mail_mock_mailboxes_by_account") or {})
        mailbox_set = [
            str(item).strip()
            for item in list(mock_mailboxes.get(selected_account_id) or ["INBOX"])
            if str(item).strip()
        ]
    selected_mailbox = str(game.get("mail_selected_mailbox") or "").strip()
    if not selected_mailbox and mailbox_set:
        selected_mailbox = mailbox_set[0]
        game["mail_selected_mailbox"] = selected_mailbox

    filters = dict(game.get("mail_filters") or {})
    query_filters = dict(filters)
    if selected_mailbox:
        query_filters.setdefault("mailbox", selected_mailbox)
    search_rows = rows
    if not mock_rows:
        by_key: dict[str, dict[str, object]] = {}
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            ref = dict(raw.get("message_ref") or {})
            key = f"{ref.get('account_id')}::{ref.get('mailbox')}::{ref.get('uid')}"
            by_key[key] = dict(raw)
        search = search_mail_metadata(
            store=store,
            filters=query_filters,
            include_body_search=False,
        )
        search_rows = [
            {
                **dict(
                    by_key.get(
                        f"{dict(item.get('message_ref') or {}).get('account_id')}::"
                        f"{dict(item.get('message_ref') or {}).get('mailbox')}::"
                        f"{dict(item.get('message_ref') or {}).get('uid')}",
                        {},
                    )
                ),
                "message_ref": dict(item.get("message_ref") or {}),
                "header_meta": dict(item.get("header_meta") or {}),
                "stale": bool(item.get("stale", False)),
                "body_scope": str(item.get("policy_state") or "metadata_only"),
                "source_ref": str(item.get("source_ref") or ""),
                "body": str(
                    dict(
                        by_key.get(
                            f"{dict(item.get('message_ref') or {}).get('account_id')}::"
                            f"{dict(item.get('message_ref') or {}).get('mailbox')}::"
                            f"{dict(item.get('message_ref') or {}).get('uid')}",
                            {},
                        )
                    ).get("body")
                    or ""
                ),
                "attachments": [
                    dict(att)
                    for att in list(
                        dict(
                            by_key.get(
                                f"{dict(item.get('message_ref') or {}).get('account_id')}::"
                                f"{dict(item.get('message_ref') or {}).get('mailbox')}::"
                                f"{dict(item.get('message_ref') or {}).get('uid')}",
                                {},
                            )
                        ).get("attachments")
                        or []
                    )
                    if isinstance(att, dict)
                ],
            }
            for item in list(search.get("results") or [])
            if isinstance(item, dict)
        ]
    else:
        def _match_row(row: dict[str, object]) -> bool:
            ref = dict(row.get("message_ref") or {})
            header = dict(row.get("header_meta") or {})
            mailbox = str(ref.get("mailbox") or "")
            if query_filters.get("mailbox") and mailbox != str(query_filters.get("mailbox")):
                return False
            if query_filters.get("from") and str(query_filters.get("from")).lower() not in str(ref.get("from") or "").lower():
                return False
            if query_filters.get("subject") and str(query_filters.get("subject")).lower() not in str(header.get("subject") or "").lower():
                return False
            unread = query_filters.get("unread")
            if unread is not None and bool(header.get("unread")) is not bool(unread):
                return False
            return True

        search_rows = [row for row in rows if _match_row(row)]

    threaded_rows = annotate_messages_with_thread_counts(search_rows)
    offset = max(0, int(game.get("mail_list_offset") or 0))
    page_size = 20
    page_rows = threaded_rows[offset : offset + page_size]
    selected_message_key = str(game.get("mail_selected_message_key") or "").strip()
    selected_row = next((row for row in threaded_rows if _mail_message_key(row) == selected_message_key), dict(page_rows[0]) if page_rows else {})
    selected_detail = {
        "message_ref": dict(selected_row.get("message_ref") or {}),
        "header_meta": dict(selected_row.get("header_meta") or {}),
        "body_scope": str(selected_row.get("body_scope") or "metadata_only"),
        "redaction_status": str(game.get("mail_detail_redaction_status") or selected_row.get("redaction_status") or "not_required"),
        "body_loaded": bool(game.get("mail_detail_body_loaded", False)),
        "body_text": str(game.get("mail_detail_body") or "") if bool(game.get("mail_detail_body_loaded", False)) else "",
        "attachments": attachment_metadata([dict(item) for item in list(selected_row.get("attachments") or []) if isinstance(item, dict)]),
        "attachment_downloaded": dict(game.get("mail_attachment_last_download") or {}),
    }
    current_artifact = get_mail_artifact(
        artifact_ref=str(game.get("mail_current_artifact_ref") or ""),
        repo_root=repo_root,
    )
    return {
        "mail_mode": True,
        "accounts": account_status_rows,
        "selected_account_id": selected_account_id,
        "selected_account": selected_account,
        "mailboxes": mailbox_set,
        "selected_mailbox": selected_mailbox,
        "filters": filters,
        "list_offset": offset,
        "total_messages": len(threaded_rows),
        "messages": page_rows,
        "selected_message_key": _mail_message_key(selected_row) if selected_row else "",
        "selected_detail": selected_detail,
        "last_search_query": str(game.get("mail_last_search_query") or ""),
        "search_result_refs": [str(item) for item in list(game.get("mail_search_result_refs") or []) if str(item).strip()],
        "notes": [dict(item) for item in list(game.get("mail_notes") or []) if isinstance(item, dict)],
        "linked_goal_refs": [str(item) for item in list(game.get("mail_linked_goal_refs") or []) if str(item).strip()],
        "current_artifact_ref": str(game.get("mail_current_artifact_ref") or ""),
        "current_artifact": dict(current_artifact or {}) if isinstance(current_artifact, dict) else {},
        "artifact_count": len(list_mail_artifacts(repo_root=repo_root)),
    }


def _build_helpcenter_payload(*, game: dict[str, object], repo_root: Path) -> dict[str, object]:
    index = load_helpcenter_index(repo_root=repo_root)
    rows = [dict(item) for item in list(index.get("reports") or []) if isinstance(item, dict)]
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    selected_analysis_id = str(game.get("helpcenter_selected_analysis_id") or "").strip()
    if not selected_analysis_id and rows:
        selected_analysis_id = str(rows[0].get("analysis_id") or "")
        game["helpcenter_selected_analysis_id"] = selected_analysis_id
    selected_row = next((item for item in rows if str(item.get("analysis_id") or "") == selected_analysis_id), rows[0] if rows else None)
    selected_analysis: dict[str, object] = {}
    if isinstance(selected_row, dict):
        json_ref = str(selected_row.get("json_ref") or "").strip()
        if json_ref:
            json_path = repo_root / json_ref
            if json_path.exists():
                try:
                    payload = json.loads(json_path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        selected_analysis = payload
                except json.JSONDecodeError:
                    selected_analysis = {}
    followup = str(selected_analysis.get("suggested_followup_task") or "").strip()
    return {
        "helpcenter_mode": True,
        "reports": rows,
        "selected_analysis_id": selected_analysis_id,
        "selected_report": dict(selected_row or {}) if isinstance(selected_row, dict) else {},
        "selected_analysis": selected_analysis,
        "followup_suggestion": followup,
        "last_ingest": dict(game.get("helpcenter_last_ingest") or {}),
    }


def execute_command(raw_command: str, state: OperatorState) -> CommandResult:
    text = str(raw_command or "").strip()
    if text.startswith(":") or text.startswith("/"):
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
                msg = "chat: [c] focus | Esc game | :chat room|ai|@id|retry | :chat backend list|use|status | :chat model list|use"
                return CommandResult(state.with_updates(status_message=msg), "help chat")
            if sub == "notes":
                msg = "notes: :notes | :notes find <t> | :notes pin/unpin/delete <id> | LOCAL ONLY"
                return CommandResult(state.with_updates(status_message=msg), "help notes")
        return CommandResult(state.with_updates(show_help=not state.show_help, status_message="help toggled"), "help toggled")
    if command in {"config", "cfg", "ai-config", "snake-config"}:
        game = dict(state.header_logo_game or {})
        opened = not bool(game.get("ai_snake_config_open"))
        game["ai_snake_config_open"] = opened
        if opened:
            game["artifact_chat_focus"] = False
            from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
            chat = get_chat_state(game)
            chat["chat_focus"] = False
            set_chat_state(game, chat)
            game["ai_snake_config_combo"] = {
                "open": False,
                "key": "",
                "filter": "",
                "filter_cursor": 0,
                "selected_option": 0,
            }
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    focus=FocusPane.CONTENT,
                    selected_index=0,
                    status_message="ai config: offen",
                ),
                "ai config opened",
            )
        game["ai_snake_config_combo"] = {"open": False}
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message="ai config: geschlossen",
            ),
            "ai config closed",
        )
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
    if command == "visual":
        game = dict(state.header_logo_game or {})
        action = str(args[0]).strip().lower() if args else "status"
        current_enabled = bool(game.get("visual_viewport_enabled"))
        if action in {"on", "off", "toggle"}:
            if action == "toggle":
                enabled = not current_enabled
            else:
                enabled = action == "on"
            game["visual_viewport_enabled"] = enabled
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    status_message=f"visual viewport: {'an' if enabled else 'aus'}",
                ),
                "visual toggled",
            )
        if action == "list":
            views = [str(item) for item in (game.get("visual_viewport_available_views") or []) if str(item).strip()]
            listed = ", ".join(views) if views else "(keine bekannt)"
            return CommandResult(
                state.with_updates(status_message=f"visual views: {listed}"),
                "visual views listed",
            )
        if action == "view":
            if len(args) < 2:
                return CommandResult(state, "visual view: id erforderlich", handled=False)
            target = str(args[1]).strip()
            if not target:
                return CommandResult(state, "visual view: id erforderlich", handled=False)
            available_views = [str(item) for item in (game.get("visual_viewport_available_views") or []) if str(item).strip()]
            if available_views and target not in available_views:
                listed = ", ".join(available_views)
                return CommandResult(
                    state.with_updates(status_message=f"visual view unbekannt: {target} | {listed}"),
                    "visual view unknown",
                    handled=False,
                )
            game["visual_viewport_active_view_request"] = target
            game["visual_viewport_enabled"] = True
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    status_message=f"visual view requested: {target}",
                ),
                "visual view requested",
            )
        if action == "status":
            runtime = dict(game.get("visual_runtime_status") or {})
            view = str(runtime.get("active_view") or game.get("visual_viewport_active_view") or "-")
            renderer = str(runtime.get("active_renderer") or "-")
            adapter = str(runtime.get("active_adapter") or "-")
            return CommandResult(
                state.with_updates(
                    status_message=(
                        f"visual: {'an' if current_enabled else 'aus'} "
                        f"view={view} renderer={renderer} adapter={adapter}"
                    )
                ),
                "visual status",
            )
        return CommandResult(state, "visual: on|off|toggle|status|list|view <id>", handled=False)
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
        pack_service = SourcePackService(registry=registry, snapshots=snapshots)
        cache = refresh_service = None
        for descriptor in load_builtin_source_descriptors():
            source_id = str(descriptor.get("source_id") or "").strip()
            if source_id and registry.get_source(source_id) is None:
                registry.create_source(descriptor)
        refresh_service = SourceRefreshService(registry=registry, snapshots=snapshots)
        cache = refresh_service.cache
        if action == "packs":
            packs = pack_service.list_packs()
            if not packs:
                return CommandResult(state.with_updates(status_message="sources packs: none"), "[]")
            preview = " | ".join(
                f"{str(item.get('source_pack_id') or '')}:{str(item.get('display_name') or '')}"
                for item in packs[:10]
            )
            return CommandResult(
                state.with_updates(status_message=f"sources packs {len(packs)}"),
                json.dumps({"count": len(packs), "packs": packs, "preview": preview}, ensure_ascii=False),
            )
        if action == "pack":
            if len(args) < 2:
                return CommandResult(state, "sources pack show|bootstrap <source-pack-id> [--dry-run]", handled=False)
            sub = str(args[1]).lower()
            if sub == "show":
                if len(args) < 3:
                    return CommandResult(state, "sources pack show <source-pack-id>", handled=False)
                source_pack_id = str(args[2]).strip()
                try:
                    pack = pack_service.get_pack(source_pack_id)
                except ValueError:
                    return CommandResult(state, f"sources: unknown source-pack {source_pack_id}", handled=False)
                selected = [
                    dict(item) for item in list(pack.get("sources") or [])
                    if isinstance(item, dict) and str(item.get("source_id") or "").strip()
                ]
                preview = " | ".join(
                    f"{str(item.get('source_id') or '')}:{str(item.get('source_priority') or '-')}"
                    for item in selected[:10]
                )
                payload = {
                    "source_pack_id": source_pack_id,
                    "display_name": str(pack.get("display_name") or ""),
                    "source_count": len(selected),
                    "sources": selected,
                    "preview": preview,
                }
                return CommandResult(
                    state.with_updates(status_message=f"sources pack show {source_pack_id}"),
                    json.dumps(payload, ensure_ascii=False),
                )
            if sub == "bootstrap":
                if len(args) < 3:
                    return CommandResult(state, "sources pack bootstrap <source-pack-id> [--dry-run]", handled=False)
                source_pack_id = str(args[2]).strip()
                dry_run = any(str(x).lower() == "--dry-run" for x in args[3:])
                result = pack_service.bootstrap(source_pack_id=source_pack_id, dry_run=dry_run)
                msg = f"sources pack bootstrap {source_pack_id}: {str(result.get('status') or 'unknown')}"
                return CommandResult(state.with_updates(status_message=msg[:240]), json.dumps(result, ensure_ascii=False))
            if sub == "query":
                if len(args) < 4:
                    return CommandResult(state, "sources pack query <source-pack-id> <question>", handled=False)
                source_pack_id = str(args[2]).strip()
                query = " ".join(args[3:]).strip()
                result = pack_service.answer_preview(source_pack_id=source_pack_id, query=query)
                origins = ", ".join(list(result.get("origins") or []))
                msg = f"sources pack query {source_pack_id}: origins={origins or '-'}"
                return CommandResult(state.with_updates(status_message=msg[:240]), json.dumps(result, ensure_ascii=False))
            return CommandResult(state, "sources pack show|bootstrap|query <source-pack-id> [--dry-run|question]", handled=False)
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
        return CommandResult(state, "sources: list | packs | pack show <id> | pack bootstrap <id> [--dry-run] | pack query <id> <question> | refresh <id> | snapshots <id> | cite <id> | cache <id> [clear]", handled=False)
    if command == "helpcenter":
        game = dict(state.header_logo_game or {})
        repo_root = _helpcenter_repo_root()
        if not args:
            payload = _build_helpcenter_payload(game=game, repo_root=repo_root)
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
                    status_message="helpcenter opened",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        sub = str(args[0]).lower()
        if sub == "open":
            if len(args) < 2:
                return CommandResult(state, "helpcenter open <analysis-id>", handled=False)
            game["helpcenter_selected_analysis_id"] = str(args[1]).strip()
            payload = _build_helpcenter_payload(game=game, repo_root=repo_root)
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
                    status_message=f"helpcenter open {args[1]}",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        if sub == "suggest-followup":
            analysis_id = str(args[1]).strip() if len(args) > 1 else str(game.get("helpcenter_selected_analysis_id") or "").strip()
            if analysis_id:
                game["helpcenter_selected_analysis_id"] = analysis_id
            payload = _build_helpcenter_payload(game=game, repo_root=repo_root)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = payload
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            followup = str(payload.get("followup_suggestion") or "").strip() or "no follow-up suggestion available"
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message="helpcenter follow-up suggestion ready (no task created)",
                ),
                json.dumps({"analysis_id": analysis_id, "followup_suggestion": followup, "auto_create": False, "payload": payload}, ensure_ascii=False),
            )
        if sub == "ingest":
            source_kind = str(args[1]).lower() if len(args) > 1 else ""
            if source_kind != "github-failures":
                return CommandResult(state, "helpcenter ingest github-failures [--repo owner/repo] [--limit N] [--dry-run]", handled=False)
            repo = "ananta888/ananta"
            limit = 5
            dry_run = False
            tokens = list(args[2:])
            i = 0
            while i < len(tokens):
                token = str(tokens[i]).strip().lower()
                if token == "--repo" and i + 1 < len(tokens):
                    repo = str(tokens[i + 1]).strip()
                    i += 2
                    continue
                if token == "--limit" and i + 1 < len(tokens):
                    try:
                        limit = max(1, int(str(tokens[i + 1]).strip()))
                    except ValueError:
                        return CommandResult(state, "helpcenter ingest --limit requires integer", handled=False)
                    i += 2
                    continue
                if token == "--dry-run":
                    dry_run = True
                    i += 1
                    continue
                i += 1
            mock_rows = [dict(item) for item in list(game.get("helpcenter_mock_github_rows") or []) if isinstance(item, dict)]
            api_client = StaticGithubWorkflowApiClient(rows=mock_rows) if mock_rows else None
            result = ingest_github_failures(
                repo=repo,
                limit=limit,
                dry_run=dry_run,
                repo_root=str(repo_root),
                api_client=api_client,
            )
            game["helpcenter_last_ingest"] = result
            payload = _build_helpcenter_payload(game=game, repo_root=repo_root)
            section_payloads = dict(state.section_payloads or {})
            section_payloads["artifacts"] = payload
            panel_states = dict(state.panel_states or {})
            panel_states["artifacts"] = PanelState.HEALTHY
            mode_label = "dry-run" if dry_run else "write"
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    section_id="artifacts",
                    selected_index=0,
                    section_payloads=section_payloads,
                    panel_states=panel_states,
                    status_message=f"helpcenter ingest {mode_label} found={result.get('found')} written={result.get('written')}",
                ),
                json.dumps({"ingest": result, "payload": payload}, ensure_ascii=False),
            )
        return CommandResult(
            state,
            "helpcenter | helpcenter open <analysis-id> | helpcenter ingest github-failures [--repo owner/repo] [--limit N] [--dry-run] | helpcenter suggest-followup [analysis-id]",
            handled=False,
        )
    if command == "mail":
        repo_root = _mail_repo_root()
        game = dict(state.header_logo_game or {})
        if not args:
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message="mail view opened",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        sub = str(args[0]).lower()

        def _option(tokens: list[str], name: str) -> str:
            key = f"--{name}"
            for idx, token in enumerate(tokens):
                if str(token).strip().lower() == key and idx + 1 < len(tokens):
                    return str(tokens[idx + 1]).strip()
            return ""

        if sub == "account":
            if len(args) < 2:
                return CommandResult(state, "mail account list|status|create|disable|delete|use", handled=False)
            action = str(args[1]).lower()
            if action == "list":
                accounts = list_imap_accounts(repo_root=repo_root)
                return CommandResult(
                    state.with_updates(status_message=f"mail accounts={len(accounts)}"),
                    json.dumps({"accounts": accounts}, ensure_ascii=False),
                )
            if action == "status":
                payload = _build_mail_payload(game=game, repo_root=repo_root)
                return CommandResult(
                    state.with_updates(header_logo_game=game, status_message=f"mail account status rows={len(payload.get('accounts') or [])}"),
                    json.dumps({"accounts": payload.get("accounts") or []}, ensure_ascii=False),
                )
            if action == "create":
                tokens = list(args[2:])
                if any(str(token).strip().lower() in {"--password", "--token"} for token in tokens):
                    return CommandResult(state, "mail account create requires credential_ref, not password/token", handled=False)
                display_name = _option(tokens, "display-name")
                host = _option(tokens, "host")
                port_text = _option(tokens, "port")
                username = _option(tokens, "username")
                credential_ref = _option(tokens, "credential-ref")
                sync_policy = _option(tokens, "sync-policy") or "headers_only"
                if not (display_name and host and port_text and username and credential_ref):
                    return CommandResult(
                        state,
                        "mail account create --display-name <name> --host <host> --port <port> --username <username_ref> --credential-ref <ref>",
                        handled=False,
                    )
                try:
                    port = int(port_text)
                except ValueError:
                    return CommandResult(state, "mail account create --port must be integer", handled=False)
                try:
                    account = create_imap_account(
                        repo_root=repo_root,
                        display_name=display_name,
                        host=host,
                        port=port,
                        username_ref=username,
                        credential_ref=credential_ref,
                        sync_policy=sync_policy,
                    )
                except ValueError as exc:
                    return CommandResult(state, f"mail account create failed: {exc}", handled=False)
                payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                        status_message=f"mail account created {account.get('account_id')}",
                    ),
                    json.dumps({"account": account, "payload": payload}, ensure_ascii=False),
                )
            if action == "use":
                if len(args) < 3:
                    return CommandResult(state, "mail account use <account-id>", handled=False)
                game["mail_selected_account_id"] = str(args[2]).strip()
                game.pop("mail_selected_mailbox", None)
                game["mail_list_offset"] = 0
                payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                        status_message=f"mail account {args[2]} selected",
                    ),
                    json.dumps(payload, ensure_ascii=False),
                )
            if action == "disable":
                if len(args) < 3:
                    return CommandResult(state, "mail account disable <account-id>", handled=False)
                try:
                    account = disable_imap_account(account_id=str(args[2]).strip(), repo_root=repo_root)
                except ValueError:
                    return CommandResult(state, "mail account disable failed: imap_account_not_found", handled=False)
                payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                        status_message=f"mail account disabled {account.get('account_id')}",
                    ),
                    json.dumps({"account": account, "payload": payload}, ensure_ascii=False),
                )
            if action == "delete":
                if len(args) < 3:
                    return CommandResult(state, "mail account delete <account-id>", handled=False)
                try:
                    account = delete_imap_account(account_id=str(args[2]).strip(), repo_root=repo_root)
                except ValueError:
                    return CommandResult(state, "mail account delete failed: imap_account_not_found", handled=False)
                if str(game.get("mail_selected_account_id") or "") == str(account.get("account_id") or ""):
                    game.pop("mail_selected_account_id", None)
                    game.pop("mail_selected_mailbox", None)
                payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                        status_message=f"mail account deleted {account.get('account_id')}",
                    ),
                    json.dumps({"deleted_account_id": account.get("account_id"), "payload": payload}, ensure_ascii=False),
                )
            return CommandResult(state, "mail account list|status|create|use|disable|delete", handled=False)

        if sub == "mailbox":
            if len(args) < 2:
                return CommandResult(state, "mail mailbox <name>", handled=False)
            game["mail_selected_mailbox"] = str(args[1]).strip()
            game["mail_list_offset"] = 0
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message=f"mail mailbox {args[1]} selected",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        if sub == "scroll":
            if len(args) < 2:
                return CommandResult(state, "mail scroll <delta>", handled=False)
            try:
                delta = int(str(args[1]).strip())
            except ValueError:
                return CommandResult(state, "mail scroll <delta>", handled=False)
            game["mail_list_offset"] = max(0, int(game.get("mail_list_offset") or 0) + delta)
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message=f"mail scroll offset={payload.get('list_offset')}",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        if sub == "filter":
            filters = dict(game.get("mail_filters") or {})
            for token in args[1:]:
                if "=" not in token:
                    continue
                key, value = str(token).split("=", 1)
                normalized_key = key.strip().lower()
                normalized_value = value.strip()
                if normalized_key == "unread":
                    filters["unread"] = normalized_value.lower() in {"1", "true", "yes", "on"}
                elif normalized_key in {"from", "subject", "mailbox", "to", "date_from", "date_to"}:
                    filters[normalized_key] = normalized_value
            game["mail_filters"] = filters
            game["mail_list_offset"] = 0
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message="mail filters updated",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        if sub == "open":
            if len(args) < 2:
                return CommandResult(state, "mail open <message-id|uid>", handled=False)
            target = str(args[1]).strip()
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
            selected_row = next(
                (
                    row
                    for row in rows
                    if _mail_message_key(row) == target or str(dict(row.get("message_ref") or {}).get("uid") or "") == target
                ),
                {},
            )
            if not selected_row:
                return CommandResult(state, "mail open failed: message not found", handled=False)
            game["mail_selected_message_key"] = _mail_message_key(selected_row)
            game["mail_detail_body_loaded"] = False
            game["mail_detail_body"] = ""
            game["mail_detail_redaction_status"] = "not_required"
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message=f"mail open {target}",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        if sub == "load-body":
            target = str(args[1]).strip() if len(args) > 1 else str(game.get("mail_selected_message_key") or "").strip()
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
            selected_row = next(
                (
                    row
                    for row in rows
                    if _mail_message_key(row) == target or str(dict(row.get("message_ref") or {}).get("uid") or "") == target
                ),
                {},
            )
            if not selected_row:
                return CommandResult(state, "mail load-body failed: message not found", handled=False)
            ref = dict(selected_row.get("message_ref") or {})
            store_row = _mail_store(repo_root).get_by_uid(
                account_id=str(ref.get("account_id") or ""),
                mailbox=str(ref.get("mailbox") or ""),
                uid=int(ref.get("uid") or 0),
            )
            body_text = str(dict(store_row or {}).get("body") or selected_row.get("body") or "")
            redacted = redact_mail_for_worker_context(body_text=body_text, attachments=list(selected_row.get("attachments") or []))
            game["mail_selected_message_key"] = _mail_message_key(selected_row)
            game["mail_detail_body_loaded"] = True
            game["mail_detail_body"] = str(redacted.get("redacted_body") or "")
            game["mail_detail_redaction_status"] = str(redacted.get("redaction_status") or "not_required")
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message=f"mail body loaded for {target}",
                ),
                json.dumps({"payload": payload, "redaction": redacted}, ensure_ascii=False),
            )
        if sub == "attachment":
            if len(args) < 2:
                return CommandResult(state, "mail attachment list|download|register ...", handled=False)
            action = str(args[1]).lower()
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            detail = dict(payload.get("selected_detail") or {})
            message_ref = dict(detail.get("message_ref") or {})
            attachments = [dict(item) for item in list(detail.get("attachments") or []) if isinstance(item, dict)]
            if action == "list":
                return CommandResult(
                    state.with_updates(status_message=f"mail attachments={len(attachments)}"),
                    json.dumps({"attachments": attachments, "message_ref": message_ref}, ensure_ascii=False),
                )
            if action == "download":
                if len(args) < 3:
                    return CommandResult(state, "mail attachment download <filename>", handled=False)
                filename = str(args[2]).strip()
                if not message_ref:
                    return CommandResult(state, "mail attachment download failed: no selected message", handled=False)
                target = next((row for row in attachments if str(row.get("filename") or "") == filename), {})
                if not target:
                    return CommandResult(state, "mail attachment download failed: attachment not found", handled=False)
                store_row = _mail_store(repo_root).get_by_uid(
                    account_id=str(message_ref.get("account_id") or ""),
                    mailbox=str(message_ref.get("mailbox") or ""),
                    uid=int(message_ref.get("uid") or 0),
                )
                raw_attachments = [dict(item) for item in list(dict(store_row or {}).get("attachments") or []) if isinstance(item, dict)]
                raw_target: dict[str, object] = {}
                indexed_meta = attachment_metadata(raw_attachments)
                for idx, meta in enumerate(indexed_meta):
                    if str(meta.get("filename") or "") == filename and idx < len(raw_attachments):
                        raw_target = dict(raw_attachments[idx])
                        break
                if not raw_target:
                    return CommandResult(state, "mail attachment download failed: content missing", handled=False)
                downloaded = download_attachment_securely(
                    attachment=raw_target,
                    target_dir=repo_root / "data" / "imap" / "downloads",
                )
                game["mail_attachment_last_download"] = downloaded
                payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                        status_message=f"mail attachment downloaded {filename}",
                    ),
                    json.dumps({"download": downloaded, "payload": payload}, ensure_ascii=False),
                )
            if action == "register":
                if len(args) < 3:
                    return CommandResult(state, "mail attachment register <filename>", handled=False)
                filename = str(args[2]).strip()
                if not message_ref:
                    return CommandResult(state, "mail attachment register failed: no selected message", handled=False)
                target = next((row for row in attachments if str(row.get("filename") or "") == filename), {})
                if not target:
                    return CommandResult(state, "mail attachment register failed: attachment not found", handled=False)
                artifact = register_mail_artifact(
                    message_ref=message_ref,
                    scope="attachment_ref",
                    redaction_status="not_required",
                    policy_decision_ref="policy:mail:attachment_ref",
                    excerpt=str(target.get("filename") or ""),
                    repo_root=repo_root,
                )
                game["mail_current_artifact_ref"] = str(artifact.get("artifact_ref") or "")
                payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                        status_message=f"mail attachment artifact registered {filename}",
                    ),
                    json.dumps({"artifact": artifact, "payload": payload}, ensure_ascii=False),
                )
            return CommandResult(state, "mail attachment list|download <filename>|register <filename>", handled=False)
        if sub == "export":
            if len(args) < 2 or str(args[1]).lower() != "current":
                return CommandResult(state, "mail export current --format json|text|eml [--include-body --confirm-body] [--goal <goal-id>]", handled=False)
            format_name = "json"
            include_body = False
            goal_id = ""
            for idx, token in enumerate(args[2:], start=2):
                lowered = str(token).lower()
                if lowered == "--format" and idx + 1 < len(args):
                    format_name = str(args[idx + 1]).strip()
                if lowered == "--include-body":
                    include_body = True
                if lowered == "--goal" and idx + 1 < len(args):
                    goal_id = str(args[idx + 1]).strip()
            if include_body and "--confirm-body" not in [str(item).lower() for item in args[2:]]:
                return CommandResult(state, "mail export with body requires --confirm-body", handled=False)
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            detail = dict(payload.get("selected_detail") or {})
            message_ref = dict(detail.get("message_ref") or {})
            if not message_ref:
                return CommandResult(state, "mail export failed: no selected message", handled=False)
            exported = export_mail_payload(
                message_ref=message_ref,
                header_meta=dict(detail.get("header_meta") or {}),
                body_text=str(detail.get("body_text") or ""),
                format_name=format_name,
                include_body=include_body,
                export_dir=repo_root / "data" / "imap" / "exports",
            )
            output_artifact = {}
            if goal_id:
                try:
                    output_artifact = GoalArtifactService().record_output_artifact(
                        goal_id=goal_id,
                        output_artifact={
                            "schema": "goal_output_artifact.v1",
                            "output_artifact_id": f"mail-export-{hashlib.sha1(str(exported.get('export_ref')).encode('utf-8')).hexdigest()[:12]}",
                            "goal_id": goal_id,
                            "artifact_type": "file",
                            "created_at": _now_iso(),
                            "artifact_ref": str(exported.get("export_ref") or ""),
                            "content_hash": str(exported.get("sha256") or ""),
                            "status": "created",
                            "provenance_summary": "mail export from operator_tui",
                            "provenance_kind": "manual",
                        },
                    )
                except GoalArtifactServiceError as exc:
                    return CommandResult(state, f"mail export goal artifact failed: {exc.reason_code}", handled=False)
            return CommandResult(
                state.with_updates(status_message=f"mail export {format_name}"),
                json.dumps({"export": exported, "goal_output_artifact": output_artifact}, ensure_ascii=False),
            )
        if sub == "snake-explain":
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            detail = dict(payload.get("selected_detail") or {})
            explain = explain_mail_for_snake_assist(
                opened=bool(detail.get("message_ref")),
                artifact_ref=str(payload.get("current_artifact_ref") or ""),
                message_ref=dict(detail.get("message_ref") or {}),
                body_text=str(detail.get("body_text") or ""),
            )
            if not bool(explain.get("ok")):
                return CommandResult(state, f"mail snake explain failed: {explain.get('reason_code')}", handled=False)
            return CommandResult(
                state.with_updates(status_message="mail snake explain ready"),
                json.dumps(explain, ensure_ascii=False),
            )
        if sub == "search":
            query = " ".join(args[1:]).strip()
            if not query:
                return CommandResult(state, "mail search <query>", handled=False)
            filters = dict(game.get("mail_filters") or {})
            filters.clear()
            for token in query.split():
                lowered = token.lower()
                if lowered.startswith("from:"):
                    filters["from"] = token.split(":", 1)[1]
                elif lowered.startswith("to:"):
                    filters["to"] = token.split(":", 1)[1]
                elif lowered.startswith("subject:"):
                    filters["subject"] = token.split(":", 1)[1]
                elif lowered.startswith("mailbox:"):
                    filters["mailbox"] = token.split(":", 1)[1]
                elif lowered.startswith("date:"):
                    value = token.split(":", 1)[1]
                    if ".." in value:
                        start, end = value.split("..", 1)
                        filters["date_from"] = start
                        filters["date_to"] = end
                elif lowered.startswith("unread:"):
                    value = token.split(":", 1)[1]
                    filters["unread"] = value.lower() in {"1", "true", "yes", "on"}
                else:
                    filters["subject"] = f"{filters.get('subject', '')} {token}".strip()
            game["mail_filters"] = filters
            game["mail_list_offset"] = 0
            game["mail_last_search_query"] = query
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            refs = []
            for row in list(payload.get("messages") or []):
                if not isinstance(row, dict):
                    continue
                ref = dict(row.get("message_ref") or {})
                refs.append(f"mail://{ref.get('account_id')}/{ref.get('mailbox')}/{ref.get('uid')}")
            game["mail_search_result_refs"] = refs
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message=f"mail search results={len(refs)}",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        if sub == "note":
            if len(args) < 3 or str(args[1]).lower() != "add":
                return CommandResult(state, "mail note add <text>", handled=False)
            text = " ".join(args[2:]).strip()
            if not text:
                return CommandResult(state, "mail note add <text>", handled=False)
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            selected = dict(payload.get("selected_detail") or {}).get("message_ref") or {}
            ref = dict(selected)
            note = {
                "message_ref": {
                    "account_id": str(ref.get("account_id") or ""),
                    "mailbox": str(ref.get("mailbox") or ""),
                    "uid": int(ref.get("uid") or 0),
                    "message_id": str(ref.get("message_id") or ""),
                },
                "note": text,
                "created_at": _now_iso(),
            }
            notes = [dict(item) for item in list(game.get("mail_notes") or []) if isinstance(item, dict)]
            notes.append(note)
            game["mail_notes"] = notes
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message="mail note added",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        if sub == "link-current-to-goal":
            if len(args) < 2:
                return CommandResult(state, "mail link-current-to-goal <goal-id>", handled=False)
            goal_id = str(args[1]).strip()
            if not goal_id:
                return CommandResult(state, "mail link-current-to-goal <goal-id>", handled=False)
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            selected = dict(payload.get("selected_detail") or {}).get("message_ref") or {}
            if not dict(selected):
                return CommandResult(state, "mail link failed: no selected message", handled=False)
            links = [str(item) for item in list(game.get("mail_linked_goal_refs") or []) if str(item).strip()]
            source_ref = f"mail://{selected.get('account_id')}/{selected.get('mailbox')}/{selected.get('uid')}"
            entry = f"{goal_id}:{source_ref}"
            if entry not in links:
                links.append(entry)
            game["mail_linked_goal_refs"] = links
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message=f"mail linked to goal {goal_id}",
                ),
                json.dumps(payload, ensure_ascii=False),
            )
        if sub == "artifact":
            if len(args) < 2:
                return CommandResult(state, "mail artifact register-current [--scope metadata_only|excerpt|full_body]", handled=False)
            action = str(args[1]).lower()
            if action != "register-current":
                return CommandResult(state, "mail artifact register-current [--scope metadata_only|excerpt|full_body]", handled=False)
            scope = "metadata_only"
            for idx, token in enumerate(args[2:], start=2):
                if str(token).lower() == "--scope" and idx + 1 < len(args):
                    requested = str(args[idx + 1]).strip().lower()
                    if requested == "body_excerpt":
                        requested = "excerpt"
                    scope = requested
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            detail = dict(payload.get("selected_detail") or {})
            message_ref = dict(detail.get("message_ref") or {})
            if not message_ref:
                return CommandResult(state, "mail artifact failed: no selected message", handled=False)
            excerpt = str(detail.get("body_text") or "")
            if scope == "full_body" and "--confirm-full-body" not in [str(item).lower() for item in args[2:]]:
                return CommandResult(state, "mail artifact full_body requires --confirm-full-body", handled=False)
            artifact = register_mail_artifact(
                message_ref=message_ref,
                scope=scope,
                redaction_status=str(detail.get("redaction_status") or "not_required"),
                policy_decision_ref=f"policy:mail:{scope}",
                excerpt=excerpt,
                repo_root=repo_root,
            )
            game["mail_current_artifact_ref"] = str(artifact.get("artifact_ref") or "")
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message=f"mail artifact registered {artifact.get('artifact_ref')}",
                ),
                json.dumps({"artifact": artifact, "payload": payload}, ensure_ascii=False),
            )
        if sub == "grant-current-to-goal":
            if len(args) < 2:
                return CommandResult(state, "mail grant-current-to-goal <goal-id> [--scope metadata_only|excerpt|full_body] [--confirm-full-body]", handled=False)
            goal_id = str(args[1]).strip()
            scope = "metadata_only"
            for idx, token in enumerate(args[2:], start=2):
                if str(token).lower() == "--scope" and idx + 1 < len(args):
                    requested = str(args[idx + 1]).strip().lower()
                    if requested == "body_excerpt":
                        requested = "excerpt"
                    scope = requested
            if scope == "full_body" and "--confirm-full-body" not in [str(item).lower() for item in args[2:]]:
                return CommandResult(state, "mail grant full_body requires --confirm-full-body", handled=False)
            payload = _build_mail_payload(game=game, repo_root=repo_root)
            detail = dict(payload.get("selected_detail") or {})
            message_ref = dict(detail.get("message_ref") or {})
            if not message_ref:
                return CommandResult(state, "mail grant failed: no selected message", handled=False)
            artifact = register_mail_artifact(
                message_ref=message_ref,
                scope=scope,
                redaction_status=str(detail.get("redaction_status") or "not_required"),
                policy_decision_ref=f"policy:mail:{scope}",
                excerpt=str(detail.get("body_text") or ""),
                repo_root=repo_root,
            )
            service = GoalArtifactService()
            artifact_ref = str(artifact.get("artifact_ref") or "")
            grant_id = f"grant-{hashlib.sha1(f'{goal_id}:{artifact_ref}:{scope}'.encode('utf-8')).hexdigest()[:10]}"
            grant_payload = {
                "schema": "source_artifact_grant.v1",
                "grant_id": grant_id,
                "goal_id": goal_id,
                "artifact_ref": artifact_ref,
                "granted_by": "operator_tui_mail",
                "granted_at": _now_iso(),
                "allowed_usages": sorted(set(["read", "use_as_context"])),
                "data_boundary": "project_private",
                "sensitivity": "internal",
                "policy_decision_ref": f"policy:mail:{scope}",
            }
            try:
                created = service.create_grant(goal_id=goal_id, grant=grant_payload)
            except GoalArtifactServiceError as exc:
                return CommandResult(state, f"mail grant failed: {exc.reason_code}", handled=False)
            game["mail_current_artifact_ref"] = artifact_ref
            payload = _build_mail_payload(game=game, repo_root=repo_root)
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
                    status_message=f"mail granted to goal {goal_id}",
                ),
                json.dumps({"grant": created, "artifact": artifact, "payload": payload}, ensure_ascii=False),
            )
        if sub == "revoke-grant":
            if len(args) < 3:
                return CommandResult(state, "mail revoke-grant <goal-id> <grant-id>", handled=False)
            goal_id = str(args[1]).strip()
            grant_id = str(args[2]).strip()
            try:
                revoked = GoalArtifactService().revoke_grant(goal_id=goal_id, grant_id=grant_id, revoke_reason="mail_revoke")
            except GoalArtifactServiceError as exc:
                return CommandResult(state, f"mail revoke failed: {exc.reason_code}", handled=False)
            return CommandResult(state.with_updates(status_message=f"mail grant revoked {grant_id}"), json.dumps(revoked, ensure_ascii=False))
        if sub == "context-envelope":
            if len(args) < 2:
                return CommandResult(state, "mail context-envelope <goal-id> [--target cloud_worker|local_worker]", handled=False)
            goal_id = str(args[1]).strip()
            target = "local_worker"
            for idx, token in enumerate(args[2:], start=2):
                if str(token).lower() == "--target" and idx + 1 < len(args):
                    target = str(args[idx + 1]).strip()
            envelope = build_mail_context_envelope(goal_id=goal_id, worker_target=target, repo_root=str(repo_root))
            return CommandResult(state.with_updates(status_message=f"mail context-envelope {goal_id} target={target}"), json.dumps(envelope, ensure_ascii=False))
        return CommandResult(
            state,
            "mail | mail account list|status|create|use|disable|delete | mail mailbox <name> | mail open <message-id|uid> | mail load-body [message-id|uid] | mail search <query> | mail filter key=value ... | mail note add <text> | mail link-current-to-goal <goal-id> | mail artifact register-current [--scope ...] | mail attachment list|download <filename>|register <filename> | mail export current --format json|text|eml [--include-body --confirm-body] [--goal <goal-id>] | mail grant-current-to-goal <goal-id> [--scope ...] [--confirm-full-body] | mail revoke-grant <goal-id> <grant-id> | mail context-envelope <goal-id> [--target ...] | mail snake-explain | mail scroll <delta>",
            handled=False,
        )
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
    if command == "plan":
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
        timeout_s = _resolve_chat_ask_timeout_seconds(game)
        game["tutor_ask_timeout_s"] = timeout_s
        game["tutor_ask_deadline_at"] = float(game["tutor_ask_at"]) + timeout_s
        game["tutor_ask_answered"] = False
        game["_ask_submitted"] = False
        game["active"] = True
        game["alive"] = True
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
            return CommandResult(
                state,
                "chat: room | ai | @<snake-id> | retry | backend list|use <id>|status | model list|use <id>",
                handled=False,
            )
        game = dict(state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel, add_direct_channel
        chat = get_chat_state(game)

        if sub == "backend":
            action = args[1].lower() if len(args) > 1 else "status"
            available = game.get("chat_backends_available")
            if not isinstance(available, list) or not available:
                available = ["ananta-worker", "opencode", "lmstudio", "hermes"]
            available_norm = [str(item).strip() for item in available if str(item).strip()]
            current = str(game.get("chat_backend") or "ananta-worker").strip()
            if action == "list":
                listed = ", ".join(available_norm)
                return CommandResult(
                    state.with_updates(status_message=f"chat backends: {listed}"),
                    "chat backends listed",
                )
            if action == "status":
                model = str(game.get("chat_backend_model") or "-").strip() or "-"
                return CommandResult(
                    state.with_updates(status_message=f"chat backend: {current} | model: {model}"),
                    "chat backend status",
                )
            if action == "use":
                target = str(args[2]).strip().lower() if len(args) > 2 else ""
                if not target:
                    return CommandResult(state, "chat backend use: backend-id erforderlich", handled=False)
                normalized = {item.lower(): item for item in available_norm}
                if target not in normalized:
                    return CommandResult(state, f"chat backend '{target}' nicht in Liste", handled=False)
                chosen = normalized[target]
                game["chat_backend"] = chosen
                message = f"chat backend aktiv: {chosen}"
                return CommandResult(
                    state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=message),
                    f"chat backend {chosen}",
                )
            return CommandResult(state, "chat backend: list | use <id> | status", handled=False)

        if sub == "model":
            action = args[1].lower() if len(args) > 1 else "list"
            models_raw = game.get("chat_backend_models")
            if isinstance(models_raw, list):
                models = [str(item).strip() for item in models_raw if str(item).strip()]
            else:
                models = []
            backend = str(game.get("chat_backend") or "ananta-worker").strip().lower()
            if action == "list" and backend in {"lmstudio", "local", "openai"}:
                models, _ = refresh_chat_backend_models(game, force=True)
            current_model = str(game.get("chat_backend_model") or "").strip()
            if current_model and current_model not in models:
                models.insert(0, current_model)
            if action == "list":
                if not models:
                    msg = "chat models: keine geladen (nutze :chat model use <id> oder setze ANANTA_TUI_CHAT_MODEL)"
                else:
                    msg = "chat models: " + ", ".join(models)
                return CommandResult(state.with_updates(header_logo_game=game, status_message=msg), "chat models listed")
            if action == "use":
                target_model = " ".join(args[2:]).strip() if len(args) > 2 else ""
                if not target_model:
                    return CommandResult(state, "chat model use: model-id erforderlich", handled=False)
                game["chat_backend_model"] = target_model
                if target_model not in models:
                    models.append(target_model)
                    game["chat_backend_models"] = models[-20:]
                return CommandResult(
                    state.with_updates(
                        header_logo_game=game,
                        mode=OperatorMode.NORMAL,
                        command_line="",
                        status_message=f"chat model aktiv: {target_model}",
                    ),
                    f"chat model {target_model}",
                )
            return CommandResult(state, "chat model: list | use <id>", handled=False)

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
