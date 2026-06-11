from __future__ import annotations

import json
from time import sleep
from typing import Any

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.types import ClientResponse
from client_surfaces.tui_runtime.ananta_tui.state import TuiViewState

from .app_widgets import _empty_response, _parse_json_object, _safe_dict, _safe_items


def _run_goal_create_action(
    client: AnantaApiClient,
    goal_create_text: str,
    goal_create_mode: str,
    goal_create_context_json: str,
) -> str | None:
    if not goal_create_text:
        return None
    context_payload, parse_err = _parse_json_object(goal_create_context_json, default={})
    if parse_err:
        return f"[GOAL-CREATE] rejected={parse_err}"
    payload: dict[str, Any] = {"goal_text": goal_create_text, "context": context_payload}
    if goal_create_mode:
        payload["mode"] = goal_create_mode
    response = client.create_goal(payload)
    return (
        f"[GOAL-CREATE] state={response.state} status={response.status_code} "
        f"goal_id={_safe_dict(response.data).get('goal_id')} task_id={_safe_dict(response.data).get('task_id')}"
    )


def _run_task_action(
    client: AnantaApiClient,
    task_action: str,
    task_action_json: str,
    confirm_task_action: bool,
    selected_task_id: str | None,
) -> str | None:
    if not task_action:
        return None
    if not selected_task_id:
        return "[TASK-ACTION] rejected=selected_task_required"
    payload, parse_err = _parse_json_object(task_action_json, default={})
    if parse_err:
        return f"[TASK-ACTION] rejected={parse_err}"
    if not confirm_task_action:
        return f"[TASK-ACTION] preview_only action={task_action} task_id={selected_task_id}"
    action_map = {
        "patch": lambda: client.patch_task(selected_task_id, payload),
        "assign": lambda: client.assign_task(selected_task_id, payload),
        "propose": lambda: client.propose_task_step(selected_task_id, payload),
        "execute": lambda: client.execute_task_step(selected_task_id, payload),
    }
    handler = action_map.get(task_action)
    if not handler:
        return f"[TASK-ACTION] rejected=unsupported_action:{task_action}"
    response = handler()
    return f"[TASK-ACTION] applied action={task_action} state={response.state} status={response.status_code}"


def _run_archived_action(
    client: AnantaApiClient,
    archived_action: str,
    archived_action_json: str,
    confirm_archived_action: bool,
    selected_archived_task_id: str,
) -> str | None:
    if not archived_action:
        return None
    payload, parse_err = _parse_json_object(archived_action_json, default={})
    if parse_err:
        return f"[ARCHIVED-ACTION] rejected={parse_err}"
    if not confirm_archived_action:
        return f"[ARCHIVED-ACTION] preview_only action={archived_action}"
    if archived_action == "restore":
        if not selected_archived_task_id:
            return "[ARCHIVED-ACTION] rejected=selected_archived_task_id_required"
        response = client.restore_archived_task(selected_archived_task_id)
    elif archived_action == "delete":
        if not selected_archived_task_id:
            return "[ARCHIVED-ACTION] rejected=selected_archived_task_id_required"
        response = client.delete_archived_task(selected_archived_task_id)
    elif archived_action == "cleanup":
        response = client.cleanup_archived_tasks(payload)
    else:
        return f"[ARCHIVED-ACTION] rejected=unsupported_action:{archived_action}"
    return (
        f"[ARCHIVED-ACTION] applied action={archived_action} "
        f"state={response.state} status={response.status_code}"
    )


