"""Organization event envelope and idempotent replay boundary."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol

from agent.common.redaction import VisibilityLevel, redact

ORGANIZATION_EVENT_TYPES = frozenset(
    {
        "organization_instantiated",
        "team_started",
        "task_routed",
        "dependency_blocked",
        "dependency_released",
        "handoff_submitted",
        "handoff_accepted",
        "handoff_rejected",
        "handoff_needs_changes",
        "handoff_cancelled",
        "gate_opened",
        "gate_approved",
        "gate_rejected",
        "escalation_requested",
        "workflow_rework_requested",
        "workflow_loop_started",
        "workflow_loop_exhausted",
        "workflow_loop_completed",
        "budget_reserved",
        "budget_settled",
        "budget_exhausted",
        "organization_completed",
    }
)


@dataclass(frozen=True, slots=True)
class OrganizationEvent:
    event_id: str
    event_type: str
    organization_id: str
    definition_revision: str
    snapshot_hash: str
    correlation_id: str
    sequence: int
    occurred_at: str
    payload: dict[str, object]


class OrganizationEventStorePort(Protocol):
    def append_once(self, event: OrganizationEvent) -> tuple[bool, OrganizationEvent]: ...

    def list_for_organization(self, organization_id: str) -> tuple[OrganizationEvent, ...]: ...


class OrganizationEventService:
    """Emits redacted envelopes; consumers own their own projections."""

    def __init__(self, *, store: OrganizationEventStorePort) -> None:
        self._store = store

    def emit(
        self,
        *,
        event_type: str,
        organization_id: str,
        definition_revision: str,
        snapshot_hash: str,
        correlation_id: str,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> OrganizationEvent:
        if event_type not in ORGANIZATION_EVENT_TYPES:
            raise ValueError("organization_event_type_unknown")
        if any(
            not str(value or "").strip()
            for value in (
                organization_id,
                definition_revision,
                snapshot_hash,
                correlation_id,
                idempotency_key,
            )
        ):
            raise ValueError("organization_event_binding_missing")
        event_id = (
            "organization-event-"
            + hashlib.sha256(f"{organization_id}:{event_type}:{idempotency_key}".encode()).hexdigest()[:28]
        )
        safe_payload = redact(dict(payload or {}), VisibilityLevel.USER)
        event = OrganizationEvent(
            event_id=event_id,
            event_type=event_type,
            organization_id=organization_id,
            definition_revision=definition_revision,
            snapshot_hash=snapshot_hash,
            correlation_id=correlation_id,
            # The store owns ordering and replaces this provisional value
            # under its append lock.  Emission therefore stays O(1).
            sequence=1,
            occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            payload=safe_payload,
        )
        inserted, stored = self._store.append_once(event)
        if not inserted and self._semantic_digest(stored) != self._semantic_digest(event):
            raise ValueError("organization_event_idempotency_conflict")
        return stored

    def replay(
        self,
        *,
        organization_id: str,
        initial: dict[str, object],
        reducer: Callable[[dict[str, object], OrganizationEvent], dict[str, object]],
    ) -> dict[str, object]:
        state = dict(initial)
        expected_sequence = 1
        seen: set[str] = set()
        for event in self._store.list_for_organization(organization_id):
            if event.event_id in seen:
                continue
            if event.sequence != expected_sequence:
                raise ValueError("organization_event_sequence_gap")
            state = reducer(state, event)
            seen.add(event.event_id)
            expected_sequence += 1
        return state

    def runtime_projection(
        self,
        *,
        organization_id: str,
        events: tuple[OrganizationEvent, ...] | None = None,
    ) -> dict[str, object]:
        """Deterministically rebuild the bounded runtime read model.

        This projection contains references and statuses only.  Authoritative
        task/artifact bodies remain in their existing stores and topology/UI
        layout state is never mutated by replay.
        """

        event_rows = events if events is not None else self._store.list_for_organization(organization_id)
        state: dict[str, object] = {
            "organization_id": organization_id,
            "status": "unknown",
            "definition_revision": None,
            "snapshot_hash": None,
            "teams": {},
            "units": {},
            "tasks": {},
            "dependencies": {},
            "handoffs": {},
            "gates": {},
            "workflow_loops": {},
            "workflows": {},
            "budgets": {},
            "escalations": [],
            "last_sequence": 0,
        }
        observed_bindings: set[tuple[str, str]] = set()
        for event in event_rows:
            observed_bindings.add((event.definition_revision, event.snapshot_hash))
            state["definition_revision"] = event.definition_revision
            state["snapshot_hash"] = event.snapshot_hash
            state["last_sequence"] = event.sequence
            self._reduce_runtime_event(state, event)
        state["binding_history_count"] = len(observed_bindings)
        state["replayed_event_count"] = len(event_rows)
        return state

    @staticmethod
    def _reduce_runtime_event(
        state: dict[str, object],
        event: OrganizationEvent,
    ) -> None:
        payload = dict(event.payload or {})
        event_type = event.event_type
        if event_type == "organization_instantiated":
            state["status"] = str(payload.get("status") or "draft")
        elif event_type == "organization_completed":
            state["status"] = "completed"
        elif event_type == "team_started":
            _upsert_projection_row(
                state,
                "units",
                str(payload.get("unit_id") or ""),
                {"status": "running"},
            )
            _upsert_projection_row(
                state,
                "teams",
                str(payload.get("team_id") or ""),
                {"status": "running", **_safe_refs(payload, "unit_id", "workflow_id")},
            )
        elif event_type == "task_routed":
            _upsert_projection_row(
                state,
                "tasks",
                str(payload.get("task_id") or ""),
                {
                    "status": "routed",
                    **_safe_refs(
                        payload,
                        "unit_id",
                        "team_id",
                        "role_slot_id",
                        "workflow_id",
                    ),
                },
            )
        elif event_type in {"dependency_blocked", "dependency_released"}:
            _upsert_projection_row(
                state,
                "dependencies",
                str(payload.get("dependency_id") or ""),
                {
                    "status": "blocked" if event_type.endswith("blocked") else "released",
                    **_safe_refs(payload, "source_task_id", "target_task_id", "gate_id"),
                },
            )
        elif event_type.startswith("handoff_"):
            _upsert_projection_row(
                state,
                "handoffs",
                str(payload.get("handoff_id") or ""),
                {
                    "status": event_type.removeprefix("handoff_"),
                    **_safe_refs(
                        payload,
                        "producer_team_id",
                        "consumer_team_id",
                        "producer_task_id",
                        "consumer_task_id",
                    ),
                },
            )
        elif event_type.startswith("gate_"):
            _upsert_projection_row(
                state,
                "gates",
                str(payload.get("gate_id") or ""),
                {
                    "status": event_type.removeprefix("gate_"),
                    **_safe_refs(payload, "task_id", "workflow_id"),
                },
            )
        elif event_type.startswith("workflow_"):
            workflow_status = event_type.removeprefix("workflow_")
            _upsert_projection_row(
                state,
                "workflow_loops",
                str(payload.get("loop_instance_id") or ""),
                {
                    "status": workflow_status,
                    **_safe_refs(payload, "workflow_id", "task_id", "artifact_version"),
                },
            )
            _upsert_projection_row(
                state,
                "workflows",
                str(payload.get("workflow_id") or ""),
                {
                    "status": workflow_status,
                    **_safe_refs(payload, "task_id", "loop_instance_id"),
                },
            )
        elif event_type.startswith("budget_"):
            _upsert_projection_row(
                state,
                "budgets",
                str(payload.get("reservation_id") or ""),
                {
                    "status": event_type.removeprefix("budget_"),
                    **_safe_refs(payload, "task_id", "team_id", "workflow_id"),
                },
            )
        elif event_type == "escalation_requested":
            escalations = state.get("escalations")
            if isinstance(escalations, list):
                escalations.append(
                    {
                        "event_id": event.event_id,
                        **_safe_refs(
                            payload,
                            "escalation_id",
                            "task_id",
                            "team_id",
                            "gate_id",
                        ),
                        "reason_code": str(payload.get("reason_code") or ""),
                    }
                )

    @staticmethod
    def _semantic_digest(event: OrganizationEvent) -> str:
        payload = asdict(event)
        payload.pop("sequence", None)
        payload.pop("occurred_at", None)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()


class InMemoryOrganizationEventStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, list[OrganizationEvent]] = {}
        self._by_id: dict[str, OrganizationEvent] = {}

    def append_once(self, event: OrganizationEvent) -> tuple[bool, OrganizationEvent]:
        with self._lock:
            existing = self._by_id.get(event.event_id)
            if existing is not None:
                return False, existing
            rows = self._events.setdefault(event.organization_id, [])
            normalized = OrganizationEvent(
                **{
                    **asdict(event),
                    "sequence": len(rows) + 1,
                }
            )
            rows.append(normalized)
            self._by_id[event.event_id] = normalized
            return True, normalized

    def list_for_organization(self, organization_id: str) -> tuple[OrganizationEvent, ...]:
        with self._lock:
            return tuple(self._events.get(organization_id, ()))


def _safe_refs(payload: dict[str, object], *keys: str) -> dict[str, str]:
    return {key: str(payload[key]) for key in keys if payload.get(key) is not None and str(payload.get(key) or "")}


def _upsert_projection_row(
    state: dict[str, object],
    collection: str,
    identifier: str,
    value: dict[str, object],
) -> None:
    if not identifier:
        return
    rows = state.get(collection)
    if not isinstance(rows, dict):
        return
    prior = rows.get(identifier)
    rows[identifier] = {
        **(dict(prior) if isinstance(prior, dict) else {}),
        **value,
    }


__all__ = [
    "InMemoryOrganizationEventStore",
    "ORGANIZATION_EVENT_TYPES",
    "OrganizationEvent",
    "OrganizationEventService",
    "OrganizationEventStorePort",
]
