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


def _render_named_items(items: list[dict[str, Any]], *, key: str = "id", label: str = "name", limit: int = 5) -> str:
    values = [f"{item.get(key)}:{item.get(label)}" for item in items[:limit]]
    return ",".join(str(value) for value in values if value and value != "None:None")


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
            f"selected_artifact={state.selected_artifact_id or '-'} "
            f"selected_collection={state.selected_collection_id or '-'} "
            f"selected_template={state.selected_template_id or '-'} "
            f"selected_team={state.selected_team_id or '-'} "
            f"selected_blueprint={state.selected_blueprint_id or '-'} "
            f"selected_profile={state.selected_instruction_profile_id or '-'} "
            f"selected_overlay={state.selected_instruction_overlay_id or '-'}"
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


def render_goals_view(
    goals: ClientResponse,
    goal_modes: ClientResponse,
    goal_detail: ClientResponse,
    goal_plan: ClientResponse,
    goal_governance: ClientResponse,
    create_goal_summary: str | None,
    selected_goal_id: str | None,
) -> str:
    lines = ["[GOALS]"]
    for goal in _safe_items(goals.data)[:10]:
        lines.append(
            (
                f"- {goal.get('id')} [{goal.get('status')}] {goal.get('title')} "
                f"team={goal.get('team')} mode={goal.get('mode')} summary={goal.get('summary')}"
            )
        )
    if len(lines) == 1:
        lines.append("- no_goals_available")
    modes = _safe_items(goal_modes.data)
    if modes:
        mode_ids = [str(mode.get("id") or mode.get("name")) for mode in modes[:10]]
        lines.append(f"goal_modes={','.join(mode_ids)}")
    if create_goal_summary:
        lines.append(create_goal_summary)

    lines.append("[GOAL-DETAIL]")
    if selected_goal_id and goal_detail.ok:
        detail = _safe_dict(goal_detail.data)
        lines.append(
            (
                f"id={detail.get('id')} trace_ref={detail.get('trace_ref')} "
                f"related_tasks={detail.get('related_task_ids')} related_artifacts={detail.get('related_artifact_ids')}"
            )
        )
    elif selected_goal_id:
        lines.append(f"selected_goal={selected_goal_id} detail_state={goal_detail.state}")
    else:
        lines.append("selected_goal=none")

    lines.append("[GOAL-PLAN]")
    plan_payload = _safe_dict(goal_plan.data)
    nodes = list(plan_payload.get("nodes") or [])
    if nodes:
        for node in nodes[:20]:
            lines.append(
                (
                    f"- {node.get('id')} [{node.get('status')}] "
                    f"{node.get('title')} depends_on={node.get('depends_on') or []}"
                )
            )
    elif selected_goal_id:
        lines.append("plan_state=raw_or_missing browser_first_patch=true")
    else:
        lines.append("plan_state=not_selected")
    lines.append("plan_patch_strategy=browser_first")

    lines.append("[GOAL-GOVERNANCE]")
    governance = _safe_dict(goal_governance.data)
    if governance:
        lines.append(
            (
                f"mode={governance.get('governance_mode')} "
                f"risk_level={governance.get('risk_level')} policy_state={governance.get('policy_state')}"
            )
        )
    elif selected_goal_id:
        lines.append(f"governance_state={goal_governance.state}")
    else:
        lines.append("governance_state=not_selected")
    if goals.state != "healthy":
        lines.append(f"goals_degraded={goals.state}")
    return "\n".join(lines)


