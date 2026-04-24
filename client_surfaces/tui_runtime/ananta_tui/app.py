from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Sequence

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.profile_auth import build_client_profile, contains_secret_key
from client_surfaces.common.types import ClientResponse
from client_surfaces.tui_runtime.ananta_tui.browser_fallback import build_browser_fallback_snapshot
from client_surfaces.tui_runtime.ananta_tui.fixture_transport import build_fixture_transport
from client_surfaces.tui_runtime.ananta_tui.state import TuiViewState
from client_surfaces.tui_runtime.ananta_tui.surface_map import TUI_SECTION_ORDER, build_hub_api_surface_map
from client_surfaces.tui_runtime.ananta_tui.views import (
    render_approval_repair_view,
    render_archived_tasks_view,
    render_artifact_explorer_view,
    render_audit_view,
    render_automation_view,
    render_config_and_provider_view,
    render_dashboard_view,
    render_goals_view,
    render_help_view,
    render_knowledge_view,
    render_navigation_shell,
    render_system_view,
    render_task_orchestration_view,
    render_task_workbench_view,
    render_teams_view,
    render_template_management_view,
)

_SAFE_CONFIG_PATHS = {
    "runtime_profile",
    "governance_mode",
    "goal_workflow_enabled",
    "persisted_plans_enabled",
    "feature_flags.goal_workflow_enabled",
    "feature_flags.persisted_plans_enabled",
}


def _safe_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _safe_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _empty_response(data: Any = None) -> ClientResponse:
    return ClientResponse(ok=True, status_code=200, state="healthy", data=data, error=None, retriable=False)


def _parse_scalar(raw_value: str) -> Any:
    text = raw_value.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered == "null":
        return None
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _assign_path(target: dict[str, Any], dotted_path: str, value: Any) -> None:
    cursor = target
    parts = [part for part in dotted_path.split(".") if part]
    for part in parts[:-1]:
        next_value = cursor.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            cursor[part] = next_value
        cursor = next_value
    if parts:
        cursor[parts[-1]] = value


