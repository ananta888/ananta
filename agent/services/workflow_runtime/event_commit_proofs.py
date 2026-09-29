"""Commit proofs and identity-head snapshots for canonical workflow event appends."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.event_canonical import (
    WORKFLOW_EVENT_COMMIT_INLINE,
    WORKFLOW_EVENT_COMMIT_MODES,
    WORKFLOW_EVENT_TOPIC,
    CanonicalWorkflowEvent,
)
from agent.services.workflow_runtime.event_transition_payloads import (
    _transition_event_identifier,
    canonical_workflow_event_from_exact_mapping,
    workflow_event_delivery_dedupe_key,
    workflow_event_outbox_id,
    workflow_transition_event_observation_binding,
)


@dataclass(frozen=True, slots=True)
class WorkflowEventCommitProof:
    """Immutable backend commit projection; mutable publisher state is excluded."""

    commit_id: str
    delivery_mode: str
    tenant_id: str
    aggregate_id: str
    topic: str
    dedupe_key: str
    created_at: float
    payload_digest: str

    def __post_init__(self) -> None:
        tenant = require_canonical_identity(self.tenant_id, field_name="tenant_id")
        run = require_canonical_identity(self.aggregate_id, field_name="run_id")
        if self.delivery_mode not in WORKFLOW_EVENT_COMMIT_MODES:
            raise OptimisticConcurrencyError("workflow_event_commit_mode_invalid")
        if self.topic != WORKFLOW_EVENT_TOPIC:
            raise OptimisticConcurrencyError("workflow_event_commit_topic_conflict")
        _transition_event_identifier(
            self.commit_id,
            maximum=80,
            reason="commit_id",
        )
        _transition_event_identifier(
            self.dedupe_key,
            maximum=768,
            reason="outbox_dedupe_key",
        )
        for value in (self.created_at,):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise OptimisticConcurrencyError("workflow_event_commit_timestamp_invalid")
            if float(value) <= 0:
                raise OptimisticConcurrencyError("workflow_event_commit_timestamp_invalid")
        if (
            not isinstance(self.payload_digest, str)
            or len(self.payload_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.payload_digest)
        ):
            raise OptimisticConcurrencyError("workflow_event_commit_payload_digest_invalid")
        if tenant != self.tenant_id or run != self.aggregate_id:
            raise OptimisticConcurrencyError("workflow_event_commit_binding_conflict")

    @classmethod
    def for_event(
        cls,
        event: CanonicalWorkflowEvent,
        *,
        delivery_mode: str = WORKFLOW_EVENT_COMMIT_INLINE,
    ) -> WorkflowEventCommitProof:
        return cls(
            commit_id=workflow_event_outbox_id(event),
            delivery_mode=delivery_mode,
            tenant_id=event.tenant_id,
            aggregate_id=event.run_id,
            topic=WORKFLOW_EVENT_TOPIC,
            dedupe_key=workflow_event_delivery_dedupe_key(event),
            created_at=event.occurred_at,
            payload_digest=sha256_json(event.to_dict()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "delivery_mode": self.delivery_mode,
            "tenant_id": self.tenant_id,
            "aggregate_id": self.aggregate_id,
            "topic": self.topic,
            "dedupe_key": self.dedupe_key,
            "created_at": self.created_at,
            "payload_digest": self.payload_digest,
        }


@dataclass(frozen=True, slots=True)
class WorkflowEventIdentityHeadSnapshot:
    """One atomic read of both event identities, stream head, and commit proof."""

    tenant_id: str
    workflow_id: str
    run_id: str
    dedupe_key: str
    event_id: str
    delivery_mode: str
    dedupe_event: CanonicalWorkflowEvent | None
    event_id_event: CanonicalWorkflowEvent | None
    head_event: CanonicalWorkflowEvent | None
    dedupe_commit: WorkflowEventCommitProof | None
    event_id_commit: WorkflowEventCommitProof | None

    def __post_init__(self) -> None:
        expected = workflow_transition_event_observation_binding(
            tenant_id=self.tenant_id,
            workflow_id=self.workflow_id,
            run_id=self.run_id,
            dedupe_key=self.dedupe_key,
            event_id=self.event_id,
        )
        if self.delivery_mode not in WORKFLOW_EVENT_COMMIT_MODES:
            raise OptimisticConcurrencyError("workflow_event_observation_mode_invalid")
        cloned_events: list[CanonicalWorkflowEvent | None] = []
        for role, event in (
            ("dedupe", self.dedupe_event),
            ("event_id", self.event_id_event),
            ("head", self.head_event),
        ):
            if event is None:
                cloned_events.append(None)
                continue
            if not isinstance(event, CanonicalWorkflowEvent):
                raise OptimisticConcurrencyError("workflow_event_observation_invalid")
            cloned = canonical_workflow_event_from_exact_mapping(event.to_dict())
            if (cloned.tenant_id, cloned.workflow_id, cloned.run_id) != expected[:3]:
                raise OptimisticConcurrencyError("workflow_event_observation_binding_conflict")
            if role == "dedupe" and cloned.dedupe_key != expected[3]:
                raise OptimisticConcurrencyError("workflow_event_observation_binding_conflict")
            if role == "event_id" and cloned.event_id != expected[4]:
                raise OptimisticConcurrencyError("workflow_event_observation_binding_conflict")
            cloned_events.append(cloned)
        dedupe_event, event_id_event, head_event = cloned_events
        if head_event is None and (dedupe_event is not None or event_id_event is not None):
            raise OptimisticConcurrencyError("workflow_event_observation_head_conflict")
        if head_event is not None:
            for event in (dedupe_event, event_id_event):
                if event is not None and event.sequence > head_event.sequence:
                    raise OptimisticConcurrencyError("workflow_event_observation_head_conflict")
        for commit in (self.dedupe_commit, self.event_id_commit):
            if commit is not None and not isinstance(commit, WorkflowEventCommitProof):
                raise OptimisticConcurrencyError("workflow_event_commit_invalid")
            if commit is not None and (
                commit.tenant_id != expected[0]
                or commit.aggregate_id != expected[2]
                or commit.delivery_mode != self.delivery_mode
            ):
                raise OptimisticConcurrencyError("workflow_event_commit_binding_conflict")
        object.__setattr__(self, "dedupe_event", dedupe_event)
        object.__setattr__(self, "event_id_event", event_id_event)
        object.__setattr__(self, "head_event", head_event)

    @property
    def head_sequence(self) -> int:
        return self.head_event.sequence if self.head_event is not None else 0