def render_task_workbench_view(
    tasks: ClientResponse,
    task_timeline: ClientResponse,
    selected_task: ClientResponse,
    task_logs: ClientResponse,
    task_action_summary: str | None,
) -> str:
    lines = ["[TASK-WORKBENCH]"]
    for task in _safe_items(tasks.data)[:10]:
        lines.append(
            (
                f"- {task.get('id')} [{task.get('status')}] {task.get('title')} "
                f"team={task.get('team_id')} agent={task.get('agent')} execution={task.get('execution_state')}"
            )
        )
    if len(lines) == 1:
        lines.append("- no_tasks_available")
    if task_action_summary:
        lines.append(task_action_summary)

    lines.append("[TASK-DETAIL]")
    detail = _safe_dict(selected_task.data)
    if detail:
        lines.append(
            (
                f"id={detail.get('id')} owner={detail.get('owner')} agent={detail.get('agent')} "
                f"proposal={detail.get('proposal_state')} execution={detail.get('execution_state')} "
                f"artifacts={detail.get('artifact_ids')}"
            )
        )
    else:
        lines.append("selected_task=none_or_missing")

    lines.append("[TASK-TIMELINE]")
    timeline_items = _safe_items(task_timeline.data)
    if timeline_items:
        for item in timeline_items[:10]:
            lines.append(
                (
                    f"- {item.get('event_id')} task={item.get('task_id')} "
                    f"status={item.get('status')} agent={item.get('agent')}"
                )
            )
    else:
        lines.append("timeline_empty_or_unavailable")

    lines.append("[TASK-LOGS]")
    log_items = _safe_items(task_logs.data)
    if log_items:
        for item in log_items[:10]:
            lines.append(f"- {item.get('ts')} {item.get('line')}")
    else:
        lines.append("logs_unavailable_or_not_selected")
    lines.append("task_actions_confirmation=required")
    return "\n".join(lines)


def render_task_orchestration_view(orchestration: ClientResponse) -> str:
    lines = ["[TASK-ORCHESTRATION]"]
    payload = _safe_dict(orchestration.data)
    lines.append(f"state={payload.get('state') or orchestration.state}")
    queues = _safe_dict(payload.get("queues"))
    for queue_name in ("normal", "blocked", "failed", "stale"):
        entries = queues.get(queue_name)
        lines.append(f"{queue_name}_count={len(entries) if isinstance(entries, list) else 0}")
    lines.append("orchestration_write_mode=read_only_guarded")
    return "\n".join(lines)


def render_archived_tasks_view(
    archived_tasks: ClientResponse,
    archived_action_summary: str | None,
) -> str:
    lines = ["[ARCHIVED-TASKS]"]
    for task in _safe_items(archived_tasks.data)[:10]:
        lines.append(f"- {task.get('id')} archived_at={task.get('archived_at')} title={task.get('title')}")
    if len(lines) == 1:
        lines.append("- no_archived_tasks")
    if archived_action_summary:
        lines.append(archived_action_summary)
    lines.append("archived_actions_confirmation=required")
    lines.append("bulk_cleanup_strategy=impact_summary_then_confirm_or_browser_fallback")
    return "\n".join(lines)


def render_artifact_explorer_view(
    artifacts: ClientResponse,
    artifact_detail: ClientResponse,
    rag_status: ClientResponse,
    rag_preview: ClientResponse,
    artifact_action_summary: str | None,
) -> str:
    lines = ["[ARTIFACT-EXPLORER]"]
    for artifact in _safe_items(artifacts.data)[:10]:
        lines.append(
            (
                f"- {artifact.get('id')} type={artifact.get('type')} "
                f"title={artifact.get('title')} task={artifact.get('task_id')}"
            )
        )
    if len(lines) == 1:
        lines.append("- no_artifacts_available")
    detail = _safe_dict(artifact_detail.data)
    if detail:
        lines.append(
            (
                f"selected_artifact={detail.get('id')} size={detail.get('size_bytes')} "
                f"type={detail.get('type')} preview={str(detail.get('preview') or '')[:120]}"
            )
        )
    status_payload = _safe_dict(rag_status.data)
    if status_payload:
        lines.append((f"rag_indexed={status_payload.get('indexed')} rag_chunks={status_payload.get('chunks')}"))
    preview_items = _safe_items(rag_preview.data)
    if preview_items:
        preview_item = preview_items[0]
        lines.append(
            (
                f"rag_preview_top=chunk:{preview_item.get('chunk_id')} "
                f"score:{preview_item.get('score')} text:{str(preview_item.get('text') or '')[:120]}"
            )
        )
    if artifact_action_summary:
        lines.append(artifact_action_summary)
    lines.append("artifact_binary_strategy=browser_fallback")
    lines.append("artifact_upload_strategy=deferred_browser_fallback")
    return "\n".join(lines)