def _flatten(payload: dict[str, Any], parent: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        full_key = f"{parent}.{key}" if parent else str(key)
        if isinstance(value, dict):
            out.update(_flatten(value, full_key))
        else:
            out[full_key] = value
    return out


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_safe_config_edits(raw_edits: Sequence[str]) -> tuple[dict[str, Any], list[str]]:
    patch: dict[str, Any] = {}
    errors: list[str] = []
    for entry in raw_edits:
        text = str(entry or "").strip()
        if not text or "=" not in text:
            errors.append(f"invalid_edit_format:{text}")
            continue
        key, raw_value = text.split("=", 1)
        dotted_key = key.strip()
        if dotted_key not in _SAFE_CONFIG_PATHS:
            errors.append(f"unsafe_key:{dotted_key}")
            continue
        if contains_secret_key(dotted_key):
            errors.append(f"secret_like_key_blocked:{dotted_key}")
            continue
        _assign_path(patch, dotted_key, _parse_scalar(raw_value))
    return patch, errors


def _render_api_map_summary() -> str:
    payload = build_hub_api_surface_map()
    classifications = Counter(
        item.get("classification")
        for methods in payload.get("by_section", {}).values()
        for item in methods
        if isinstance(item, dict)
    )
    lines = ["[API-MAP]"]
    lines.append(f"sections={len(payload.get('sections') or [])}")
    lines.append(f"methods={sum(classifications.values())}")
    lines.append(
        (
            f"class_tui_mvp={classifications.get('tui-mvp', 0)} "
            f"class_tui_advanced={classifications.get('tui-advanced', 0)} "
            f"class_browser_fallback={classifications.get('browser-fallback', 0)} "
            f"class_not_terminal={classifications.get('not-suitable-for-terminal', 0)}"
        )
    )
    return "\n".join(lines)


def _parse_json_object(raw: str, *, default: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
    text = str(raw or "").strip()
    if not text:
        return default or {}, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return default or {}, f"json_parse_error:{exc.msg}"
    if not isinstance(parsed, dict):
        return default or {}, "json_must_be_object"
    return parsed, None


@dataclass(frozen=True)
class ConfigEditRuntime:
    summary_line: str | None
    config_response: ClientResponse


class TuiRuntimeApp:
    def __init__(
        self,
        client: AnantaApiClient,
        *,
        section: str = "Dashboard",
        terminal_width: int = 120,
        selected_goal_id: str | None = None,
        selected_task_id: str | None = None,
        selected_artifact_id: str | None = None,
        selected_collection_id: str | None = None,
        selected_template_id: str | None = None,
        safe_config_edits: Sequence[str] = (),
        apply_safe_config: bool = False,
        task_status_filter: str | None = None,
        task_team_filter: str | None = None,
        task_agent_filter: str | None = None,
        task_error_only: bool = False,
        goal_create_text: str = "",
        goal_create_mode: str = "",
        goal_create_context_json: str = "",
        task_action: str = "",
        task_action_json: str = "",
        confirm_task_action: bool = False,
        archived_action: str = "",
        archived_action_json: str = "",
        selected_archived_task_id: str = "",
        confirm_archived_action: bool = False,
        artifact_action: str = "",
        artifact_action_json: str = "",
        confirm_artifact_action: bool = False,
        artifact_rag_preview_limit: int = 5,
        template_operation: str = "",
        template_payload_json: str = "",
        knowledge_search_query: str = "",
        knowledge_top_k: int = 5,
        index_selected_collection: bool = False,
        confirm_knowledge_index: bool = False,
    ) -> None:
        self._client = client
        self._state = (
            TuiViewState()
            .with_section(section)
            .with_terminal_width(int(terminal_width))
            .with_selection(
                goal_id=selected_goal_id,
                task_id=selected_task_id,
                artifact_id=selected_artifact_id,
                collection_id=selected_collection_id,
                template_id=selected_template_id,
            )
        )
        self._safe_config_edits = tuple(safe_config_edits)
        self._apply_safe_config = bool(apply_safe_config)
        self._task_status_filter = task_status_filter or None
        self._task_team_filter = task_team_filter or None
        self._task_agent_filter = task_agent_filter or None
        self._task_error_only = bool(task_error_only)
        self._goal_create_text = goal_create_text.strip()
        self._goal_create_mode = goal_create_mode.strip()
        self._goal_create_context_json = goal_create_context_json
        self._task_action = task_action.strip().lower()
        self._task_action_json = task_action_json
        self._confirm_task_action = bool(confirm_task_action)
        self._archived_action = archived_action.strip().lower()
        self._archived_action_json = archived_action_json
        self._selected_archived_task_id = selected_archived_task_id.strip()
        self._confirm_archived_action = bool(confirm_archived_action)
        self._artifact_action = artifact_action.strip().lower()
        self._artifact_action_json = artifact_action_json
        self._confirm_artifact_action = bool(confirm_artifact_action)
        self._artifact_rag_preview_limit = max(1, int(artifact_rag_preview_limit))
        self._template_operation = template_operation.strip().lower()
        self._template_payload_json = template_payload_json
        self._knowledge_search_query = knowledge_search_query.strip()
        self._knowledge_top_k = max(1, int(knowledge_top_k))
        self._index_selected_collection = bool(index_selected_collection)
        self._confirm_knowledge_index = bool(confirm_knowledge_index)
        self._api_map = build_hub_api_surface_map()

    def _apply_config_edits(self, config_response: ClientResponse) -> ConfigEditRuntime:
        if not self._safe_config_edits:
            return ConfigEditRuntime(summary_line=None, config_response=config_response)
        patch, errors = _parse_safe_config_edits(self._safe_config_edits)
        if errors:
            return ConfigEditRuntime(
                summary_line=f"[CONFIG-EDIT] rejected={'|'.join(errors)}",
                config_response=config_response,
            )
        current_payload = _safe_dict(config_response.data)
        if not current_payload:
            return ConfigEditRuntime(
                summary_line="[CONFIG-EDIT] skipped=config_unavailable_for_preview",
                config_response=config_response,
            )
        merged_payload = _merge_dict(current_payload, patch)
        before = _flatten(current_payload)
        after = _flatten(merged_payload)
        changed_keys = [key for key in sorted(after.keys()) if before.get(key) != after.get(key)]
        preview = ",".join(changed_keys[:8]) if changed_keys else "none"
        if not self._apply_safe_config:
            return ConfigEditRuntime(
                summary_line=f"[CONFIG-EDIT] preview_only changed_keys={preview}",
                config_response=config_response,
            )
        write_response = self._client.set_config(merged_payload)
        if write_response.ok:
            refreshed = self._client.get_config()
            return ConfigEditRuntime(
                summary_line=f"[CONFIG-EDIT] applied changed_keys={preview}",
                config_response=refreshed,
            )
        return ConfigEditRuntime(
            summary_line=f"[CONFIG-EDIT] apply_failed state={write_response.state}",
            config_response=config_response,
        )

    def _run_goal_create_action(self) -> str | None:
        if not self._goal_create_text:
            return None
        context_payload, parse_err = _parse_json_object(self._goal_create_context_json, default={})
        if parse_err:
            return f"[GOAL-CREATE] rejected={parse_err}"
        payload: dict[str, Any] = {"goal_text": self._goal_create_text, "context": context_payload}
        if self._goal_create_mode:
            payload["mode"] = self._goal_create_mode
        response = self._client.create_goal(payload)
        return (
            f"[GOAL-CREATE] state={response.state} status={response.status_code} "
            f"goal_id={_safe_dict(response.data).get('goal_id')} task_id={_safe_dict(response.data).get('task_id')}"
        )

    def _run_task_action(self, selected_task_id: str | None) -> str | None:
        if not self._task_action:
            return None
        if not selected_task_id:
            return "[TASK-ACTION] rejected=selected_task_required"
        payload, parse_err = _parse_json_object(self._task_action_json, default={})
        if parse_err:
            return f"[TASK-ACTION] rejected={parse_err}"
        if not self._confirm_task_action:
            return f"[TASK-ACTION] preview_only action={self._task_action} task_id={selected_task_id}"
        action_map = {
            "patch": lambda: self._client.patch_task(selected_task_id, payload),
            "assign": lambda: self._client.assign_task(selected_task_id, payload),
            "propose": lambda: self._client.propose_task_step(selected_task_id, payload),
            "execute": lambda: self._client.execute_task_step(selected_task_id, payload),
        }
        handler = action_map.get(self._task_action)
        if not handler:
            return f"[TASK-ACTION] rejected=unsupported_action:{self._task_action}"
        response = handler()
        return f"[TASK-ACTION] applied action={self._task_action} state={response.state} status={response.status_code}"

    def _run_archived_action(self) -> str | None:
        if not self._archived_action:
            return None
        payload, parse_err = _parse_json_object(self._archived_action_json, default={})
        if parse_err:
            return f"[ARCHIVED-ACTION] rejected={parse_err}"
        if not self._confirm_archived_action:
            return f"[ARCHIVED-ACTION] preview_only action={self._archived_action}"
        if self._archived_action == "restore":
            if not self._selected_archived_task_id:
                return "[ARCHIVED-ACTION] rejected=selected_archived_task_id_required"
            response = self._client.restore_archived_task(self._selected_archived_task_id)
        elif self._archived_action == "delete":
            if not self._selected_archived_task_id:
                return "[ARCHIVED-ACTION] rejected=selected_archived_task_id_required"
            response = self._client.delete_archived_task(self._selected_archived_task_id)
        elif self._archived_action == "cleanup":
            response = self._client.cleanup_archived_tasks(payload)
        else:
            return f"[ARCHIVED-ACTION] rejected=unsupported_action:{self._archived_action}"
        return (
            f"[ARCHIVED-ACTION] applied action={self._archived_action} "
            f"state={response.state} status={response.status_code}"
        )

    def _run_artifact_action(self, selected_artifact_id: str | None) -> str | None:
        if not self._artifact_action:
            return None
        if not selected_artifact_id:
            return "[ARTIFACT-ACTION] rejected=selected_artifact_required"
        payload, parse_err = _parse_json_object(self._artifact_action_json, default={})
        if parse_err:
            return f"[ARTIFACT-ACTION] rejected={parse_err}"
        if not self._confirm_artifact_action:
            return f"[ARTIFACT-ACTION] preview_only action={self._artifact_action} artifact_id={selected_artifact_id}"
        if self._artifact_action == "extract":
            response = self._client.extract_artifact(selected_artifact_id)
        elif self._artifact_action == "index":
            response = self._client.index_artifact(selected_artifact_id, payload)
        else:
            return f"[ARTIFACT-ACTION] rejected=unsupported_action:{self._artifact_action}"
        return (
            f"[ARTIFACT-ACTION] applied action={self._artifact_action} "
            f"state={response.state} status={response.status_code}"
        )

    def _run_knowledge_action(self, selected_collection_id: str | None) -> str | None:
        parts: list[str] = []
        if self._index_selected_collection:
            if not selected_collection_id:
                parts.append("index_rejected:selected_collection_required")
            elif not self._confirm_knowledge_index:
                parts.append("index_preview_only")
            else:
                response = self._client.index_knowledge_collection(selected_collection_id, payload={})
                parts.append(f"index_state={response.state}")
        if self._knowledge_search_query:
            if not selected_collection_id:
                parts.append("search_rejected:selected_collection_required")
            else:
                response = self._client.search_knowledge_collection(
                    selected_collection_id,
                    query=self._knowledge_search_query,
                    top_k=self._knowledge_top_k,
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

    def _run_template_operation(self) -> str | None:
        if not self._template_operation:
            return None
        payload, parse_err = _parse_json_object(self._template_payload_json, default={})
        if parse_err:
            return f"[TEMPLATE-OP] rejected={parse_err}"
        if self._template_operation == "validate":
            response = self._client.validate_template(payload)
        elif self._template_operation == "preview":
            response = self._client.preview_template(payload)
        elif self._template_operation == "diagnostics":
            response = self._client.template_validation_diagnostics(payload)
        else:
            return f"[TEMPLATE-OP] rejected=unsupported_operation:{self._template_operation}"
        return (
            f"[TEMPLATE-OP] operation={self._template_operation} state={response.state} status={response.status_code}"
        )

    def run_once(self) -> str:
        health = self._client.get_health()
        capabilities = self._client.get_capabilities()
        dashboard = self._client.get_dashboard_read_model()
        assistant = self._client.get_assistant_read_model()
        goals = self._client.list_goals()
        goal_modes = self._client.list_goal_modes()
        tasks = self._client.list_tasks()
        task_timeline = self._client.get_task_timeline(
            team_id=self._task_team_filter,
            agent=self._task_agent_filter,
            status=self._task_status_filter,
            error_only=self._task_error_only,
            limit=200,
        )
        orchestration = self._client.get_task_orchestration_read_model()
        archived_tasks = self._client.list_archived_tasks(limit=100, offset=0)
        artifacts = self._client.list_artifacts()
        knowledge_collections = self._client.list_knowledge_collections()
        knowledge_profiles = self._client.list_knowledge_index_profiles()
        templates = self._client.list_templates()
        template_variable_registry = self._client.get_template_variable_registry()
        template_sample_contexts = self._client.get_template_sample_contexts()
        config = self._client.get_config()
        providers = self._client.list_providers()
        provider_catalog = self._client.list_provider_catalog()
        benchmarks = self._client.get_llm_benchmarks(task_kind="analysis", top_n=5)
        benchmark_config = self._client.get_llm_benchmarks_config()
        contracts = self._client.get_system_contracts()
        agents = self._client.list_agents()
        stats = self._client.get_stats()
        stats_history = self._client.get_stats_history()
        teams = self._client.list_teams()
        autopilot = self._client.get_autopilot_status()
        auto_planner = self._client.get_auto_planner_status()
        triggers = self._client.get_triggers_status()
        audit_logs = self._client.get_audit_logs(limit=30)
        approvals = self._client.list_approvals()
        repairs = self._client.list_repairs()

        goal_ids = {str(item.get("id")) for item in _safe_items(goals.data) if item.get("id")}
        task_ids = {str(item.get("id")) for item in _safe_items(tasks.data) if item.get("id")}
        artifact_ids = {str(item.get("id")) for item in _safe_items(artifacts.data) if item.get("id")}
        collection_ids = {str(item.get("id")) for item in _safe_items(knowledge_collections.data) if item.get("id")}
        template_ids = {str(item.get("id")) for item in _safe_items(templates.data) if item.get("id")}
        state = self._state.sanitize_selection(
            goal_ids=goal_ids,
            task_ids=task_ids,
            artifact_ids=artifact_ids,
            collection_ids=collection_ids,
            template_ids=template_ids,
        ).mark_refresh()
        fallback_snapshot = build_browser_fallback_snapshot(self._client.profile.base_url, state)

        runtime_cfg = self._apply_config_edits(config)
        config = runtime_cfg.config_response

        create_goal_summary = self._run_goal_create_action()
        task_action_summary = self._run_task_action(state.selected_task_id)
        archived_action_summary = self._run_archived_action()
        artifact_action_summary = self._run_artifact_action(state.selected_artifact_id)
        knowledge_action_summary = self._run_knowledge_action(state.selected_collection_id)
        template_operation_summary = self._run_template_operation()

        goal_detail = (
            self._client.get_goal_detail(state.selected_goal_id) if state.selected_goal_id else _empty_response({})
        )
        goal_plan = (
            self._client.get_goal_plan(state.selected_goal_id) if state.selected_goal_id else _empty_response({})
        )
        goal_governance = (
            self._client.get_goal_governance_summary(state.selected_goal_id)
            if state.selected_goal_id
            else _empty_response({})
        )
        selected_task = self._client.get_task(state.selected_task_id) if state.selected_task_id else _empty_response({})
        task_logs = (
            self._client.get_task_logs(state.selected_task_id) if state.selected_task_id else _empty_response({})
        )
        artifact_detail = (
            self._client.get_artifact(state.selected_artifact_id) if state.selected_artifact_id else _empty_response({})
        )
        artifact_rag_status = (
            self._client.get_artifact_rag_status(state.selected_artifact_id)
            if state.selected_artifact_id
            else _empty_response({})
        )
        artifact_rag_preview = (
            self._client.get_artifact_rag_preview(state.selected_artifact_id, limit=self._artifact_rag_preview_limit)
            if state.selected_artifact_id
            else _empty_response({"items": []})
        )
        collection_detail = (
            self._client.get_knowledge_collection(state.selected_collection_id)
            if state.selected_collection_id
            else _empty_response({})
        )

        sections = list(self._api_map.get("sections") or list(TUI_SECTION_ORDER))
        capabilities_payload = _safe_dict(capabilities.data)
        capability_items = capabilities_payload.get("capabilities")
        capability_line = (
            f"[CAPABILITIES]\nitems={','.join(str(item) for item in capability_items)}"
            if isinstance(capability_items, list) and capability_items
            else f"[CAPABILITIES]\nstate={capabilities.state}"
        )

        return "\n\n".join(
            [
                render_navigation_shell(state, sections, _safe_dict(fallback_snapshot.get("links"))),
                _render_api_map_summary(),
                capability_line,
                render_dashboard_view(self._client.profile, dashboard, assistant, health),
                render_goals_view(
                    goals,
                    goal_modes,
                    goal_detail,
                    goal_plan,
                    goal_governance,
                    create_goal_summary,
                    state.selected_goal_id,
                ),
                render_task_workbench_view(tasks, task_timeline, selected_task, task_logs, task_action_summary),
                render_task_orchestration_view(orchestration),
                render_archived_tasks_view(archived_tasks, archived_action_summary),
                render_artifact_explorer_view(
                    artifacts,
                    artifact_detail,
                    artifact_rag_status,
                    artifact_rag_preview,
                    artifact_action_summary,
                ),
                render_knowledge_view(
                    knowledge_collections, knowledge_profiles, collection_detail, knowledge_action_summary
                ),
                render_template_management_view(
                    templates,
                    template_variable_registry,
                    template_sample_contexts,
                    template_operation_summary,
                    state.selected_template_id,
                ),
                render_config_and_provider_view(
                    config,
                    providers,
                    provider_catalog,
                    benchmarks,
                    benchmark_config,
                    runtime_cfg.summary_line,
                ),
                render_system_view(health, contracts, agents, stats, stats_history),
                render_teams_view(teams),
                render_automation_view(autopilot, auto_planner, triggers),
                render_audit_view(audit_logs),
                render_approval_repair_view(approvals, repairs),
                render_help_view(fallback_snapshot),
            ]
        )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Ananta TUI runtime parity shell.")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--profile-id", default="default")
    parser.add_argument("--auth-mode", default="session_token")
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--environment", default="local")
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON summary instead of text.")
    parser.add_argument("--section", default="Dashboard")
    parser.add_argument("--terminal-width", type=int, default=120)
    parser.add_argument("--selected-goal-id", default="")
    parser.add_argument("--selected-task-id", default="")
    parser.add_argument("--selected-artifact-id", default="")
    parser.add_argument("--selected-collection-id", default="")
    parser.add_argument("--selected-template-id", default="")
    parser.add_argument("--set-safe-config", action="append", default=[])
    parser.add_argument("--apply-safe-config", action="store_true")

    parser.add_argument("--goal-create-text", default="")
    parser.add_argument("--goal-create-mode", default="")
    parser.add_argument("--goal-create-context-json", default="")

    parser.add_argument("--task-status-filter", default="")
    parser.add_argument("--task-team-filter", default="")
    parser.add_argument("--task-agent-filter", default="")
    parser.add_argument("--task-error-only", action="store_true")
    parser.add_argument("--task-action", choices=["", "patch", "assign", "propose", "execute"], default="")
    parser.add_argument("--task-action-json", default="")
    parser.add_argument("--confirm-task-action", action="store_true")

    parser.add_argument("--archived-action", choices=["", "restore", "cleanup", "delete"], default="")
    parser.add_argument("--archived-action-json", default="")
    parser.add_argument("--selected-archived-task-id", default="")
    parser.add_argument("--confirm-archived-action", action="store_true")

    parser.add_argument("--artifact-action", choices=["", "extract", "index"], default="")
    parser.add_argument("--artifact-action-json", default="")
    parser.add_argument("--confirm-artifact-action", action="store_true")
    parser.add_argument("--artifact-rag-preview-limit", type=int, default=5)

    parser.add_argument("--knowledge-search-query", default="")
    parser.add_argument("--knowledge-top-k", type=int, default=5)
    parser.add_argument("--index-selected-collection", action="store_true")
    parser.add_argument("--confirm-knowledge-index", action="store_true")

    parser.add_argument("--template-operation", choices=["", "validate", "preview", "diagnostics"], default="")
    parser.add_argument("--template-payload-json", default="")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        profile = build_client_profile(
            {
                "profile_id": args.profile_id,
                "base_url": args.base_url,
                "auth_mode": args.auth_mode,
                "auth_token": args.auth_token,
                "environment": args.environment,
                "timeout_seconds": args.timeout_seconds,
            }
        )
    except ValueError as exc:
        print(f"[TUI-ERROR] invalid_profile: {exc}")
        return 2

    transport = build_fixture_transport() if args.fixture else None
    client = AnantaApiClient(profile, transport=transport)
    output = TuiRuntimeApp(
        client,
        section=args.section,
        terminal_width=args.terminal_width,
        selected_goal_id=args.selected_goal_id or None,
        selected_task_id=args.selected_task_id or None,
        selected_artifact_id=args.selected_artifact_id or None,
        selected_collection_id=args.selected_collection_id or None,
        selected_template_id=args.selected_template_id or None,
        safe_config_edits=tuple(args.set_safe_config),
        apply_safe_config=args.apply_safe_config,
        task_status_filter=args.task_status_filter or None,
        task_team_filter=args.task_team_filter or None,
        task_agent_filter=args.task_agent_filter or None,
        task_error_only=args.task_error_only,
        goal_create_text=args.goal_create_text,
        goal_create_mode=args.goal_create_mode,
        goal_create_context_json=args.goal_create_context_json,
        task_action=args.task_action,
        task_action_json=args.task_action_json,
        confirm_task_action=args.confirm_task_action,
        archived_action=args.archived_action,
        archived_action_json=args.archived_action_json,
        selected_archived_task_id=args.selected_archived_task_id,
        confirm_archived_action=args.confirm_archived_action,
        artifact_action=args.artifact_action,
        artifact_action_json=args.artifact_action_json,
        confirm_artifact_action=args.confirm_artifact_action,
        artifact_rag_preview_limit=args.artifact_rag_preview_limit,
        template_operation=args.template_operation,
        template_payload_json=args.template_payload_json,
        knowledge_search_query=args.knowledge_search_query,
        knowledge_top_k=args.knowledge_top_k,
        index_selected_collection=args.index_selected_collection,
        confirm_knowledge_index=args.confirm_knowledge_index,
    ).run_once()
    if args.json:
        print(
            json.dumps(
                {
                    "schema": "ananta_tui_runtime_output_v3",
                    "output": output,
                    "section_order": list(TUI_SECTION_ORDER),
                    "api_surface_map": build_hub_api_surface_map(),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
