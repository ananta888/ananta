"""Dialect-neutral SQLAlchemy event, checkpoint, and transactional outbox stores."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowRuntimeCheckpointDB,
    WorkflowRuntimeEventDB,
    WorkflowRuntimeOutboxDB,
)
from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_runtime._serialization import canonical_json, sha256_json
from agent.services.workflow_runtime.errors import FencingTokenError, OptimisticConcurrencyError
from agent.services.workflow_runtime.events import (
    WORKFLOW_EVENT_COMMIT_OUTBOX,
    WORKFLOW_EVENT_TOPIC,
    CanonicalWorkflowEvent,
    EventStore,
    WorkflowEventCommitProof,
    WorkflowEventIdentityHeadSnapshot,
    assert_workflow_event_dedupe_read_binding,
    assert_workflow_transition_event_record_projection,
    canonical_workflow_event_from_exact_mapping,
    event_payload_equal,
    workflow_event_dedupe_read_binding,
    workflow_transition_event_observation_binding,
)
from agent.services.workflow_runtime.persistence import (
    CheckpointStore,
    assert_workflow_checkpoint_identity_read_binding,
    workflow_checkpoint_identity_read_binding,
)
from agent.services.workflow_runtime.security import SignedCheckpoint
from agent.services.workflow_runtime.sqlalchemy_support import (
    SessionFactory,
    SQLAlchemyStoreSupport,
    stable_row_id,
)

OUTBOX_STATUSES = frozenset({"pending", "processing", "published", "dead_letter"})
_TRANSITION_EVENT_COMMIT_COLUMNS = (
    ("id", WorkflowRuntimeOutboxDB.id),
    ("tenant_id", WorkflowRuntimeOutboxDB.tenant_id),
    ("aggregate_id", WorkflowRuntimeOutboxDB.aggregate_id),
    ("topic", WorkflowRuntimeOutboxDB.topic),
    ("dedupe_key", WorkflowRuntimeOutboxDB.dedupe_key),
    ("created_at", WorkflowRuntimeOutboxDB.created_at),
    ("payload", WorkflowRuntimeOutboxDB.payload),
)
_TRANSITION_EVENT_COLUMNS = (
    ("tenant_id", WorkflowRuntimeEventDB.tenant_id),
    ("workflow_id", WorkflowRuntimeEventDB.workflow_id),
    ("run_id", WorkflowRuntimeEventDB.run_id),
    ("sequence", WorkflowRuntimeEventDB.sequence),
    ("dedupe_key", WorkflowRuntimeEventDB.dedupe_key),
    ("event_id", WorkflowRuntimeEventDB.event_id),
    ("event_type", WorkflowRuntimeEventDB.event_type),
    ("content_hash", WorkflowRuntimeEventDB.content_hash),
    ("occurred_at", WorkflowRuntimeEventDB.occurred_at),
    ("canonical_event", WorkflowRuntimeEventDB.canonical_event),
)


@dataclass(frozen=True)
class RuntimeOutboxMessage:
    id: str
    tenant_id: str
    aggregate_id: str
    topic: str
    dedupe_key: str
    status: str
    revision: int
    attempts: int
    available_at: float
    claimed_by: str
    claim_expires_at: float | None
    created_at: float
    published_at: float | None
    payload: dict[str, object]


class SQLAlchemyRuntimeOutbox(SQLAlchemyStoreSupport):
    """Leased transactional outbox supporting PostgreSQL and SQLite.

    PostgreSQL claims use ``FOR UPDATE SKIP LOCKED``. SQLite uses the shared
    CAS update and the adapter lock supplied by :class:`SQLAlchemyStoreSupport`.
    """

    def claim_batch(
        self,
        *,
        tenant_id: str,
        consumer_id: str,
        limit: int = 100,
        lease_seconds: float = 30.0,
        now: float | None = None,
    ) -> tuple[RuntimeOutboxMessage, ...]:
        if not tenant_id or not consumer_id:
            raise ValueError("outbox_claim_binding_required")
        if int(limit) < 1 or float(lease_seconds) <= 0:
            raise ValueError("outbox_claim_limits_invalid")
        timestamp = float(time.time() if now is None else now)
        claimed: list[RuntimeOutboxMessage] = []
        with self._transaction() as session:
            eligible = sa.or_(
                WorkflowRuntimeOutboxDB.status == "pending",
                sa.and_(
                    WorkflowRuntimeOutboxDB.status == "processing",
                    WorkflowRuntimeOutboxDB.claim_expires_at.is_not(None),
                    WorkflowRuntimeOutboxDB.claim_expires_at <= timestamp,
                ),
            )
            statement = (
                sa.select(WorkflowRuntimeOutboxDB)
                .where(
                    WorkflowRuntimeOutboxDB.tenant_id == str(tenant_id),
                    WorkflowRuntimeOutboxDB.available_at <= timestamp,
                    eligible,
                )
                .order_by(
                    WorkflowRuntimeOutboxDB.available_at.asc(),
                    WorkflowRuntimeOutboxDB.created_at.asc(),
                    WorkflowRuntimeOutboxDB.id.asc(),
                )
                .limit(int(limit))
            )
            rows = session.execute(self._for_update(statement, skip_locked=True)).scalars().all()
            for row in rows:
                result = session.execute(
                    sa.update(WorkflowRuntimeOutboxDB)
                    .where(
                        WorkflowRuntimeOutboxDB.id == row.id,
                        WorkflowRuntimeOutboxDB.tenant_id == str(tenant_id),
                        WorkflowRuntimeOutboxDB.revision == row.revision,
                    )
                    .values(
                        status="processing",
                        revision=row.revision + 1,
                        attempts=row.attempts + 1,
                        claimed_by=str(consumer_id),
                        claim_expires_at=timestamp + float(lease_seconds),
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    continue
                session.expire(row)
                session.refresh(row)
                claimed.append(_outbox_message(row))
        return tuple(claimed)

    def acknowledge(
        self,
        *,
        tenant_id: str,
        message_id: str,
        consumer_id: str,
        expected_revision: int,
        now: float | None = None,
    ) -> RuntimeOutboxMessage:
        timestamp = float(time.time() if now is None else now)
        with self._transaction() as session:
            current = session.get(WorkflowRuntimeOutboxDB, str(message_id))
            if current is None or current.tenant_id != str(tenant_id):
                raise KeyError("outbox_message_not_found")
            if current.status == "published":
                return _outbox_message(current)
            if current.status != "processing" or current.claimed_by != str(consumer_id):
                raise OptimisticConcurrencyError("outbox_claim_owner_conflict")
            result = session.execute(
                sa.update(WorkflowRuntimeOutboxDB)
                .where(
                    WorkflowRuntimeOutboxDB.id == current.id,
                    WorkflowRuntimeOutboxDB.tenant_id == str(tenant_id),
                    WorkflowRuntimeOutboxDB.status == "processing",
                    WorkflowRuntimeOutboxDB.claimed_by == str(consumer_id),
                    WorkflowRuntimeOutboxDB.revision == int(expected_revision),
                )
                .values(
                    status="published",
                    revision=int(expected_revision) + 1,
                    published_at=timestamp,
                    claim_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise OptimisticConcurrencyError("outbox_compare_and_set_failed")
            session.expire(current)
            session.refresh(current)
            return _outbox_message(current)

    def release(
        self,
        *,
        tenant_id: str,
        message_id: str,
        consumer_id: str,
        expected_revision: int,
        retry_after_seconds: float = 0.0,
        dead_letter: bool = False,
        now: float | None = None,
    ) -> RuntimeOutboxMessage:
        timestamp = float(time.time() if now is None else now)
        if retry_after_seconds < 0:
            raise ValueError("outbox_retry_delay_invalid")
        with self._transaction() as session:
            current = session.get(WorkflowRuntimeOutboxDB, str(message_id))
            if current is None or current.tenant_id != str(tenant_id):
                raise KeyError("outbox_message_not_found")
            if current.status != "processing" or current.claimed_by != str(consumer_id):
                raise OptimisticConcurrencyError("outbox_claim_owner_conflict")
            result = session.execute(
                sa.update(WorkflowRuntimeOutboxDB)
                .where(
                    WorkflowRuntimeOutboxDB.id == current.id,
                    WorkflowRuntimeOutboxDB.revision == int(expected_revision),
                    WorkflowRuntimeOutboxDB.claimed_by == str(consumer_id),
                )
                .values(
                    status="dead_letter" if dead_letter else "pending",
                    revision=int(expected_revision) + 1,
                    available_at=timestamp + float(retry_after_seconds),
                    claimed_by="",
                    claim_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise OptimisticConcurrencyError("outbox_compare_and_set_failed")
            session.expire(current)
            session.refresh(current)
            return _outbox_message(current)

    def list_messages(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        limit: int = 100,
    ) -> tuple[RuntimeOutboxMessage, ...]:
        if status is not None and status not in OUTBOX_STATUSES:
            raise ValueError("outbox_status_invalid")
        statement = (
            sa.select(WorkflowRuntimeOutboxDB)
            .where(WorkflowRuntimeOutboxDB.tenant_id == str(tenant_id))
            .order_by(WorkflowRuntimeOutboxDB.created_at.asc(), WorkflowRuntimeOutboxDB.id.asc())
            .limit(max(0, int(limit)))
        )
        if status is not None:
            statement = statement.where(WorkflowRuntimeOutboxDB.status == status)
        with self._read_session() as session:
            return tuple(_outbox_message(row) for row in session.execute(statement).scalars().all())


class SQLAlchemyEventStore(SQLAlchemyStoreSupport, EventStore):
    """Append-only canonical EventStore with a transactional outbox."""

    def __init__(
        self,
        bind: Engine | SessionFactory,
        *,
        publish_to_outbox: bool = True,
        outbox_topic: str = WORKFLOW_EVENT_TOPIC,
    ) -> None:
        super().__init__(bind)
        self._publish_to_outbox = bool(publish_to_outbox)
        self._outbox_topic = str(outbox_topic)

    def append(self, event: CanonicalWorkflowEvent, *, expected_sequence: int) -> CanonicalWorkflowEvent:
        event.assert_valid(allow_unsequenced=True)
        try:
            with self._transaction() as session:
                duplicate = session.execute(
                    sa.select(WorkflowRuntimeEventDB).where(
                        WorkflowRuntimeEventDB.tenant_id == event.tenant_id,
                        WorkflowRuntimeEventDB.run_id == event.run_id,
                        WorkflowRuntimeEventDB.dedupe_key == event.dedupe_key,
                    )
                ).scalar_one_or_none()
                if duplicate is not None:
                    return _same_event_or_raise(duplicate, event)

                current = int(
                    session.execute(
                        sa.select(sa.func.coalesce(sa.func.max(WorkflowRuntimeEventDB.sequence), 0)).where(
                            WorkflowRuntimeEventDB.tenant_id == event.tenant_id,
                            WorkflowRuntimeEventDB.run_id == event.run_id,
                        )
                    ).scalar_one()
                )
                if int(expected_sequence) != current:
                    raise OptimisticConcurrencyError(
                        f"event_sequence_conflict:expected={expected_sequence}:actual={current}"
                    )
                stored = event.with_sequence(current + 1)
                session.add(_event_row(stored))
                if self._publish_to_outbox:
                    session.add(_event_outbox_row(stored, topic=self._outbox_topic))
                session.flush()
                return CanonicalWorkflowEvent.from_mapping(stored.to_dict())
        except IntegrityError as exc:
            return self._resolve_append_integrity(event, expected_sequence=expected_sequence, cause=exc)

    def append_transition_event(
        self,
        event: CanonicalWorkflowEvent,
        *,
        expected_sequence: int,
    ) -> CanonicalWorkflowEvent:
        if not self._publish_to_outbox or self._outbox_topic != WORKFLOW_EVENT_TOPIC:
            raise OptimisticConcurrencyError("workflow_transition_event_outbox_required")
        stored = self.append(event, expected_sequence=expected_sequence)
        if not event_payload_equal(stored, event):
            raise OptimisticConcurrencyError("workflow_transition_event_identity_conflict")
        return stored

    def _resolve_append_integrity(
        self,
        event: CanonicalWorkflowEvent,
        *,
        expected_sequence: int,
        cause: IntegrityError,
    ) -> CanonicalWorkflowEvent:
        with self._read_session() as session:
            duplicate = session.execute(
                sa.select(WorkflowRuntimeEventDB).where(
                    WorkflowRuntimeEventDB.tenant_id == event.tenant_id,
                    WorkflowRuntimeEventDB.run_id == event.run_id,
                    WorkflowRuntimeEventDB.dedupe_key == event.dedupe_key,
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                return _same_event_or_raise(duplicate, event)
            identity = session.execute(
                sa.select(WorkflowRuntimeEventDB).where(
                    WorkflowRuntimeEventDB.tenant_id == event.tenant_id,
                    WorkflowRuntimeEventDB.run_id == event.run_id,
                    WorkflowRuntimeEventDB.event_id == event.event_id,
                )
            ).scalar_one_or_none()
            if identity is not None:
                raise OptimisticConcurrencyError("event_id_payload_conflict") from cause
            actual = int(
                session.execute(
                    sa.select(sa.func.coalesce(sa.func.max(WorkflowRuntimeEventDB.sequence), 0)).where(
                        WorkflowRuntimeEventDB.tenant_id == event.tenant_id,
                        WorkflowRuntimeEventDB.run_id == event.run_id,
                    )
                ).scalar_one()
            )
        raise OptimisticConcurrencyError(
            f"event_sequence_conflict:expected={expected_sequence}:actual={actual}"
        ) from cause

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
        statement = (
            sa.select(WorkflowRuntimeEventDB)
            .where(
                WorkflowRuntimeEventDB.tenant_id == validated_tenant_id,
                WorkflowRuntimeEventDB.run_id == validated_run_id,
                WorkflowRuntimeEventDB.sequence > int(after_sequence),
            )
            .order_by(WorkflowRuntimeEventDB.sequence.asc())
        )
        if limit is not None:
            statement = statement.limit(max(0, int(limit)))
        with self._read_session() as session:
            rows = session.execute(statement).scalars().all()
            return tuple(CanonicalWorkflowEvent.from_mapping(dict(row.canonical_event)) for row in rows)

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
        with self._read_session() as session:
            row = session.execute(
                sa.select(WorkflowRuntimeEventDB).where(
                    WorkflowRuntimeEventDB.tenant_id == tenant,
                    WorkflowRuntimeEventDB.run_id == run,
                    WorkflowRuntimeEventDB.dedupe_key == dedupe,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            event = canonical_workflow_event_from_exact_mapping(dict(row.canonical_event))
            assert_workflow_transition_event_record_projection(
                event,
                tenant_id=row.tenant_id,
                workflow_id=row.workflow_id,
                run_id=row.run_id,
                sequence=row.sequence,
                dedupe_key=row.dedupe_key,
                event_id=row.event_id,
                event_type=row.event_type,
                content_hash=row.content_hash,
                occurred_at=row.occurred_at,
            )
            assert_workflow_event_dedupe_read_binding(
                event,
                expected=(tenant, workflow, run, dedupe),
            )
            return event

    def observe_transition_event(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        dedupe_key: str,
        event_id: str,
    ) -> WorkflowEventIdentityHeadSnapshot:
        if not self._publish_to_outbox or self._outbox_topic != WORKFLOW_EVENT_TOPIC:
            raise OptimisticConcurrencyError("workflow_transition_event_outbox_required")
        tenant, workflow, run, dedupe, identity = workflow_transition_event_observation_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            dedupe_key=dedupe_key,
            event_id=event_id,
        )

        event_scope = (
            WorkflowRuntimeEventDB.tenant_id == tenant,
            WorkflowRuntimeEventDB.run_id == run,
        )
        canonical_outbox_scope = (
            WorkflowRuntimeOutboxDB.tenant_id == tenant,
            WorkflowRuntimeOutboxDB.topic == WORKFLOW_EVENT_TOPIC,
        )
        delivery_dedupe = f"{run}:{dedupe}"

        def event_value(
            column: Any,
            conditions: tuple[Any, ...],
            *,
            latest: bool = False,
        ) -> Any:
            query = sa.select(column).where(*conditions)
            if latest:
                query = query.order_by(WorkflowRuntimeEventDB.sequence.desc())
            return query.limit(1).scalar_subquery()

        def outbox_value(column: Any, *conditions: Any) -> Any:
            return sa.select(column).where(*conditions).limit(1).scalar_subquery()

        dedupe_outbox = (
            *canonical_outbox_scope,
            WorkflowRuntimeOutboxDB.dedupe_key == delivery_dedupe,
        )
        identity_outbox = (
            *canonical_outbox_scope,
            WorkflowRuntimeOutboxDB.aggregate_id == run,
            WorkflowRuntimeOutboxDB.payload["event_id"].as_string() == identity,
        )
        dedupe_event_scope = (*event_scope, WorkflowRuntimeEventDB.dedupe_key == dedupe)
        identity_event_scope = (*event_scope, WorkflowRuntimeEventDB.event_id == identity)
        statement = sa.select(
            *(
                event_value(column, dedupe_event_scope).label(f"dedupe_event_{name}")
                for name, column in _TRANSITION_EVENT_COLUMNS
            ),
            *(
                event_value(column, identity_event_scope).label(f"identity_event_{name}")
                for name, column in _TRANSITION_EVENT_COLUMNS
            ),
            *(
                event_value(column, event_scope, latest=True).label(f"head_event_{name}")
                for name, column in _TRANSITION_EVENT_COLUMNS
            ),
            (
                sa.select(sa.func.count())
                .select_from(WorkflowRuntimeEventDB)
                .where(*event_scope, WorkflowRuntimeEventDB.workflow_id != workflow)
                .scalar_subquery()
            ).label("workflow_mismatch"),
            *(
                outbox_value(column, *dedupe_outbox).label(f"dedupe_{name}")
                for name, column in _TRANSITION_EVENT_COMMIT_COLUMNS
            ),
            (
                sa.select(sa.func.count()).select_from(WorkflowRuntimeOutboxDB).where(*dedupe_outbox).scalar_subquery()
            ).label("dedupe_count"),
            *(
                outbox_value(column, *identity_outbox).label(f"event_id_{name}")
                for name, column in _TRANSITION_EVENT_COMMIT_COLUMNS
            ),
            (
                sa.select(sa.func.count())
                .select_from(WorkflowRuntimeOutboxDB)
                .where(*identity_outbox)
                .scalar_subquery()
            ).label("event_id_count"),
        )
        with self._read_session() as session:
            values = session.execute(statement).one()._mapping
        if int(values["workflow_mismatch"] or 0):
            raise OptimisticConcurrencyError("workflow_event_observation_binding_conflict")

        def stored_event(prefix: str) -> CanonicalWorkflowEvent | None:
            raw = values[f"{prefix}_canonical_event"]
            if raw is None:
                return None
            event = canonical_workflow_event_from_exact_mapping(dict(raw))
            assert_workflow_transition_event_record_projection(
                event,
                tenant_id=values[f"{prefix}_tenant_id"],
                workflow_id=values[f"{prefix}_workflow_id"],
                run_id=values[f"{prefix}_run_id"],
                sequence=values[f"{prefix}_sequence"],
                dedupe_key=values[f"{prefix}_dedupe_key"],
                event_id=values[f"{prefix}_event_id"],
                event_type=values[f"{prefix}_event_type"],
                content_hash=values[f"{prefix}_content_hash"],
                occurred_at=values[f"{prefix}_occurred_at"],
            )
            return event

        def commit(prefix: str) -> WorkflowEventCommitProof | None:
            count = int(values[f"{prefix}_count"] or 0)
            if count == 0:
                return None
            if count != 1:
                raise OptimisticConcurrencyError("workflow_event_commit_identity_conflict")
            payload = values[f"{prefix}_payload"]
            if not isinstance(payload, dict):
                raise OptimisticConcurrencyError("workflow_event_commit_payload_invalid")
            return WorkflowEventCommitProof(
                commit_id=str(values[f"{prefix}_id"]),
                delivery_mode=WORKFLOW_EVENT_COMMIT_OUTBOX,
                tenant_id=str(values[f"{prefix}_tenant_id"]),
                aggregate_id=str(values[f"{prefix}_aggregate_id"]),
                topic=str(values[f"{prefix}_topic"]),
                dedupe_key=str(values[f"{prefix}_dedupe_key"]),
                created_at=float(values[f"{prefix}_created_at"]),
                payload_digest=sha256_json(payload),
            )

        return WorkflowEventIdentityHeadSnapshot(
            tenant_id=tenant,
            workflow_id=workflow,
            run_id=run,
            dedupe_key=dedupe,
            event_id=identity,
            delivery_mode=WORKFLOW_EVENT_COMMIT_OUTBOX,
            dedupe_event=stored_event("dedupe_event"),
            event_id_event=stored_event("identity_event"),
            head_event=stored_event("head_event"),
            dedupe_commit=commit("dedupe"),
            event_id_commit=commit("event_id"),
        )

    @property
    def outbox(self) -> SQLAlchemyRuntimeOutbox:
        return SQLAlchemyRuntimeOutbox(self._session_factory)


class SQLAlchemyCheckpointStore(SQLAlchemyStoreSupport, CheckpointStore):
    """Immutable, revisioned checkpoint history with CAS and fencing."""

    def save(self, checkpoint: SignedCheckpoint, *, expected_revision: int) -> SignedCheckpoint:
        checkpoint._assert_structure()
        try:
            with self._transaction() as session:
                duplicate = session.execute(
                    sa.select(WorkflowRuntimeCheckpointDB).where(
                        WorkflowRuntimeCheckpointDB.tenant_id == checkpoint.tenant_id,
                        WorkflowRuntimeCheckpointDB.checkpoint_id == checkpoint.checkpoint_id,
                    )
                ).scalar_one_or_none()
                if duplicate is not None:
                    return _same_checkpoint_or_raise(duplicate, checkpoint)
                latest = session.execute(
                    sa.select(WorkflowRuntimeCheckpointDB)
                    .where(
                        WorkflowRuntimeCheckpointDB.tenant_id == checkpoint.tenant_id,
                        WorkflowRuntimeCheckpointDB.run_id == checkpoint.run_id,
                        WorkflowRuntimeCheckpointDB.task_id == checkpoint.task_id,
                    )
                    .order_by(WorkflowRuntimeCheckpointDB.revision.desc())
                    .limit(1)
                ).scalar_one_or_none()
                current_revision = int(latest.revision if latest is not None else 0)
                current_fence = int(latest.fencing_token if latest is not None else 0)
                if int(expected_revision) != current_revision or checkpoint.revision != current_revision + 1:
                    raise OptimisticConcurrencyError(
                        f"checkpoint_revision_conflict:expected={expected_revision}:actual={current_revision}"
                    )
                if checkpoint.fencing_token < current_fence:
                    raise FencingTokenError("checkpoint_fencing_token_stale")
                session.add(_checkpoint_row(checkpoint))
                session.flush()
                return SignedCheckpoint.from_mapping(checkpoint.to_dict())
        except IntegrityError as exc:
            with self._read_session() as session:
                duplicate = session.execute(
                    sa.select(WorkflowRuntimeCheckpointDB).where(
                        WorkflowRuntimeCheckpointDB.tenant_id == checkpoint.tenant_id,
                        WorkflowRuntimeCheckpointDB.checkpoint_id == checkpoint.checkpoint_id,
                    )
                ).scalar_one_or_none()
                if duplicate is not None:
                    return _same_checkpoint_or_raise(duplicate, checkpoint)
            raise OptimisticConcurrencyError("checkpoint_compare_and_set_failed") from exc

    def get_latest(self, *, tenant_id: str, run_id: str, task_id: str) -> SignedCheckpoint | None:
        statement = (
            sa.select(WorkflowRuntimeCheckpointDB)
            .where(
                WorkflowRuntimeCheckpointDB.tenant_id == str(tenant_id),
                WorkflowRuntimeCheckpointDB.run_id == str(run_id),
                WorkflowRuntimeCheckpointDB.task_id == str(task_id),
            )
            .order_by(WorkflowRuntimeCheckpointDB.revision.desc())
            .limit(1)
        )
        with self._read_session() as session:
            row = session.execute(statement).scalar_one_or_none()
            return SignedCheckpoint.from_mapping(dict(row.signed_checkpoint)) if row else None

    def get_by_id(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        task_id: str,
        checkpoint_id: str,
    ) -> SignedCheckpoint | None:
        tenant, workflow, run, task, identity = workflow_checkpoint_identity_read_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            task_id=task_id,
            checkpoint_id=checkpoint_id,
        )
        with self._read_session() as session:
            row = session.execute(
                sa.select(WorkflowRuntimeCheckpointDB).where(
                    WorkflowRuntimeCheckpointDB.tenant_id == tenant,
                    WorkflowRuntimeCheckpointDB.checkpoint_id == identity,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            checkpoint = SignedCheckpoint.from_mapping(dict(row.signed_checkpoint))
            assert_workflow_checkpoint_identity_read_binding(
                checkpoint,
                expected=(tenant, workflow, run, task, identity),
            )
            return checkpoint

    def list_history(self, *, tenant_id: str, run_id: str, task_id: str) -> tuple[SignedCheckpoint, ...]:
        statement = (
            sa.select(WorkflowRuntimeCheckpointDB)
            .where(
                WorkflowRuntimeCheckpointDB.tenant_id == str(tenant_id),
                WorkflowRuntimeCheckpointDB.run_id == str(run_id),
                WorkflowRuntimeCheckpointDB.task_id == str(task_id),
            )
            .order_by(WorkflowRuntimeCheckpointDB.revision.asc())
        )
        with self._read_session() as session:
            return tuple(
                SignedCheckpoint.from_mapping(dict(row.signed_checkpoint))
                for row in session.execute(statement).scalars().all()
            )


def _event_row(event: CanonicalWorkflowEvent) -> WorkflowRuntimeEventDB:
    return WorkflowRuntimeEventDB(
        id=stable_row_id("wfre", event.tenant_id, event.run_id, event.event_id),
        tenant_id=event.tenant_id,
        workflow_id=event.workflow_id,
        run_id=event.run_id,
        sequence=event.sequence,
        event_id=event.event_id,
        event_type=event.event_type,
        dedupe_key=event.dedupe_key,
        content_hash=event.content_hash,
        occurred_at=event.occurred_at,
        canonical_event=event.to_dict(),
    )


def _event_outbox_row(event: CanonicalWorkflowEvent, *, topic: str) -> WorkflowRuntimeOutboxDB:
    dedupe_key = f"{event.run_id}:{event.dedupe_key}"
    return WorkflowRuntimeOutboxDB(
        id=stable_row_id("wfro", event.tenant_id, topic, dedupe_key),
        tenant_id=event.tenant_id,
        aggregate_id=event.run_id,
        topic=topic,
        dedupe_key=dedupe_key,
        status="pending",
        revision=1,
        attempts=0,
        available_at=event.occurred_at,
        claimed_by="",
        claim_expires_at=None,
        created_at=event.occurred_at,
        published_at=None,
        payload=event.to_dict(),
    )


def _checkpoint_row(checkpoint: SignedCheckpoint) -> WorkflowRuntimeCheckpointDB:
    return WorkflowRuntimeCheckpointDB(
        id=stable_row_id("wfrc", checkpoint.tenant_id, checkpoint.checkpoint_id),
        checkpoint_id=checkpoint.checkpoint_id,
        tenant_id=checkpoint.tenant_id,
        workflow_id=checkpoint.workflow_id,
        run_id=checkpoint.run_id,
        task_id=checkpoint.task_id,
        revision=checkpoint.revision,
        fencing_token=checkpoint.fencing_token,
        created_at=checkpoint.created_at,
        signed_checkpoint=checkpoint.to_dict(),
    )


def _same_event_or_raise(row: WorkflowRuntimeEventDB, candidate: CanonicalWorkflowEvent) -> CanonicalWorkflowEvent:
    if row.content_hash != candidate.content_hash:
        raise OptimisticConcurrencyError("dedupe_key_payload_conflict")
    return CanonicalWorkflowEvent.from_mapping(dict(row.canonical_event))


def _same_checkpoint_or_raise(row: WorkflowRuntimeCheckpointDB, candidate: SignedCheckpoint) -> SignedCheckpoint:
    stored = SignedCheckpoint.from_mapping(dict(row.signed_checkpoint))
    if canonical_json(stored.to_dict()) != canonical_json(candidate.to_dict()):
        raise OptimisticConcurrencyError("checkpoint_id_payload_conflict")
    return stored


def _outbox_message(row: WorkflowRuntimeOutboxDB) -> RuntimeOutboxMessage:
    return RuntimeOutboxMessage(
        id=row.id,
        tenant_id=row.tenant_id,
        aggregate_id=row.aggregate_id,
        topic=row.topic,
        dedupe_key=row.dedupe_key,
        status=row.status,
        revision=row.revision,
        attempts=row.attempts,
        available_at=row.available_at,
        claimed_by=row.claimed_by,
        claim_expires_at=row.claim_expires_at,
        created_at=row.created_at,
        published_at=row.published_at,
        payload=dict(row.payload),
    )