def render_knowledge_view(
    collections: ClientResponse,
    index_profiles: ClientResponse,
    collection_detail: ClientResponse,
    knowledge_action_summary: str | None,
) -> str:
    lines = ["[KNOWLEDGE]"]
    for item in _safe_items(collections.data)[:10]:
        lines.append(f"- {item.get('id')} name={item.get('name')} docs={item.get('documents')}")
    if len(lines) == 1:
        lines.append("- no_collections_available")
    profile_items = _safe_items(index_profiles.data)
    if profile_items:
        profile_ids = [str(item.get("id") or item.get("name")) for item in profile_items[:10]]
        lines.append(f"index_profiles={','.join(profile_ids)}")
    detail = _safe_dict(collection_detail.data)
    if detail:
        lines.append(
            (
                f"selected_collection={detail.get('id')} name={detail.get('name')} "
                f"documents={detail.get('documents')} last_indexed={detail.get('last_indexed_at')}"
            )
        )
    if knowledge_action_summary:
        lines.append(knowledge_action_summary)
    if collections.state != "healthy":
        lines.append(f"knowledge_degraded={collections.state}")
    return "\n".join(lines)


def render_template_management_view(
    templates: ClientResponse,
    variable_registry: ClientResponse,
    sample_contexts: ClientResponse,
    template_operation_summary: str | None,
    selected_template_id: str | None,
) -> str:
    lines = ["[TEMPLATES]"]
    template_items = _safe_items(templates.data)
    for item in template_items[:10]:
        lines.append(
            f"- {item.get('id')} name={item.get('name')} kind={item.get('kind')} version={item.get('version')}"
        )
    if len(lines) == 1:
        lines.append("- no_templates_available")
    if selected_template_id:
        selected = next((item for item in template_items if str(item.get("id")) == selected_template_id), None)
        if selected:
            lines.append(f"selected_template={selected.get('id')} detail_name={selected.get('name')}")
        else:
            lines.append(f"selected_template={selected_template_id} detail_state=missing")
    registry_payload = _safe_dict(variable_registry.data)
    vars_payload = registry_payload.get("variables")
    if isinstance(vars_payload, list):
        lines.append(f"template_variable_registry_count={len(vars_payload)}")
    samples_payload = _safe_dict(sample_contexts.data).get("samples")
    if isinstance(samples_payload, list):
        lines.append(f"template_sample_contexts_count={len(samples_payload)}")
    if template_operation_summary:
        lines.append(template_operation_summary)
    lines.append("template_write_strategy=browser_first_or_explicit_guarded")
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


def render_team_blueprint_view(
    teams: ClientResponse,
    blueprints: ClientResponse,
    blueprint_catalog: ClientResponse,
    blueprint_detail: ClientResponse,
    team_types: ClientResponse,
    team_roles: ClientResponse,
    roles_for_type: ClientResponse,
    team_action_summary: str | None,
) -> str:
    lines = [render_teams_view(teams), "[BLUEPRINTS]"]
    blueprint_items = _safe_items(blueprints.data)
    if blueprint_items:
        for item in blueprint_items[:10]:
            lines.append(
                (
                    f"- {item.get('id')} name={item.get('name')} "
                    f"team_type={item.get('team_type_id')} version={item.get('version')}"
                )
            )
    else:
        lines.append("- no_blueprints_available")
    catalog_items = _safe_items(blueprint_catalog.data)
    if catalog_items:
        lines.append(f"catalogs={_render_named_items(catalog_items)}")
    detail = _safe_dict(blueprint_detail.data)
    if detail:
        lines.append(
            (
                f"selected_blueprint={detail.get('id')} "
                f"team_type={detail.get('team_type_id')} roles={detail.get('roles')}"
            )
        )
    type_items = _safe_items(team_types.data)
    role_items = _safe_items(team_roles.data)
    typed_role_items = _safe_items(roles_for_type.data)
    if type_items:
        lines.append(f"team_types={_render_named_items(type_items)}")
    if role_items:
        lines.append(f"team_roles={_render_named_items(role_items)}")
    if typed_role_items:
        lines.append(f"roles_for_selected_type={_render_named_items(typed_role_items)}")
    if team_action_summary:
        lines.append(team_action_summary)
    lines.append("team_activation_confirmation=required")
    return "\n".join(lines)


