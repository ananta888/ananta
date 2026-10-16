"""Lazy, Hub-only composition root for the opt-in development service."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from flask import current_app

_LOCK = threading.Lock()


def get_development_coordinator():
    from agent.config import settings
    if settings.role != 'hub':
        return None
    filename = os.environ.get('ANANTA_SELF_DEVELOPMENT_POLICY_FILE')
    if not filename:
        return None
    with _LOCK:
        if 'self_development' not in current_app.extensions:
            from agent.services.self_development.coordinator import DevelopmentCoordinator
            from agent.services.self_development.policy import DevelopmentPolicy
            policy = DevelopmentPolicy(Path(filename))
            current_app.extensions['self_development'] = DevelopmentCoordinator(
                policy, planner=_build_planner(policy))
        return current_app.extensions['self_development']


def _build_planner(policy):
    """Compose the Hub model adapter only when the operator enabled planning."""
    if not policy.planning_enabled():
        return None
    from agent.services.hub_llm_service import HubLLMService
    from agent.services.self_development.planner import HubModelContractPlanner

    service = HubLLMService()
    planning = dict(policy.value.get('planning') or {})
    overrides = {key: planning[key] for key in ('provider', 'model', 'base_url') if planning.get(key)}

    def generate(prompt):
        text, _usage, _raw = service.generate_text_and_usage(prompt=prompt, **overrides)
        return text

    return HubModelContractPlanner(generate)


def apply_worker_scope(app, policy):
    """Derive the Worker mutation scope from the single operator policy.

    Only widens the allowlist (union); never narrows the operator's own
    ``ananta_worker_workspace_mutation`` settings. Persisted so Workers receive
    it after their next config load.
    """
    from agent.services.self_development.scope import merge_worker_mutation
    if not policy.planning_enabled():
        return False
    config = app.config.get('AGENT_CONFIG')
    if not isinstance(config, dict):
        return False
    current = config.get('ananta_worker_workspace_mutation')
    merged, changed = merge_worker_mutation(policy.value, current)
    if not changed:
        return False
    config['ananta_worker_workspace_mutation'] = merged
    app.config['AGENT_CONFIG'] = config
    import json

    try:
        from agent.db_models import ConfigDB
        from agent.services.repository_registry import get_repository_registry

        get_repository_registry().config_repo.save(
            ConfigDB(key='ananta_worker_workspace_mutation', value_json=json.dumps(merged)))
    except Exception as exc:  # pragma: no cover - persistence is best effort
        app.logger.warning('self_development_worker_scope_persist_failed: %s', exc)
    app.logger.info('self_development_worker_scope_applied')
    return True


def apply_guardrails(app, policy):
    """Tie the Hub Autopilot guardrails to the operator budgets.

    Unlimited budgets (``0``/``null``) disable the matching Autopilot guardrail
    (``0`` means "no cap"), so a long unattended run is not stopped by a global
    default that is unrelated to this policy.
    """
    from agent.services.self_development.budget import unlimited
    config = app.config.get('AGENT_CONFIG')
    if not isinstance(config, dict):
        return False
    max_seconds = policy.value.get('max_seconds', 1800)
    max_tasks = policy.value.get('max_tasks', 1)
    limits = {
        'enabled': True,
        'max_runtime_seconds': 0 if unlimited(max_seconds) else int(max_seconds),
        'max_ticks_total': 0 if unlimited(max_seconds) else 5000,
        'max_dispatched_total': 0 if unlimited(max_tasks) else int(max_tasks),
    }
    current = config.get('autonomous_guardrails') or {}
    if all(current.get(key) == value for key, value in limits.items()):
        return False
    merged = {**current, **limits}
    config['autonomous_guardrails'] = merged
    app.config['AGENT_CONFIG'] = config
    import json

    try:
        from agent.db_models import ConfigDB
        from agent.services.repository_registry import get_repository_registry

        get_repository_registry().config_repo.save(
            ConfigDB(key='autonomous_guardrails', value_json=json.dumps(merged)))
    except Exception as exc:  # pragma: no cover - persistence is best effort
        app.logger.warning('self_development_guardrails_persist_failed: %s', exc)
    app.logger.info('self_development_guardrails_applied')
    return True


def apply_tool_loop_limits(app, policy):
    """Widen the Worker tool-loop iteration budget for autonomous coding runs.

    The local model reads several files before it writes; the stock iteration
    budget aborts such runs with ``loop_aborted: max_iterations`` before a patch
    is produced. Only raises the limits.
    """
    config = app.config.get('AGENT_CONFIG')
    if not isinstance(config, dict):
        return False
    current = dict(config.get('ananta_worker_tool_loop') or {})
    limits = {'max_iterations': 30, 'max_tool_calls': 60, 'max_invalid_outputs': 4,
              'read_only_nudge_after': 4}
    codecompass_reads = ['codecompass.plan_context', 'codecompass.search_symbols',
                         'codecompass.search', 'codecompass.get_file_context',
                         'codecompass.architecture_query']
    allowed = list(current.get('allowed_tools') or [])
    tools_changed = False
    for tool in codecompass_reads:
        if tool not in allowed:
            allowed.append(tool)
            tools_changed = True
    unchanged = (not tools_changed
                 and all(int(current.get(key) or 0) >= value for key, value in limits.items()))
    if unchanged:
        return False
    merged = {**current, **limits}
    if tools_changed or 'allowed_tools' in current:
        merged['allowed_tools'] = allowed
    config['ananta_worker_tool_loop'] = merged
    app.config['AGENT_CONFIG'] = config
    import json

    try:
        from agent.db_models import ConfigDB
        from agent.services.repository_registry import get_repository_registry

        get_repository_registry().config_repo.save(
            ConfigDB(key='ananta_worker_tool_loop', value_json=json.dumps(merged)))
    except Exception as exc:  # pragma: no cover - persistence is best effort
        app.logger.warning('self_development_tool_loop_persist_failed: %s', exc)
    app.logger.info('self_development_tool_loop_limits_applied')
    return True


def ensure_autopilot(app):
    """Rearm the Hub Autopilot for an active development session (P5 watchdog)."""
    import time

    from agent.services.self_development.budget import session_active
    coordinator = get_development_coordinator()
    if coordinator is None:
        return False
    try:
        state = coordinator.journal.read()
        if not session_active(coordinator.policy.value, state.get('session'), time.time()):
            return False
        if not any(i['status'] in {'ready', 'running'} for i in state['items'].values()):
            return False
        from agent.routes.tasks.autopilot import autonomous_loop
        if autonomous_loop.running:
            return True
        autonomous_loop.start(team_id=coordinator.policy.value['team_id'], goal='',
                              max_concurrency=1, interval_seconds=5,
                              security_level='balanced', budget_label='hub-verified-self-development')
        app.logger.info('self_development_autopilot_rearmed')
        return True
    except Exception as exc:  # pragma: no cover - watchdog must never block boot
        app.logger.warning('self_development_autopilot_rearm_failed: %s', exc)
        return False


def development_thread_diagnostics():
    """Admin diagnostics: code locations only, never locals or secret values."""
    import sys
    import traceback
    frames = sys._current_frames()
    return [{'thread': thread.name, 'stack': [
        {'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
        for frame in traceback.extract_stack(frames[thread.ident], limit=16)]}
        for thread in threading.enumerate() if thread.ident in frames
        and ('autonom' in thread.name or 'ThreadPoolExecutor' in thread.name)]