def _run_artifact_action(
    client: AnantaApiClient,
    artifact_action: str,
    artifact_action_json: str,
    confirm_artifact_action: bool,
    selected_artifact_id: str | None,
) -> str | None:
    if not artifact_action:
        return None
    if not selected_artifact_id:
        return "[ARTIFACT-ACTION] rejected=selected_artifact_required"
    payload, parse_err = _parse_json_object(artifact_action_json, default={})
    if parse_err:
        return f"[ARTIFACT-ACTION] rejected={parse_err}"
    if not confirm_artifact_action:
        return f"[ARTIFACT-ACTION] preview_only action={artifact_action} artifact_id={selected_artifact_id}"
    if artifact_action == "extract":
        response = client.extract_artifact(selected_artifact_id)
    elif artifact_action == "index":
        response = client.index_artifact(selected_artifact_id, payload)
    else:
        return f"[ARTIFACT-ACTION] rejected=unsupported_action:{artifact_action}"
    return (
        f"[ARTIFACT-ACTION] applied action={artifact_action} "
        f"state={response.state} status={response.status_code}"
    )


def _run_knowledge_action(
    client: AnantaApiClient,
    index_selected_collection: bool,
    confirm_knowledge_index: bool,
    knowledge_search_query: str,
    knowledge_top_k: int,
    selected_collection_id: str | None,
) -> str | None:
    parts: list[str] = []
    if index_selected_collection:
        if not selected_collection_id:
            parts.append("index_rejected:selected_collection_required")
        elif not confirm_knowledge_index:
            parts.append("index_preview_only")
        else:
            response = client.index_knowledge_collection(selected_collection_id, payload={})
            parts.append(f"index_state={response.state}")
    if knowledge_search_query:
        if not selected_collection_id:
            parts.append("search_rejected:selected_collection_required")
        else:
            response = client.search_knowledge_collection(
                selected_collection_id,
                query=knowledge_search_query,
                top_k=knowledge_top_k,
            )
            items = _safe_items(response.data)
            parts.append(f"search_state={response.state}")
            parts.append(f"search_hits={len(items)}")
            if items:
                top = items[0]
                parts.append(
                    (
                        f"top_hit={top.get('source')} "
                        f"score={top.get('score')} "
                        f"snippet={str(top.get('snippet') or '')[:80]}"
                    )
                )
    if not parts:
        return None
    return "[KNOWLEDGE-ACTION] " + " ".join(parts)


def _run_template_operation(
    client: AnantaApiClient,
    template_operation: str,
    template_payload_json: str,
) -> str | None:
    if not template_operation:
        return None
    payload, parse_err = _parse_json_object(template_payload_json, default={})
    if parse_err:
        return f"[TEMPLATE-OP] rejected={parse_err}"
    if template_operation == "validate":
        response = client.validate_template(payload)
    elif template_operation == "preview":
        response = client.preview_template(payload)
    elif template_operation == "diagnostics":
        response = client.template_validation_diagnostics(payload)
    else:
        return f"[TEMPLATE-OP] rejected=unsupported_operation:{template_operation}"
    return (
        f"[TEMPLATE-OP] operation={template_operation} state={response.state} status={response.status_code}"
    )


def _run_team_action(
    client: AnantaApiClient,
    team_action: str,
    team_action_json: str,
    confirm_team_action: bool,
    selected_team_id: str | None,
) -> str | None:
    if not team_action:
        return None
    payload, parse_err = _parse_json_object(team_action_json, default={})
    if parse_err:
        return f"[TEAM-ACTION] rejected={parse_err}"
    if team_action != "activate":
        return f"[TEAM-ACTION] rejected=unsupported_action:{team_action}"
    if not selected_team_id:
        return "[TEAM-ACTION] rejected=selected_team_required"
    if not confirm_team_action:
        return f"[TEAM-ACTION] preview_only action=activate team_id={selected_team_id}"
    response = client.activate_team(selected_team_id)
    return (
        f"[TEAM-ACTION] applied action=activate team_id={selected_team_id} "
        f"state={response.state} status={response.status_code}"
    )


