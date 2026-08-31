"""Best-effort observation helpers for model invocation attempts."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)


def observe_model_invocation_attempt(
    *,
    attempt: Mapping[str, Any],
    resolution_info: Mapping[str, Any],
    success: bool,
    reason_code: str,
    call_profile: Mapping[str, Any] | None,
) -> None:
    """Record content-free invocation metadata without changing call results."""

    profile = attempt.get("profile")
    try:
        from flask import current_app, g, has_app_context

        from agent.services.model_invocation_observation import (
            ModelInvocationAttemptObservation,
        )

        if not has_app_context():
            return
        port = current_app.extensions.get("model_invocation_observation_port")
        observe = getattr(port, "observe_attempt", None)
        if not callable(observe):
            return
        observe(
            ModelInvocationAttemptObservation(
                profile_id=str(getattr(profile, "profile_id", "") or "").strip().lower() or None,
                provider_id=str(attempt.get("provider") or "unknown"),
                model_id=str(attempt.get("model") or "unknown"),
                success=success,
                reason_code=str(reason_code or "unknown"),
                call_profile=call_profile,
                fallback_index=max(0, int(resolution_info.get("fallback_index") or 0)),
                context_capacity=max(1, int(getattr(profile, "context_tokens", 1) or 1)),
                confidence_available=False,
                goal_id=str(getattr(g, "llm_goal_id", "") or "").strip() or None,
                task_id=str(getattr(g, "llm_task_id", "") or "").strip() or None,
            )
        )
    except Exception:
        logger.warning("model invocation observation failed", exc_info=True)
