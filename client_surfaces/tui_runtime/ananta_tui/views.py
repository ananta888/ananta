from __future__ import annotations

from typing import Any

from client_surfaces.common.profile_auth import contains_secret_key, redact_sensitive_text
from client_surfaces.common.types import ClientProfile, ClientResponse
from client_surfaces.tui_runtime.ananta_tui.state import TuiViewState


def _safe_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _safe_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _coerce_scalar(value: Any) -> str:
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    if isinstance(value, str):
        return value
    return repr(value)


def _flatten_config(payload: dict[str, Any], parent: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        path = f"{parent}.{key}" if parent else str(key)
        if isinstance(value, dict):
            flat.update(_flatten_config(value, path))
        else:
            flat[path] = value
    return flat


def render_navigation_shell(state: TuiViewState, sections: list[str], fallback_links: dict[str, Any]) -> str:
    lines = ["[NAVIGATION]"]
    if state.compact_mode:
        compact = " | ".join(
            (f"[{section[0]}]" if section == state.current_section else section[0]) for section in sections
        )
        lines.append(f"mode=compact current={state.current_section} sections={compact}")
    else:
        marks = " | ".join((f"*{section}*" if section == state.current_section else section) for section in sections)
        lines.append(f"mode=full current={state.current_section}")
        lines.append(f"sections={marks}")
    lines.append(
        (
            f"selected_goal={state.selected_goal_id or '-'} "
            f"selected_task={state.selected_task_id or '-'} "
            f"selected_artifact={state.selected_artifact_id or '-'}"
        )
    )
    lines.append(f"refresh_count={state.refresh_count}")
    selected_task_link = _safe_dict(fallback_links).get("selected_task")
    if selected_task_link:
        lines.append(f"selected_task_browser_link={selected_task_link}")
    return "\n".join(lines)


def render_dashboard_view(
    profile: ClientProfile,
    dashboard: ClientResponse,
    assistant: ClientResponse,
    health: ClientResponse,
) -> str:
    lines = ["[DASHBOARD]"]
    lines.append(f"profile={profile.profile_id} endpoint={profile.base_url}")
    dashboard_payload = _safe_dict(dashboard.data)
    assistant_payload = _safe_dict(assistant.data)
    lines.append(f"dashboard_state={dashboard.state} assistant_state={assistant.state}")
    lines.append(f"health_state={health.state} health_status={health.status_code}")
    if dashboard_payload:
        lines.append(
            (
                f"active_profile={dashboard_payload.get('active_profile')} "
                f"governance_mode={dashboard_payload.get('governance_mode')}"
            )
        )
        warnings = dashboard_payload.get("warnings") or []
        if isinstance(warnings, list) and warnings:
            lines.append(f"warnings={', '.join(str(item) for item in warnings[:5])}")
    if assistant_payload:
        lines.append(f"assistant_mode={assistant_payload.get('active_mode')}")
    if dashboard.state != "healthy":
        lines.append(f"dashboard_degraded={dashboard.error or dashboard.state}")
    lines.append("[HEALTH]")
    lines.append(f"state={health.state} status={health.status_code}")
    return "\n".join(lines)


def render_goals_view(goals: ClientResponse, goal_modes: ClientResponse) -> str:
    lines = ["[GOALS]"]
    goal_items = _safe_items(goals.data)
    if goal_items:
        for goal in goal_items[:10]:
            lines.append(f"- {goal.get('id')} [{goal.get('status')}] {goal.get('title')}")
    else:
        lines.append("- no_goals_available")
    modes = _safe_items(goal_modes.data)
    if not modes and isinstance(goal_modes.data, dict):
        modes = _safe_items(_safe_dict(goal_modes.data).get("items"))
    if modes:
        mode_ids = [str(mode.get("id") or mode.get("name")) for mode in modes[:10]]
        lines.append(f"goal_modes={','.join(mode_ids)}")
    if goals.state != "healthy":
        lines.append(f"goals_degraded={goals.state}")
    return "\n".join(lines)


def render_task_artifact_view(tasks: ClientResponse, artifacts: ClientResponse) -> str:
    lines = ["[TASKS]"]
    task_items = _safe_items(tasks.data)
    if task_items:
        for task in task_items[:10]:
            lines.append(f"- {task.get('id')} [{task.get('status')}] {task.get('title')}")
    else:
        lines.append("- no_tasks_available")
    lines.append("[ARTIFACTS]")
    artifact_items = _safe_items(artifacts.data)
    if artifact_items:
        for artifact in artifact_items[:10]:
            lines.append(f"- {artifact.get('id')} ({artifact.get('type')}) {artifact.get('title')}")
    else:
        lines.append("- no_artifacts_available")
    if tasks.state != "healthy":
        lines.append(f"tasks_degraded={tasks.state}")
    if artifacts.state != "healthy":
        lines.append(f"artifacts_degraded={artifacts.state}")
    return "\n".join(lines)


def render_knowledge_view(collections: ClientResponse, index_profiles: ClientResponse) -> str:
    lines = ["[KNOWLEDGE]"]
    collection_items = _safe_items(collections.data)
    if collection_items:
        for item in collection_items[:10]:
            lines.append(f"- {item.get('id')} name={item.get('name')} docs={item.get('documents')}")
    else:
        lines.append("- no_collections_available")
    profile_items = _safe_items(index_profiles.data)
    if profile_items:
        profile_ids = [str(item.get("id") or item.get("name")) for item in profile_items[:10]]
        lines.append(f"index_profiles={','.join(profile_ids)}")
    if collections.state != "healthy":
        lines.append(f"knowledge_degraded={collections.state}")
    return "\n".join(lines)


def render_config_and_provider_view(
    config: ClientResponse,
    providers: ClientResponse,
    provider_catalog: ClientResponse,
    benchmarks: ClientResponse,
    benchmark_config: ClientResponse,
    config_edit_summary: str | None,
) -> str:
    lines = ["[CONFIG]"]
    config_payload = _safe_dict(config.data)
    if config_payload:
        flattened = _flatten_config(config_payload)
        visible_lines = 0
        for key in sorted(flattened.keys()):
            value = flattened[key]
            display = "***" if contains_secret_key(key) else redact_sensitive_text(_coerce_scalar(value))
            lines.append(f"- {key}={display}")
            visible_lines += 1
            if visible_lines >= 12:
                lines.append("- ...")
                break
    else:
        lines.append("- config_not_available")
    lines.append(f"config_state={config.state}")
    if config.state == "policy_denied":
        lines.append("config_permission=denied")
    if config_edit_summary:
        lines.append(config_edit_summary)

    lines.append("[PROVIDERS]")
    provider_items = _safe_items(providers.data)
    if provider_items:
        for item in provider_items[:10]:
            lines.append(f"- {item.get('id')} provider={item.get('provider')} model={item.get('model')}")
    else:
        lines.append("- no_providers_available")
    catalog_payload = _safe_dict(provider_catalog.data)
    if catalog_payload:
        lines.append(f"provider_catalog_keys={','.join(sorted(str(k) for k in catalog_payload.keys())[:8])}")

    lines.append("[BENCHMARKS]")
    benchmark_items = _safe_items(benchmarks.data)
    if benchmark_items:
        for item in benchmark_items[:5]:
            lines.append(
                (
                    f"- {item.get('provider')}/{item.get('model')} "
                    f"task_kind={item.get('task_kind')} score={item.get('score')}"
                )
            )
    else:
        lines.append("- no_benchmark_entries")
    benchmark_cfg = _safe_dict(benchmark_config.data)
    if benchmark_cfg:
        lines.append(f"benchmark_enabled={benchmark_cfg.get('enabled')}")
    return "\n".join(lines)


def render_system_view(
    health: ClientResponse,
    contracts: ClientResponse,
    agents: ClientResponse,
    stats: ClientResponse,
    stats_history: ClientResponse,
) -> str:
    lines = ["[SYSTEM]"]
    lines.append(f"health={health.state}")
    contract_payload = _safe_dict(contracts.data)
    if contract_payload:
        lines.append(
            (
                f"contracts_version={contract_payload.get('contracts_version')} "
                f"compatibility={contract_payload.get('compatibility')}"
            )
        )
    else:
        lines.append(f"contracts_state={contracts.state}")
    agent_items = _safe_items(agents.data)
    if agent_items:
        lines.append(f"agents_count={len(agent_items)}")
        lines.append("agents=" + ",".join(str(item.get("id") or item.get("agent_id")) for item in agent_items[:5]))
    else:
        lines.append("agents_count=0")
    stats_payload = _safe_dict(stats.data)
    if stats_payload:
        lines.append(
            (
                f"queue_depth={stats_payload.get('queue_depth')} "
                f"tasks_in_progress={stats_payload.get('tasks_in_progress')}"
            )
        )
    history_items = _safe_items(stats_history.data)
    lines.append(f"stats_history_points={len(history_items)}")
    degraded = [
        name
        for name, resp in (
            ("contracts", contracts),
            ("agents", agents),
            ("stats", stats),
            ("stats_history", stats_history),
        )
        if resp.state != "healthy"
    ]
    if degraded:
        lines.append(f"degraded_sources={','.join(degraded)}")
    return "\n".join(lines)


def render_teams_view(teams: ClientResponse) -> str:
    lines = ["[TEAMS]"]
    team_items = _safe_items(teams.data)
    if team_items:
        for item in team_items[:10]:
            lines.append(f"- {item.get('id')} name={item.get('name')} mode={item.get('mode')}")
    else:
        lines.append("- no_teams_available")
    if teams.state != "healthy":
        lines.append(f"teams_degraded={teams.state}")
    return "\n".join(lines)


def render_automation_view(
    autopilot: ClientResponse,
    auto_planner: ClientResponse,
    triggers: ClientResponse,
) -> str:
    lines = ["[AUTOMATION]"]
    autopilot_payload = _safe_dict(autopilot.data)
    planner_payload = _safe_dict(auto_planner.data)
    trigger_payload = _safe_dict(triggers.data)
    lines.append(
        (
            f"autopilot_running={autopilot_payload.get('running')} "
            f"security_level={autopilot_payload.get('security_level')}"
        )
    )
    lines.append(f"auto_planner_enabled={planner_payload.get('enabled')}")
    lines.append(f"triggers_enabled={trigger_payload.get('enabled')}")
    if autopilot.state != "healthy":
        lines.append(f"autopilot_degraded={autopilot.state}")
    return "\n".join(lines)


def render_audit_view(audit_logs: ClientResponse) -> str:
    lines = ["[AUDIT]"]
    items = _safe_items(audit_logs.data)
    if items:
        for item in items[:10]:
            lines.append(f"- {item.get('id')} kind={item.get('kind')} target={item.get('target_id')}")
    else:
        lines.append("- no_audit_entries")
    if audit_logs.state != "healthy":
        lines.append(f"audit_degraded={audit_logs.state}")
    return "\n".join(lines)


def render_approval_repair_view(approvals: ClientResponse, repairs: ClientResponse) -> str:
    lines = ["[APPROVALS]"]
    approval_items = _safe_items(approvals.data)
    if approval_items:
        for item in approval_items[:10]:
            lines.append(f"- {item.get('id')} scope={item.get('scope')} state={item.get('state')}")
    else:
        lines.append("- no_approval_items")
    lines.append("[REPAIR]")
    lines.append("[REPAIRS]")
    repair_items = _safe_items(repairs.data)
    if repair_items:
        for repair in repair_items[:10]:
            lines.append(
                (
                    f"- {repair.get('session_id')} diagnosis={repair.get('diagnosis')} "
                    f"verification={repair.get('verification_result')} outcome={repair.get('outcome')}"
                )
            )
    else:
        lines.append("- no_repair_sessions")
    lines.append("note=view_only_no_implicit_execution")
    return "\n".join(lines)


def render_help_view(fallback_snapshot: dict[str, Any]) -> str:
    lines = ["[HELP]"]
    lines.append("shortcuts=up/down select_section | enter open_detail | r refresh | b open_browser_link")
    lines.append("dangerous_actions=explicit_confirmation_and_backend_validation")
    browser_first = list(_safe_dict(fallback_snapshot).get("browser_first_operations") or [])
    if browser_first:
        lines.append(f"browser_first={','.join(str(item) for item in browser_first)}")
    links = _safe_dict(fallback_snapshot).get("links")
    if isinstance(links, dict):
        for key in ("selected_goal", "selected_task", "selected_artifact", "config", "audit"):
            value = links.get(key)
            if value:
                lines.append(f"{key}_browser_link={value}")
    return "\n".join(lines)
