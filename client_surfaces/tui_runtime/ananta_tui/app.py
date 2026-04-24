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
    render_audit_view,
    render_automation_view,
    render_config_and_provider_view,
    render_dashboard_view,
    render_goals_view,
    render_help_view,
    render_knowledge_view,
    render_navigation_shell,
    render_system_view,
    render_task_artifact_view,
    render_teams_view,
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
        safe_config_edits: Sequence[str] = (),
        apply_safe_config: bool = False,
    ) -> None:
        self._client = client
        self._state = (
            TuiViewState()
            .with_section(section)
            .with_terminal_width(int(terminal_width))
            .with_selection(goal_id=selected_goal_id, task_id=selected_task_id, artifact_id=selected_artifact_id)
        )
        self._safe_config_edits = tuple(safe_config_edits)
        self._apply_safe_config = bool(apply_safe_config)
        self._api_map = build_hub_api_surface_map()

    def _apply_config_edits(self, config_response: ClientResponse) -> ConfigEditRuntime:
        if not self._safe_config_edits:
            return ConfigEditRuntime(summary_line=None, config_response=config_response)
        patch, errors = _parse_safe_config_edits(self._safe_config_edits)
        if errors:
            return ConfigEditRuntime(
                summary_line=f"[CONFIG-EDIT] rejected={'|'.join(errors)}", config_response=config_response
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

    def run_once(self) -> str:
        health = self._client.get_health()
        capabilities = self._client.get_capabilities()
        dashboard = self._client.get_dashboard_read_model()
        assistant = self._client.get_assistant_read_model()
        goals = self._client.list_goals()
        goal_modes = self._client.list_goal_modes()
        tasks = self._client.list_tasks()
        artifacts = self._client.list_artifacts()
        knowledge_collections = self._client.list_knowledge_collections()
        knowledge_profiles = self._client.list_knowledge_index_profiles()
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

        runtime_cfg = self._apply_config_edits(config)
        config = runtime_cfg.config_response

        goal_ids = {str(item.get("id")) for item in _safe_items(goals.data) if item.get("id")}
        task_ids = {str(item.get("id")) for item in _safe_items(tasks.data) if item.get("id")}
        artifact_ids = {str(item.get("id")) for item in _safe_items(artifacts.data) if item.get("id")}

        state = self._state.sanitize_selection(
            goal_ids=goal_ids, task_ids=task_ids, artifact_ids=artifact_ids
        ).mark_refresh()
        fallback_snapshot = build_browser_fallback_snapshot(self._client.profile.base_url, state)

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
                render_goals_view(goals, goal_modes),
                render_task_artifact_view(tasks, artifacts),
                render_knowledge_view(knowledge_collections, knowledge_profiles),
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
    parser.add_argument("--set-safe-config", action="append", default=[])
    parser.add_argument("--apply-safe-config", action="store_true")
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
        safe_config_edits=tuple(args.set_safe_config),
        apply_safe_config=args.apply_safe_config,
    ).run_once()
    if args.json:
        print(
            json.dumps(
                {
                    "schema": "ananta_tui_runtime_output_v2",
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
