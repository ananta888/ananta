"""One opt-in, scope-bound result retrieval on the existing Hub tick; no new loop."""

import os

from agent.common.native_task_result_binding import is_native_task
from agent.services.native_dispatch_result_recovery import (
    production_native_result_recovery_ports,
    recover_native_dispatch_results,
)


def recover_native_result_on_tick(*, loop, tasks, ports_factory=production_native_result_recovery_ports):
    app = loop._app
    if app is None:
        return None
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        return None
    enabled = app.config.get("ANANTA_NATIVE_RESULT_RECOVERY_ENABLED",
                             os.environ.get("ANANTA_NATIVE_RESULT_RECOVERY_ENABLED") == "1")
    if enabled is not True or loop._stop_event.is_set():
        return None
    eligible = sorted((task for task in tasks if is_native_task(task)
                       and task.status == "waiting_for_review"
                       and task.status_reason_code == "native_autopilot_dispatch_blocked"
                       and isinstance(task.tenant_id, str) and task.tenant_id), key=lambda task: task.id)
    if not eligible:
        return None
    # Persisted existing tick count gives round-robin fairness without new state.
    task = eligible[int(loop.tick_count) % len(eligible)]
    try:
        with app.app_context():
            ports = ports_factory()
            result = recover_native_dispatch_results(
                ports=ports, tenant_id=task.tenant_id, limit=1, task_id=task.id,
                expected_scope=(task.goal_id, task.team_id),
            )
            if result["accepted"] != 1:
                return None
            accepted = ports.repository.get_by_id(task.id)
    except Exception:
        # SQL/transport failures remain unresolved; existing tick handling resumes.
        return None
    completed = accepted.status == "completed"
    if completed:
        loop._increment_completed()
    loop._increment_tick_count()
    loop._set_last_error(None)
    loop._persist_state(enabled=loop.running)
    return {"dispatched": 0, "recovered": 1, "completed": int(completed), "failed": int(not completed),
            "task_ids": [task.id], "reason": "native_result_recovered"}
