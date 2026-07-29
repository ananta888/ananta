"""Task-intervention adapter for the Hub-owned Run-Control service."""

from __future__ import annotations

import inspect
import time
from typing import Any


def _accepts_vector_authorization(intervene) -> bool:
    """Recognize both the current API and compatible ``**kwargs`` adapters."""

    try:
        parameters = inspect.signature(intervene).parameters
    except (TypeError, ValueError):
        return True
    return bool(
        "vector_authorization" in parameters
        or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
    )


class RunControlTaskInterventionMixin:
    """Translate Run commands into authorized TaskAdmin interventions."""

    def _task_intervene(self, cmd: Any, action: str) -> None:
        task_id = str(cmd.task_id or "").strip()
        if not task_id:
            cmd.status = "rejected_by_policy"
            cmd.result = {"error": "task_id_required"}
            return

        from agent.services.service_registry import get_core_services
        from agent.services.vector_store_authorization_policy import (
            get_vector_store_authorization_policy,
        )

        authorization = (
            get_vector_store_authorization_policy().system_context(
                actor=cmd.requested_by,
                purpose="run_control",
            )
        )
        task_admin = get_core_services().task_admin_service
        intervene = task_admin.intervene_task
        if _accepts_vector_authorization(intervene):
            outcome = intervene(
                task_id=task_id,
                action=action,
                actor=cmd.requested_by,
                vector_authorization=authorization,
            )
        else:
            # Preserve old in-process adapters only for generic tasks.  A
            # reserved Vector row must never lose its explicit authorization
            # or fall through to an adapter that predates the domain.
            from agent.services.vector_task_admin_guard_service import (
                get_vector_task_admin_guard_service,
            )

            if (
                get_vector_task_admin_guard_service()
                .require_authorized_if_vector(
                    task_id=task_id,
                    authorization=authorization,
                )
            ):
                outcome = (
                    False,
                    "vector_index_task_admin_adapter_incompatible",
                    {
                        "reason_code": (
                            "vector_index_task_admin_adapter_incompatible"
                        ),
                        "http_status": 409,
                    },
                )
            else:
                outcome = intervene(
                    task_id=task_id,
                    action=action,
                    actor=cmd.requested_by,
                )
        ok, message, data = outcome
        if ok:
            cmd.status = "applied"
            cmd.result.update(data)
            cmd.effective_at = time.time()
            return

        try:
            intervention_status = int(
                data.get("http_status") or 0
            )
        except (TypeError, ValueError):
            intervention_status = 0
        cmd.status = (
            "rejected_by_policy"
            if (
                message == "invalid_transition"
                or 400 <= intervention_status < 500
            )
            else "failed"
        )
        cmd.result.update(
            {
                "error": message,
                **{
                    key: value
                    for key, value in data.items()
                    if key != "error"
                },
            }
        )


__all__ = ["RunControlTaskInterventionMixin"]
