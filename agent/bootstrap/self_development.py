"""Boot wiring for the opt-in Hub self-development service.

Runs only when ``ANANTA_SELF_DEVELOPMENT_POLICY_FILE`` is set on the Hub. It
applies the single-source Worker scope and rearms the Autopilot for an active
session. It never starts work when no session is active.
"""
from __future__ import annotations

import os


def initialize_self_development(app):
    if not os.environ.get('ANANTA_SELF_DEVELOPMENT_POLICY_FILE'):
        return
    try:
        from agent.services.self_development.runtime import (
            apply_guardrails,
            apply_tool_loop_limits,
            apply_worker_scope,
            ensure_autopilot,
            get_development_coordinator,
        )
        with app.app_context():
            coordinator = get_development_coordinator()
            if coordinator is None:
                return
            apply_worker_scope(app, coordinator.policy)
            apply_guardrails(app, coordinator.policy)
            apply_tool_loop_limits(app, coordinator.policy)
            ensure_autopilot(app)
    except Exception:
        app.logger.warning('self_development_bootstrap_skipped', exc_info=True)