def _run_instruction_action(
    client: AnantaApiClient,
    instruction_action: str,
    instruction_action_json: str,
    confirm_instruction_action: bool,
    state: TuiViewState,
) -> str | None:
    if not instruction_action:
        return None
    payload, parse_err = _parse_json_object(instruction_action_json, default={})
    if parse_err:
        return f"[INSTRUCTION-ACTION] rejected={parse_err}"
    if not confirm_instruction_action:
        return f"[INSTRUCTION-ACTION] preview_only action={instruction_action}"

    if instruction_action == "select_profile":
        if not state.selected_instruction_profile_id:
            return "[INSTRUCTION-ACTION] rejected=selected_instruction_profile_required"
        response = client.select_instruction_profile(state.selected_instruction_profile_id)
    elif instruction_action == "select_overlay":
        if not state.selected_instruction_overlay_id:
            return "[INSTRUCTION-ACTION] rejected=selected_instruction_overlay_required"
        response = client.select_instruction_overlay(state.selected_instruction_overlay_id, payload=payload)
    elif instruction_action == "link_overlay":
        if not state.selected_instruction_overlay_id:
            return "[INSTRUCTION-ACTION] rejected=selected_instruction_overlay_required"
        response = client.link_instruction_overlay(state.selected_instruction_overlay_id, payload=payload)
    elif instruction_action == "unlink_overlay":
        if not state.selected_instruction_overlay_id:
            return "[INSTRUCTION-ACTION] rejected=selected_instruction_overlay_required"
        response = client.unlink_instruction_overlay(state.selected_instruction_overlay_id)
    elif instruction_action == "set_goal_selection":
        if not state.selected_goal_id:
            return "[INSTRUCTION-ACTION] rejected=selected_goal_required"
        response = client.set_goal_instruction_selection(state.selected_goal_id, payload=payload)
    elif instruction_action == "set_task_selection":
        if not state.selected_task_id:
            return "[INSTRUCTION-ACTION] rejected=selected_task_required"
        response = client.set_task_instruction_selection(state.selected_task_id, payload=payload)
    else:
        return f"[INSTRUCTION-ACTION] rejected=unsupported_action:{instruction_action}"
    return (
        f"[INSTRUCTION-ACTION] applied action={instruction_action} "
        f"state={response.state} status={response.status_code}"
    )


def _run_automation_action(
    client: AnantaApiClient,
    automation_action: str,
    automation_action_json: str,
    confirm_automation_action: bool,
) -> str | None:
    if not automation_action:
        return None
    payload, parse_err = _parse_json_object(automation_action_json, default={})
    if parse_err:
        return f"[AUTOMATION-ACTION] rejected={parse_err}"
    if not confirm_automation_action:
        return f"[AUTOMATION-ACTION] preview_only action={automation_action}"

    if automation_action == "autopilot_start":
        response = client.start_autopilot(payload)
    elif automation_action == "autopilot_stop":
        response = client.stop_autopilot()
    elif automation_action == "autopilot_tick":
        response = client.tick_autopilot()
    elif automation_action == "configure_planner":
        response = client.configure_auto_planner(payload)
    elif automation_action == "configure_triggers":
        response = client.configure_triggers(payload)
    else:
        return f"[AUTOMATION-ACTION] rejected=unsupported_action:{automation_action}"
    return (
        f"[AUTOMATION-ACTION] applied action={automation_action} "
        f"state={response.state} status={response.status_code}"
    )


