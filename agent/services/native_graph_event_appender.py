"""Bounded Native event append across independent Hub gateway audit writers.

Checkpoint cursors describe the graph state, not an exclusive event-stream
lease. Only known observations may be caught up without replaying graph state.
Another control transition is a stale-state conflict, never silently skipped.
"""

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, EventStore

_OBSERVATIONS = frozenset(
    {
        "workflow.context.bundle_read",
        "workflow.budget.provider_reserved",
        "workflow.budget.provider_reconciled",
        "workflow.budget.retry_consumed",
        "workflow.step.authorization_checked",
        "workflow.tool.authorization_checked",
        "workflow.tool.approval_consumed",
        "workflow.tool.approval_consumption_pending",
        *(
            f"workflow.side_effect.{status}"
            for status in ("planned", "authorized", "started", "completed", "failed", "uncertain", "compensated")
        ),
    }
)


def append_native_event(store: EventStore, event: CanonicalWorkflowEvent, *, observed_sequence: int, lease=None):
    append = store.append
    arguments = {}
    if lease is not None:
        append = getattr(store, "append_fenced", None)
        if not callable(append):
            raise RuntimeError("bpmn_event_recipient_fencing_unavailable")
        arguments["lease"] = lease
    cursor = observed_sequence
    for _ in range(8):
        try:
            return append(event, expected_sequence=cursor, **arguments)
        except OptimisticConcurrencyError as exc:
            if not str(exc).startswith(("event_sequence_conflict:", "dedupe_key_payload_conflict")):
                raise
            updates = store.list_events(
                tenant_id=event.tenant_id, run_id=event.run_id, after_sequence=cursor, limit=257
            )
            if not updates or len(updates) > 256:
                raise OptimisticConcurrencyError("native_event_catchup_unavailable") from exc
            for stored in updates:
                if (stored.tenant_id, stored.workflow_id, stored.run_id) != (
                    event.tenant_id,
                    event.workflow_id,
                    event.run_id,
                ):
                    raise OptimisticConcurrencyError("native_event_catchup_binding_mismatch") from exc
                if stored.sequence != cursor + 1:
                    raise OptimisticConcurrencyError("native_event_catchup_history_gap") from exc
                if stored.dedupe_key == event.dedupe_key:
                    # Recover the first durable occurrence after a crash between
                    # event append and checkpoint save; never rewrite its time.
                    if _intent(stored) != _intent(event):
                        raise OptimisticConcurrencyError("dedupe_key_payload_conflict") from exc
                    return stored
                if stored.event_type not in _OBSERVATIONS or stored.actor != "hub":
                    raise OptimisticConcurrencyError("native_event_stale_control_state") from exc
                cursor = stored.sequence
    raise OptimisticConcurrencyError("native_event_append_contention_exceeded")


def _intent(event):
    value = event.to_dict()
    for key in ("event_id", "occurred_at", "sequence"):
        value.pop(key, None)
    return canonical_json(value)


def append_gateway_observation(store: EventStore, event: CanonicalWorkflowEvent):
    """Append audit only; never rebase a workflow control decision.

    Gateway authorization/metering already took place under its own authority.
    Its observation may share the stream with a concurrent graph checkpoint.
    Exact replay retains the first timestamp; changed dedupe content is denied.
    """
    if event.actor != "hub" or event.event_type not in _OBSERVATIONS:
        raise ValueError("gateway_observation_event_required")
    for _ in range(8):
        current = store.list_events(tenant_id=event.tenant_id, run_id=event.run_id)
        for stored in current:
            if stored.workflow_id != event.workflow_id:
                raise OptimisticConcurrencyError("gateway_observation_binding_mismatch")
            if stored.dedupe_key == event.dedupe_key:
                if _intent(stored) != _intent(event):
                    raise OptimisticConcurrencyError("dedupe_key_payload_conflict")
                return stored
        try:
            return store.append(event, expected_sequence=len(current))
        except OptimisticConcurrencyError as exc:
            if not str(exc).startswith(("event_sequence_conflict:", "dedupe_key_payload_conflict")):
                raise
    raise OptimisticConcurrencyError("gateway_observation_contention_exceeded")
