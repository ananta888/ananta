"""Canonical workflow events, event-store port, and rebuildable projections.

This module is the stable public entry point and owns the event-store ports;
the implementation lives in the ``event_*`` sibling modules and is re-exported
here unchanged.
"""

from __future__ import annotations

import time  # noqa: F401 - kept as a module attribute for existing clock/uuid test seams
import uuid  # noqa: F401 - kept as a module attribute for existing clock/uuid test seams
from typing import Protocol

from agent.services.workflow_runtime.event_canonical import (  # noqa: F401 - public re-export
    _CANONICAL_EVENT_FIELDS,
    _EVENT_TYPE_UNSET,
    CANONICAL_WORKFLOW_EVENT_SCHEMA,
    WORKFLOW_EVENT_COMMIT_INLINE,
    WORKFLOW_EVENT_COMMIT_MODES,
    WORKFLOW_EVENT_COMMIT_OUTBOX,
    WORKFLOW_EVENT_TOPIC,
    CanonicalWorkflowEvent,
)
from agent.services.workflow_runtime.event_commit_proofs import (  # noqa: F401 - public re-export
    WorkflowEventCommitProof,
    WorkflowEventIdentityHeadSnapshot,
)
from agent.services.workflow_runtime.event_memory_store import (  # noqa: F401 - public re-export
    InMemoryEventStore,
)
from agent.services.workflow_runtime.event_projection import (  # noqa: F401 - public re-export
    LegacyWorkflowBackendEventAdapter,
    WorkflowRunProjection,
)
from agent.services.workflow_runtime.event_transition_payloads import (  # noqa: F401 - public re-export
    _MAX_TRANSITION_EVENT_BYTES,
    _MAX_TRANSITION_JSON_DEPTH,
    _MAX_TRANSITION_JSON_ITEMS,
    _clone_event,
    _copy_transition_json,
    _stable_legacy_timestamp,
    _transition_event_identifier,
    _TransitionJsonBudget,
    assert_workflow_event_dedupe_read_binding,
    assert_workflow_transition_event_record_projection,
    canonical_workflow_event_from_exact_mapping,
    event_payload_equal,
    workflow_event_dedupe_read_binding,
    workflow_event_delivery_dedupe_key,
    workflow_event_outbox_id,
    workflow_transition_event_observation_binding,
    workflow_transition_event_payload_copy,
)


class EventStore(Protocol):
    """Append-only, tenant-bound canonical event storage."""

    def append(self, event: CanonicalWorkflowEvent, *, expected_sequence: int) -> CanonicalWorkflowEvent: ...

    def list_events(
        self,
        *,
        tenant_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[CanonicalWorkflowEvent, ...]: ...


class WorkflowTransitionEventAppendPort(Protocol):
    """Append one transition event through the raw authoritative store."""

    def append_transition_event(
        self,
        event: CanonicalWorkflowEvent,
        *,
        expected_sequence: int,
    ) -> CanonicalWorkflowEvent: ...


class WorkflowTransitionEventObservationReadPort(Protocol):
    """Atomically read transition event identities, head, and commit evidence."""

    def observe_transition_event(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        dedupe_key: str,
        event_id: str,
    ) -> WorkflowEventIdentityHeadSnapshot: ...


class WorkflowEventDedupeReadPort(Protocol):
    """Read one exact transition-owned event identity.

    The bounded canonical key contract is intentionally narrower than legacy
    ``EventStore`` inputs; the existing broad mutation protocol is unchanged.
    """

    def get_by_dedupe(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        dedupe_key: str,
    ) -> CanonicalWorkflowEvent | None: ...


