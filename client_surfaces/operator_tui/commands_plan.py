"""``:plan`` command handlers of the operator TUI: planning tracks and plan summaries.

Split out of ``commands_planning`` (which keeps ``:diff3``). ``handle_plan_command``
dispatches ``summary`` and ``track``; each subcommand is a small handler in
``_PLAN_SUMMARY_ACTIONS`` or ``_PLAN_TRACK_ACTIONS`` operating on a frozen
``_PlanTrackCommand``. Planning persistence and task integration stay in the
agent planning services.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.repository import goal_repo, task_repo
from agent.services.planning_summary_doctor_service import doctor_file, fix_file
from agent.services.planning_summary_engine import PlanningSummaryEngine
from agent.services.planning_track_pipeline_service import persist_planning_track_result
from agent.services.planning_track_planner_service import build_planner_context_envelope, render_track_planning_prompt
from agent.services.planning_track_task_integration_service import PlanningTrackTaskIntegrationService
from client_surfaces.operator_tui.models import CommandResult, OperatorState, PanelState


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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


PLAN_USAGE = "plan track [--from-goal <goal-id>] | plan summary doctor|fix|recompute"
_PLAN_TRACK_FILTER_KEYS = frozenset({"status", "priority", "risk", "type"})


def _artifacts_view_state(state: OperatorState, payload: dict, status_message: str, **updates) -> OperatorState:
    """``state`` switched to the artifacts section showing ``payload``."""
    section_payloads = dict(state.section_payloads or {})
    section_payloads["artifacts"] = payload
    panel_states = dict(state.panel_states or {})
    panel_states["artifacts"] = PanelState.HEALTHY
    return state.with_updates(
        **updates,
        section_id="artifacts",
        selected_index=0,
        section_payloads=section_payloads,
        panel_states=panel_states,
        status_message=status_message,
    )


# --- :plan summary ... --------------------------------------------------------------


def _plan_summary_doctor(args: list[str], state: OperatorState) -> CommandResult:
    if len(args) < 3:
        return CommandResult(state, "plan summary doctor <file>", handled=False)
    result = doctor_file(str(args[2]).strip())
    return CommandResult(
        state.with_updates(status_message=f"plan summary doctor {result.get('format')}"),
        json.dumps(result, ensure_ascii=False),
        handled=True,
    )


def _plan_summary_fix(args: list[str], state: OperatorState) -> CommandResult:
    if len(args) < 3:
        return CommandResult(state, "plan summary fix <file>", handled=False)
    result = fix_file(str(args[2]).strip(), write=True)
    payload = {k: v for k, v in result.items() if k != "payload"}
    return CommandResult(
        state.with_updates(status_message=f"plan summary fix changed={bool(result.get('changed'))}"),
        json.dumps(payload, ensure_ascii=False),
        handled=True,
    )


def _plan_summary_recompute(args: list[str], state: OperatorState) -> CommandResult:
    game = dict(state.header_logo_game or {})
    goal_id = str(game.get("active_goal_id") or "").strip()
    if not goal_id:
        return CommandResult(state, "plan summary recompute requires active goal", handled=False)
    output_id = str(
        game.get("planning_track_selected_output_id") or game.get("active_planning_track_output_id") or ""
    ).strip()
    if not output_id:
        return CommandResult(state, "plan summary recompute requires selected planning output", handled=False)
    try:
        recompute_result = _recompute_planning_track_output(goal_id=goal_id, output_artifact_id=output_id)
    except ValueError as exc:
        return CommandResult(state, f"plan summary recompute blocked reason={str(exc)}", handled=False)
    refreshed = _build_planning_track_payload(goal_id=goal_id, game=game)
    return CommandResult(
        _artifacts_view_state(state, refreshed, f"plan summary recompute {output_id}"),
        json.dumps({"output_artifact_id": output_id, **recompute_result, "payload": refreshed}, ensure_ascii=False),
        handled=True,
    )


_PLAN_SUMMARY_ACTIONS = {
    "doctor": _plan_summary_doctor,
    "fix": _plan_summary_fix,
    "recompute": _plan_summary_recompute,
}


def _plan_summary(args: list[str], state: OperatorState) -> CommandResult:
    handler = _PLAN_SUMMARY_ACTIONS.get(str(args[1]).lower() if len(args) > 1 else "")
    if handler is None:
        return CommandResult(state, "plan summary doctor <file> | fix <file> | recompute", handled=False)
    return handler(args, state)


# --- :plan track ... ----------------------------------------------------------------


@dataclass(frozen=True)
class _PlanTrackCommand:
    """One ``:plan track`` invocation for a resolved goal."""

    tail: list[str]
    state: OperatorState
    game: dict
    goal_id: str
    service: GoalArtifactService
    integration: PlanningTrackTaskIntegrationService

    def fail(self, message: str) -> CommandResult:
        return CommandResult(self.state, message, handled=False)

    def show_track(self, status_message: str) -> CommandResult:
        """Rebuild the planning track payload and open it in the artifacts section."""
        payload = _build_planning_track_payload(goal_id=self.goal_id, game=self.game)
        return CommandResult(
            _artifacts_view_state(self.state, payload, status_message, header_logo_game=self.game),
            json.dumps(payload, ensure_ascii=False),
        )


def _track_filter(cmd: _PlanTrackCommand) -> CommandResult:
    filters = dict(cmd.game.get("planning_track_filters") or {})
    for token in cmd.tail[1:]:
        text = str(token).strip()
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key.strip() in _PLAN_TRACK_FILTER_KEYS and value.strip():
            filters[key.strip()] = value.strip()
    cmd.game["planning_track_filters"] = filters
    return cmd.show_track(f"plan track filter {cmd.goal_id}")


def _track_clear_filter(cmd: _PlanTrackCommand) -> CommandResult:
    cmd.game["planning_track_filters"] = {}
    return cmd.show_track(f"plan track clear-filter {cmd.goal_id}")


def _short_sha1(text: str, length: int) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def _plan_adopt_provenance(goal_id: str, output_id: str, materialized: dict) -> dict:
    return {
        "schema": "execution_provenance.v1",
        "provenance_id": f"prov-{_short_sha1(f'{goal_id}:{output_id}:adopt', 16)}",
        "goal_id": goal_id,
        "task_id": f"plan-adopt:{output_id}",
        "execution_id": f"exec-{_short_sha1(f'{goal_id}:{output_id}:adopt:exec', 14)}",
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
    }


def _track_adopt(cmd: _PlanTrackCommand) -> CommandResult:
    if len(cmd.tail) < 2:
        return cmd.fail("plan track adopt <output-artifact-id>")
    output_id = str(cmd.tail[1]).strip()
    try:
        materialized = cmd.integration.adopt_track(goal_id=cmd.goal_id, output_artifact_id=output_id)
    except ValueError as exc:
        return cmd.fail(f"plan track adopt blocked output={output_id} reason={str(exc)}")
    cmd.game["active_planning_track_output_id"] = output_id
    cmd.game["planning_track_selected_output_id"] = output_id
    cmd.service.upsert_execution_provenance(
        goal_id=cmd.goal_id, provenance=_plan_adopt_provenance(cmd.goal_id, output_id, materialized)
    )
    return cmd.show_track(f"plan track adopted {output_id}")


def _track_reject(cmd: _PlanTrackCommand) -> CommandResult:
    if len(cmd.tail) < 2:
        return cmd.fail("plan track reject <output-artifact-id>")
    output_id = str(cmd.tail[1]).strip()
    rejected_result = cmd.integration.reject_track(goal_id=cmd.goal_id, output_artifact_id=output_id)
    cmd.game["rejected_planning_track_output_ids"] = list(rejected_result.get("rejected_output_ids") or [])
    if str(cmd.game.get("active_planning_track_output_id") or "") == output_id:
        cmd.game["active_planning_track_output_id"] = ""
    return cmd.show_track(f"plan track rejected {output_id}")


def _track_row(rows: list, output_id: str):
    return next((item for item in rows if str(item.get("output_artifact_id") or "") == output_id), None)


def _track_diff(cmd: _PlanTrackCommand) -> CommandResult:
    if len(cmd.tail) < 3:
        return cmd.fail("plan track diff <left-output-id> <right-output-id>")
    left_id = str(cmd.tail[1]).strip()
    right_id = str(cmd.tail[2]).strip()
    rows = list(_build_planning_track_payload(goal_id=cmd.goal_id, game=cmd.game).get("track_rows") or [])
    left_row = _track_row(rows, left_id)
    right_row = _track_row(rows, right_id)
    if not isinstance(left_row, dict) or not isinstance(right_row, dict):
        return cmd.fail("plan track diff requires two existing planning track outputs")
    diff = _build_plan_task_diff(dict(left_row.get("payload") or {}), dict(right_row.get("payload") or {}))
    cmd.game["planning_track_diff"] = {"left_output_id": left_id, "right_output_id": right_id, **diff}
    return cmd.show_track(f"plan track diff {left_id}->{right_id}")


def _adopted_output_id(cmd: _PlanTrackCommand) -> str:
    return str(cmd.game.get("active_planning_track_output_id") or "").strip()


def _track_execute_next(cmd: _PlanTrackCommand) -> CommandResult:
    output_id = _adopted_output_id(cmd)
    if not output_id:
        return cmd.fail("plan track execute-next requires an adopted output")
    try:
        execution = cmd.integration.execute_next_plan_task(
            goal_id=cmd.goal_id,
            output_artifact_id=output_id,
            worker_id="operator_tui",
        )
    except ValueError as exc:
        return cmd.fail(f"plan track execute-next blocked reason={str(exc)}")
    cmd.game["planning_track_last_execution"] = execution
    return cmd.show_track(
        f"plan track execute-next {execution.get('plan_task_id')}->{execution.get('internal_task_id')}"
    )


def _track_sync_status(cmd: _PlanTrackCommand) -> CommandResult:
    if len(cmd.tail) < 3:
        return cmd.fail("plan track sync-status <plan-task-id> <todo|in_progress|blocked|completed|failed>")
    output_id = _adopted_output_id(cmd)
    if not output_id:
        return cmd.fail("plan track sync-status requires an adopted output")
    plan_task_id = str(cmd.tail[1]).strip()
    internal_status = str(cmd.tail[2]).strip().lower()
    try:
        cmd.integration.sync_plan_status_from_internal_task(
            goal_id=cmd.goal_id,
            output_artifact_id=output_id,
            plan_task_id=plan_task_id,
            internal_status=internal_status,
        )
    except ValueError as exc:
        return cmd.fail(f"plan track sync-status blocked reason={str(exc)}")
    return cmd.show_track(f"plan track sync-status {plan_task_id}={internal_status}")


def _granted_source_refs(graph: dict) -> list[str]:
    source_grants = [dict(item) for item in list(graph.get("source_grants") or []) if isinstance(item, dict)]
    return [
        str(item.get("artifact_ref") or "").strip()
        for item in source_grants
        if str(item.get("artifact_ref") or "").strip()
    ]


def _record_track_generation(game: dict, result: dict) -> None:
    lifecycle = ["pending", "validating"]
    if int(result.get("repair_attempt_count") or 0) > 0:
        lifecycle.append("repaired")
    lifecycle.append(str(result.get("status") or "failed"))
    game["planning_track_status"] = str(result.get("status") or "failed")
    game["planning_track_lifecycle"] = lifecycle
    game["planning_track_status_hint"] = f"source_usage_refs={len(list(result.get('source_usage_refs') or []))}"
    game["planning_track_status_issues"] = list(result.get("issues") or [])
    selected_output_id = str(dict(result.get("output_artifact") or {}).get("output_artifact_id") or "")
    if selected_output_id:
        game["planning_track_selected_output_id"] = selected_output_id


def _issue_label(result: dict) -> str:
    issue_preview = list(result.get("issues") or [])[:3]
    if not issue_preview:
        return "none"
    return "; ".join(
        f"{item.get('path')}:{item.get('reason_code')}" for item in issue_preview if isinstance(item, dict)
    )


def _track_generate(cmd: _PlanTrackCommand) -> CommandResult:
    """Default: run planner track generation (mock worker path in tests/dev)."""
    goal_id, game = cmd.goal_id, cmd.game
    source_refs = _granted_source_refs(cmd.service.get_goal_graph(goal_id))
    available_artifacts = [{"source_ref": ref} for ref in source_refs]
    context_envelope = build_planner_context_envelope(
        goal_id=goal_id,
        goal_text=f"Goal {goal_id}",
        constraints=[],
        available_artifacts=available_artifacts,
        allowed_source_refs=list(source_refs),
        codecompass_refs=[],
    )
    final_prompt = render_track_planning_prompt(goal_text=f"Goal {goal_id}", context_envelope=context_envelope)
    raw_output = str(game.get("planner_mock_output") or "").strip()
    if not raw_output:
        raw_output = json.dumps(_build_mock_planning_track_payload(goal_id), ensure_ascii=False)
    game["planning_track_status"] = "pending"
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
        goal_artifact_service=cmd.service,
    )
    _record_track_generation(game, result)
    return cmd.show_track(f"plan track {goal_id} {result.get('status')} issues={_issue_label(result)}")


_PLAN_TRACK_ACTIONS = {
    "filter": _track_filter,
    "clear-filter": _track_clear_filter,
    "adopt": _track_adopt,
    "reject": _track_reject,
    "diff": _track_diff,
    "execute-next": _track_execute_next,
    "sync-status": _track_sync_status,
}


def _explicit_goal_id(tail: list[str]) -> str:
    lowered = [str(item).lower() for item in tail]
    if "--from-goal" not in lowered:
        return ""
    idx = lowered.index("--from-goal")
    return str(tail[idx + 1]).strip() if idx + 1 < len(tail) else ""


def _plan_track(args: list[str], state: OperatorState) -> CommandResult:
    game = dict(state.header_logo_game or {})
    tail = list(args[1:])
    explicit_goal_id = _explicit_goal_id(tail)
    goal_id = explicit_goal_id or str(game.get("active_goal_id") or "").strip()
    if not goal_id:
        return CommandResult(state, "plan track requires active goal or --from-goal <goal-id>", handled=False)
    if explicit_goal_id:
        game["active_goal_id"] = goal_id
    sub = str(tail[0]).lower() if tail and not str(tail[0]).startswith("--") else ""
    service = GoalArtifactService()
    cmd = _PlanTrackCommand(
        tail=tail,
        state=state,
        game=game,
        goal_id=goal_id,
        service=service,
        integration=PlanningTrackTaskIntegrationService(goal_artifact_service=service),
    )
    return _PLAN_TRACK_ACTIONS.get(sub, _track_generate)(cmd)


def handle_plan_command(args: list[str], state: OperatorState) -> CommandResult:
    """Dispatch :plan subcommands."""
    action = str(args[0]).lower() if args else ""
    if action == "summary":
        return _plan_summary(args, state)
    if action == "track":
        return _plan_track(args, state)
    return CommandResult(state, PLAN_USAGE, handled=False)
