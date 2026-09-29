"""In-memory canonical workflow event store."""

from __future__ import annotations

import threading

from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.event_canonical import WORKFLOW_EVENT_COMMIT_INLINE, CanonicalWorkflowEvent
from agent.services.workflow_runtime.event_commit_proofs import (
    WorkflowEventCommitProof,
    WorkflowEventIdentityHeadSnapshot,
)
from agent.services.workflow_runtime.event_transition_payloads import (
    _clone_event,
    assert_workflow_event_dedupe_read_binding,
    canonical_workflow_event_from_exact_mapping,
    event_payload_equal,
    workflow_event_dedupe_read_binding,
    workflow_transition_event_observation_binding,
)


class InMemoryEventStore:
    """Thread-safe reference implementation of :class:`EventStore`."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], list[CanonicalWorkflowEvent]] = {}
        self._dedupe: dict[tuple[str, str, str], CanonicalWorkflowEvent] = {}
        self._event_ids: dict[tuple[str, str, str], CanonicalWorkflowEvent] = {}
        self._lock = threading.RLock()

    def append(self, event: CanonicalWorkflowEvent, *, expected_sequence: int) -> CanonicalWorkflowEvent:
        event.assert_valid(allow_unsequenced=True)
        key = (event.tenant_id, event.run_id)
        dedupe_key = (*key, event.dedupe_key)
        with self._lock:
            duplicate = self._dedupe.get(dedupe_key)
            if duplicate is not None:
                if duplicate.content_hash != event.content_hash:
                    raise OptimisticConcurrencyError("dedupe_key_payload_conflict")
                return _clone_event(duplicate)
            identity = self._event_ids.get((*key, event.event_id))
            if identity is not None:
                raise OptimisticConcurrencyError("event_id_payload_conflict")
            current = len(self._events.get(key, ()))
            if int(expected_sequence) != current:
                raise OptimisticConcurrencyError(
                    f"event_sequence_conflict:expected={expected_sequence}:actual={current}"
                )
            stored = _clone_event(event.with_sequence(current + 1))
            self._events.setdefault(key, []).append(stored)
            self._dedupe[dedupe_key] = stored
            self._event_ids[(*key, stored.event_id)] = stored
            return _clone_event(stored)

    def append_transition_event(
        self,
        event: CanonicalWorkflowEvent,
        *,
        expected_sequence: int,
    ) -> CanonicalWorkflowEvent:
        stored = self.append(event, expected_sequence=expected_sequence)
        if not event_payload_equal(stored, event):
            raise OptimisticConcurrencyError("workflow_transition_event_identity_conflict")
        return stored

    def append_fenced(self, event: CanonicalWorkflowEvent, *, expected_sequence: int, lease) -> CanonicalWorkflowEvent:
        from agent.services.workflow_runtime.persistence import InMemoryCheckpointStore

        if lease is None:
            raise ValueError("bpmn_recipient_lease_required")
        if not isinstance(lease.store, InMemoryCheckpointStore):
            raise OptimisticConcurrencyError("bpmn_recipient_lease_authority_unavailable")
        return lease.store.mutate_fenced(
            lease=lease,
            recipient=event,
            mutation=lambda: self.append(event, expected_sequence=expected_sequence),
        )

    def list_events(
        self,
        *,
        tenant_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[CanonicalWorkflowEvent, ...]:
        validated_tenant_id = require_canonical_identity(
            tenant_id,
            field_name="tenant_id",
        )
        validated_run_id = require_canonical_identity(
            run_id,
            field_name="run_id",
        )
        with self._lock:
            values = [
                event
                for event in self._events.get((validated_tenant_id, validated_run_id), ())
                if event.sequence > int(after_sequence)
            ]
            if limit is not None:
                values = values[: max(0, int(limit))]
            return tuple(_clone_event(event) for event in values)

    def get_by_dedupe(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        dedupe_key: str,
    ) -> CanonicalWorkflowEvent | None:
        tenant, workflow, run, dedupe = workflow_event_dedupe_read_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            dedupe_key=dedupe_key,
        )
        with self._lock:
            event = self._dedupe.get((tenant, run, dedupe))
            if event is None:
                return None
            assert_workflow_event_dedupe_read_binding(
                event,
                expected=(tenant, workflow, run, dedupe),
            )
            return canonical_workflow_event_from_exact_mapping(event.to_dict())

    def observe_transition_event(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        dedupe_key: str,
        event_id: str,
    ) -> WorkflowEventIdentityHeadSnapshot:
        tenant, workflow, run, dedupe, identity = workflow_transition_event_observation_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            dedupe_key=dedupe_key,
            event_id=event_id,
        )
        with self._lock:
            stream = self._events.get((tenant, run), ())
            if any(event.workflow_id != workflow for event in stream):
                raise OptimisticConcurrencyError("workflow_event_observation_binding_conflict")
            dedupe_event = self._dedupe.get((tenant, run, dedupe))
            event_id_event = self._event_ids.get((tenant, run, identity))
            head_event = stream[-1] if stream else None
            return WorkflowEventIdentityHeadSnapshot(
                tenant_id=tenant,
                workflow_id=workflow,
                run_id=run,
                dedupe_key=dedupe,
                event_id=identity,
                delivery_mode=WORKFLOW_EVENT_COMMIT_INLINE,
                dedupe_event=dedupe_event,
                event_id_event=event_id_event,
                head_event=head_event,
                dedupe_commit=(WorkflowEventCommitProof.for_event(dedupe_event) if dedupe_event is not None else None),
                event_id_commit=(
                    WorkflowEventCommitProof.for_event(event_id_event) if event_id_event is not None else None
                ),
            )
