"""Permit exact journal-bound Native result admission from transport review only."""

from agent.db_models.native_dispatch import NativeDispatchDB
from agent.repositories.sqlalchemy_support import require_locked_row, stable_row_id
from agent.services.native_dispatch_binding import native_dispatch_binding_digest


def require_native_transport_recovery(*, session, task, authority):
    if task.status != "waiting_for_review" or task.status_reason_code != "native_autopilot_dispatch_blocked":
        raise ValueError("native_result_active_task_required")
    row = require_locked_row(session, NativeDispatchDB, stable_row_id("native-dispatch", task.tenant_id, task.id),
                             missing_reason="native_dispatch_recovery_journal_required")
    # Native authority has already validated the current lease, grant and Worker.
    if (row.state not in {"sending", "unknown"}
            or row.binding_digest != native_dispatch_binding_digest(
                task, worker_id=authority.worker_id, worker_url=authority.worker_url,
                assignment_revision=authority.assignment_revision)):
        raise ValueError("native_dispatch_recovery_binding_denied")