def _run_approval_action(
    client: AnantaApiClient,
    approval_action: str,
    approval_action_json: str,
    confirm_approval_action: bool,
    selected_task_id: str | None,
) -> str | None:
    if not approval_action:
        return None
    payload, parse_err = _parse_json_object(approval_action_json, default={})
    if parse_err:
        return f"[APPROVAL-ACTION] rejected={parse_err}"
    task_id = str(payload.get("task_id") or selected_task_id or "").strip()
    if not task_id:
        return "[APPROVAL-ACTION] rejected=selected_task_required"
    if not confirm_approval_action:
        return f"[APPROVAL-ACTION] preview_only action={approval_action} task_id={task_id}"

    if approval_action not in {"approve", "reject"}:
        return f"[APPROVAL-ACTION] rejected=unsupported_action:{approval_action}"

    task_response = client.get_task(task_id)
    detail = _safe_dict(task_response.data)
    proposal_state = str(detail.get("proposal_state") or "").strip().lower()
    if proposal_state in {"approved", "rejected", "already_handled"}:
        return f"[APPROVAL-ACTION] skipped=already_handled task_id={task_id}"
    if proposal_state in {"denied", "policy_denied", "blocked"}:
        return f"[APPROVAL-ACTION] skipped=denied_or_blocked task_id={task_id}"
    if proposal_state in {"stale", "expired"}:
        return f"[APPROVAL-ACTION] skipped=stale task_id={task_id}"

    response = client.review_task_proposal(
        task_id,
        action=approval_action,
        comment=str(payload.get("comment") or "").strip() or None,
    )
    return (
        f"[APPROVAL-ACTION] applied action={approval_action} task_id={task_id} "
        f"state={response.state} status={response.status_code}"
    )


def _run_repair_action(
    client: AnantaApiClient,
    repair_action: str,
    repair_action_json: str,
    confirm_repair_action: bool,
    selected_repair_session_id: str | None,
) -> str | None:
    if not repair_action:
        return None
    payload, parse_err = _parse_json_object(repair_action_json, default={})
    if parse_err:
        return f"[REPAIR-ACTION] rejected={parse_err}"
    session_id = str(payload.get("session_id") or selected_repair_session_id or "").strip()
    if not session_id:
        return "[REPAIR-ACTION] rejected=selected_repair_session_required"
    if not confirm_repair_action:
        return f"[REPAIR-ACTION] preview_only action={repair_action} session_id={session_id}"
    if repair_action not in {"dry_run", "execute", "verify"}:
        return f"[REPAIR-ACTION] rejected=unsupported_action:{repair_action}"

    if bool(payload.get("unsafe")):
        return f"[REPAIR-ACTION] blocked=unsafe_payload session_id={session_id}"
    return (
        f"[REPAIR-ACTION] blocked=browser_fallback_required action={repair_action} "
        f"session_id={session_id}"
    )


def _render_live_refresh_block(
    client: AnantaApiClient,
    state: TuiViewState,
    live_refresh_cycles: int,
    live_refresh_interval_seconds: float,
    live_refresh_target: str,
) -> str | None:
    if live_refresh_cycles <= 1 or live_refresh_target not in {
        "system",
        "task_logs",
        "system_task_logs",
    }:
        return None
    include_system = live_refresh_target in {"system", "system_task_logs"}
    include_task_logs = live_refresh_target in {"task_logs", "system_task_logs"}
    lines = ["[LIVE-REFRESH]"]
    lines.append(
        (
            f"target={live_refresh_target} cycles={live_refresh_cycles} "
            f"interval_seconds={live_refresh_interval_seconds}"
        )
    )
    for cycle in range(1, live_refresh_cycles + 1):
        health = client.get_health()
        lines.append(f"cycle={cycle}/{live_refresh_cycles} health={health.state}")
        if include_system:
            stats = client.get_stats()
            stats_payload = _safe_dict(stats.data)
            lines.append(
                (
                    f"system_state={stats.state} queue_depth={stats_payload.get('queue_depth')} "
                    f"tasks_in_progress={stats_payload.get('tasks_in_progress')}"
                )
            )
        if include_task_logs:
            if state.selected_task_id:
                task_logs = client.get_task_logs(state.selected_task_id)
                log_items = _safe_items(task_logs.data)
                latest = log_items[-1] if log_items else {}
                lines.append(
                    (
                        f"task_logs_state={task_logs.state} task_id={state.selected_task_id} "
                        f"latest={latest.get('line')}"
                    )
                )
            else:
                lines.append("task_logs_skipped=selected_task_required")
        if cycle < live_refresh_cycles:
            sleep(live_refresh_interval_seconds)
    lines.append("live_refresh_stoppable=limit_cycles_or_ctrl_c")
    return "\n".join(lines)