def render_instruction_layers_view(
    layer_model: ClientResponse,
    effective_layers: ClientResponse,
    profiles: ClientResponse,
    overlays: ClientResponse,
    instruction_action_summary: str | None,
) -> str:
    lines = ["[INSTRUCTION-LAYERS]"]
    model = _safe_dict(layer_model.data)
    layers = _safe_items(model.get("layers"))
    if layers:
        for layer in layers[:10]:
            lines.append(
                f"- layer={layer.get('id')} kind={layer.get('kind')} overridable={layer.get('overridable')}"
            )
    else:
        lines.append(f"model_state={layer_model.state}")
    effective = _safe_dict(effective_layers.data)
    effective_stack = _safe_items(effective.get("effective_stack"))
    if effective_stack:
        lines.append("[INSTRUCTION-EFFECTIVE]")
        for item in effective_stack[:10]:
            lines.append(f"- layer={item.get('layer')} source={item.get('source')}")
    non_overridable = effective.get("non_overridable_layers")
    if isinstance(non_overridable, list):
        lines.append(f"non_overridable_layers={','.join(str(item) for item in non_overridable)}")

    lines.append("[INSTRUCTION-PROFILES]")
    profile_items = _safe_items(profiles.data)
    if profile_items:
        for item in profile_items[:10]:
            lines.append(f"- {item.get('id')} name={item.get('name')} owner={item.get('owner_username')}")
    else:
        lines.append("- no_instruction_profiles")

    lines.append("[INSTRUCTION-OVERLAYS]")
    overlay_items = _safe_items(overlays.data)
    if overlay_items:
        for item in overlay_items[:10]:
            lines.append(
                (
                    f"- {item.get('id')} name={item.get('name')} "
                    f"attachment={item.get('attachment_kind')}:{item.get('attachment_id')}"
                )
            )
    else:
        lines.append("- no_instruction_overlays")
    if instruction_action_summary:
        lines.append(instruction_action_summary)
    lines.append("instruction_write_strategy=explicit_guarded_or_browser_fallback")
    return "\n".join(lines)


def render_automation_view(
    autopilot: ClientResponse,
    auto_planner: ClientResponse,
    triggers: ClientResponse,
    automation_action_summary: str | None,
) -> str:
    lines = ["[AUTOMATION]"]
    autopilot_payload = _safe_dict(autopilot.data)
    planner_payload = _safe_dict(auto_planner.data)
    trigger_payload = _safe_dict(triggers.data)
    lines.append(
        (
            f"autopilot_running={autopilot_payload.get('running')} "
            f"security_level={autopilot_payload.get('security_level')} "
            f"max_concurrency={autopilot_payload.get('max_concurrency')} "
            f"budget={autopilot_payload.get('budget_label')}"
        )
    )
    lines.append(f"auto_planner_enabled={planner_payload.get('enabled')}")
    lines.append(f"triggers_enabled={trigger_payload.get('enabled')}")
    if automation_action_summary:
        lines.append(automation_action_summary)
    lines.append("automation_write_mode=explicit_confirmation_only")
    if autopilot.state != "healthy":
        lines.append(f"autopilot_degraded={autopilot.state}")
    return "\n".join(lines)


def render_audit_view(audit_logs: ClientResponse, audit_analysis: ClientResponse) -> str:
    lines = ["[AUDIT]"]
    items = _safe_items(audit_logs.data)
    if items:
        for item in items[:10]:
            redacted_message = redact_sensitive_text(str(item.get("message") or ""))
            lines.append(
                (
                    f"- {item.get('id')} kind={item.get('kind')} target={item.get('target_id')} "
                    f"task={item.get('task_id')} goal={item.get('goal_id')} artifact={item.get('artifact_id')} "
                    f"trace={item.get('trace_ref')} msg={redacted_message}"
                )
            )
    else:
        lines.append("- no_audit_entries")
    analysis_payload = _safe_dict(audit_analysis.data)
    summary = _safe_dict(analysis_payload.get("summary"))
    if summary:
        lines.append(f"analysis_total={summary.get('total')} analysis_high_risk={summary.get('high_risk')}")
    patterns = analysis_payload.get("top_patterns")
    if isinstance(patterns, list) and patterns:
        lines.append(f"analysis_patterns={','.join(str(item) for item in patterns[:8])}")
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
