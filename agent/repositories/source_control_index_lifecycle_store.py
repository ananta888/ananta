"""SQL store for knowledge-index bindings, runs and activation."""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    ActiveKnowledgeIndexEventDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceControlJobEventOutboxDB,
)
from agent.models.source_control_persistence import (
    ActivationReconciliationResult,
    ActiveKnowledgeIndexEventRecord,
    ActiveKnowledgeIndexRecord,
    IndexLifecycleProjection,
    KnowledgeIndexBindingRecord,
    KnowledgeIndexRunBindingRecord,
    SourceControlPersistenceError,
    derive_active_index_id,
    derive_index_lifecycle,
)
from agent.repositories.source_control_record_mappers import (
    activation_event_record,
    active_index_record,
    index_binding_record,
    index_run_binding_record,
    require_sha256_digest,
    stable_id,
)
from agent.repositories.source_control_scoped_lookups import (
    require_scoped_index,
    require_scoped_revision,
)


class SQLSourceIndexLifecycleStore:
    """Persist index bindings, verified runs and generation-safe activation."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], float] = time.time,
        activation_fault_hook: Callable[[], None] | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._activation_fault_hook = activation_fault_hook

    def save_index_binding(
        self, record: KnowledgeIndexBindingRecord
    ) -> KnowledgeIndexBindingRecord:
        with Session(self._engine) as db:
            existing = db.get(
                KnowledgeIndexSourceBindingDB,
                record.knowledge_index_id,
            )
            if existing is not None:
                stored = index_binding_record(existing)
                if stored != record:
                    raise SourceControlPersistenceError(
                        "source_control_index_binding_conflict"
                    )
                return stored
            require_scoped_revision(
                db,
                tenant_id=record.tenant_id,
                project_id=record.project_id,
                owner_id=record.owner_id,
                source_revision_id=record.source_revision_id,
            )
            row = KnowledgeIndexSourceBindingDB(**record.__dict__)
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise SourceControlPersistenceError(
                    "source_control_index_binding_conflict"
                ) from None
            db.refresh(row)
            return index_binding_record(row)

    def save_index_run_binding(
        self, record: KnowledgeIndexRunBindingRecord
    ) -> KnowledgeIndexRunBindingRecord:
        with Session(self._engine) as db:
            existing = db.get(
                KnowledgeIndexRunSourceBindingDB,
                record.index_run_id,
            )
            if existing is not None:
                stored = index_run_binding_record(existing)
                if stored != record:
                    raise SourceControlPersistenceError(
                        "source_control_index_run_binding_conflict"
                    )
                return stored
            index = require_scoped_index(
                db,
                tenant_id=record.tenant_id,
                project_id=record.project_id,
                owner_id=record.owner_id,
                knowledge_index_id=record.knowledge_index_id,
            )
            if (
                index.source_revision_id != record.source_revision_id
                or index.policy_snapshot_id != record.policy_snapshot_id
                or index.policy_snapshot_digest
                != record.policy_snapshot_digest
            ):
                raise SourceControlPersistenceError(
                    "source_control_index_run_binding_mismatch"
                )
            row = KnowledgeIndexRunSourceBindingDB(**record.__dict__)
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise SourceControlPersistenceError(
                    "source_control_index_run_binding_conflict"
                ) from None
            db.refresh(row)
            return index_run_binding_record(row)

    def project_completed_index_run(
        self,
        *,
        index: KnowledgeIndexBindingRecord,
        run: KnowledgeIndexRunBindingRecord,
    ) -> tuple[KnowledgeIndexBindingRecord, KnowledgeIndexRunBindingRecord]:
        """Atomically insert or replay one fully verified completed run."""

        if (
            index.status != "completed"
            or run.status != "completed"
            or not run.artifacts_verified
            or not index.activation_requested
            or not index.artifact_manifest_digest
            or run.artifact_manifest_digest
            != index.artifact_manifest_digest
            or run.knowledge_index_id != index.knowledge_index_id
            or (
                run.tenant_id,
                run.project_id,
                run.owner_id,
                run.source_revision_id,
                run.policy_snapshot_id,
                run.policy_snapshot_digest,
            )
            != (
                index.tenant_id,
                index.project_id,
                index.owner_id,
                index.source_revision_id,
                index.policy_snapshot_id,
                index.policy_snapshot_digest,
            )
        ):
            raise SourceControlPersistenceError(
                "source_control_completed_index_projection_invalid"
            )
        require_sha256_digest(index.artifact_manifest_digest)
        with Session(self._engine) as db:
            with db.begin():
                require_scoped_revision(
                    db,
                    tenant_id=index.tenant_id,
                    project_id=index.project_id,
                    owner_id=index.owner_id,
                    source_revision_id=index.source_revision_id,
                )
                index_row = db.get(
                    KnowledgeIndexSourceBindingDB,
                    index.knowledge_index_id,
                )
                if index_row is None:
                    index_row = KnowledgeIndexSourceBindingDB(
                        **index.__dict__
                    )
                    db.add(index_row)
                    db.flush()
                else:
                    self._assert_index_projection_binding(index_row, index)

                run_row = db.get(
                    KnowledgeIndexRunSourceBindingDB,
                    run.index_run_id,
                )
                if run_row is not None:
                    self._assert_run_projection_binding(run_row, run)
                    if run_row.status == "completed":
                        if (
                            not run_row.artifacts_verified
                            or run_row.artifact_manifest_digest
                            != run.artifact_manifest_digest
                        ):
                            raise SourceControlPersistenceError(
                                "source_control_index_run_projection_conflict"
                            )
                        return (
                            index_binding_record(index_row),
                            index_run_binding_record(run_row),
                        )
                    if run_row.status != "pending":
                        raise SourceControlPersistenceError(
                            "source_control_index_run_projection_conflict"
                        )
                    run_row.status = "completed"
                    run_row.artifact_manifest_digest = (
                        run.artifact_manifest_digest
                    )
                    run_row.artifacts_verified = True
                    run_row.lock_version += 1
                    run_row.completed_at_epoch = run.completed_at_epoch
                else:
                    run_row = KnowledgeIndexRunSourceBindingDB(
                        **run.__dict__
                    )
                    db.add(run_row)

                if index_row.status not in {"pending", "completed"}:
                    raise SourceControlPersistenceError(
                        "source_control_index_projection_conflict"
                    )
                if index_row.status == "completed":
                    index_row.lock_version += 1
                index_row.status = "completed"
                index_row.artifact_manifest_digest = (
                    index.artifact_manifest_digest
                )
                index_row.activation_requested = True
                index_row.updated_at_epoch = index.updated_at_epoch
                db.flush()
                projected_index = index_binding_record(index_row)
                projected_run = index_run_binding_record(run_row)
            return projected_index, projected_run

    @staticmethod
    def _assert_index_projection_binding(
        row: KnowledgeIndexSourceBindingDB,
        expected: KnowledgeIndexBindingRecord,
    ) -> None:
        fields = (
            "tenant_id",
            "project_id",
            "owner_id",
            "connection_id",
            "source_revision_id",
            "policy_snapshot_id",
            "policy_snapshot_digest",
            "index_contract_version",
        )
        if any(
            getattr(row, field) != getattr(expected, field)
            for field in fields
        ):
            raise SourceControlPersistenceError(
                "source_control_index_projection_binding_mismatch"
            )

    @staticmethod
    def _assert_run_projection_binding(
        row: KnowledgeIndexRunSourceBindingDB,
        expected: KnowledgeIndexRunBindingRecord,
    ) -> None:
        fields = (
            "knowledge_index_id",
            "tenant_id",
            "project_id",
            "owner_id",
            "source_revision_id",
            "policy_snapshot_id",
            "policy_snapshot_digest",
        )
        if any(
            getattr(row, field) != getattr(expected, field)
            for field in fields
        ):
            raise SourceControlPersistenceError(
                "source_control_index_run_projection_binding_mismatch"
            )

    def complete_index_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        index_run_id: str,
        expected_run_lock_version: int,
        expected_index_lock_version: int,
        artifact_manifest_digest: str,
        completed_at_epoch: float,
    ) -> tuple[
        KnowledgeIndexBindingRecord,
        KnowledgeIndexRunBindingRecord,
    ]:
        require_sha256_digest(artifact_manifest_digest)
        with Session(self._engine) as db:
            with db.begin():
                run = db.exec(
                    select(KnowledgeIndexRunSourceBindingDB).where(
                        KnowledgeIndexRunSourceBindingDB.index_run_id
                        == index_run_id,
                        KnowledgeIndexRunSourceBindingDB.tenant_id
                        == tenant_id,
                        KnowledgeIndexRunSourceBindingDB.project_id
                        == project_id,
                        KnowledgeIndexRunSourceBindingDB.owner_id == owner_id,
                    )
                ).first()
                if run is None:
                    raise SourceControlPersistenceError(
                        "source_control_index_run_not_found"
                    )
                index = require_scoped_index(
                    db,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    knowledge_index_id=run.knowledge_index_id,
                )
                if (
                    run.lock_version != expected_run_lock_version
                    or index.lock_version != expected_index_lock_version
                ):
                    raise SourceControlPersistenceError(
                        "source_control_version_conflict"
                    )
                run_result = db.execute(
                    update(KnowledgeIndexRunSourceBindingDB)
                    .where(
                        KnowledgeIndexRunSourceBindingDB.index_run_id
                        == index_run_id,
                        KnowledgeIndexRunSourceBindingDB.lock_version
                        == expected_run_lock_version,
                    )
                    .values(
                        status="completed",
                        artifact_manifest_digest=artifact_manifest_digest,
                        artifacts_verified=True,
                        lock_version=expected_run_lock_version + 1,
                        completed_at_epoch=completed_at_epoch,
                    )
                )
                index_result = db.execute(
                    update(KnowledgeIndexSourceBindingDB)
                    .where(
                        KnowledgeIndexSourceBindingDB.knowledge_index_id
                        == index.knowledge_index_id,
                        KnowledgeIndexSourceBindingDB.lock_version
                        == expected_index_lock_version,
                    )
                    .values(
                        status="completed",
                        artifact_manifest_digest=artifact_manifest_digest,
                        activation_requested=True,
                        lock_version=expected_index_lock_version + 1,
                        updated_at_epoch=completed_at_epoch,
                    )
                )
                if run_result.rowcount != 1 or index_result.rowcount != 1:
                    raise SourceControlPersistenceError(
                        "source_control_version_conflict"
                    )
            with Session(self._engine) as loaded:
                refreshed_run = loaded.get(
                    KnowledgeIndexRunSourceBindingDB,
                    index_run_id,
                )
                refreshed_index = loaded.get(
                    KnowledgeIndexSourceBindingDB,
                    run.knowledge_index_id,
                )
                if refreshed_run is None or refreshed_index is None:
                    raise SourceControlPersistenceError(
                        "source_control_index_completion_inconsistent"
                    )
                return (
                    index_binding_record(refreshed_index),
                    index_run_binding_record(refreshed_run),
                )

    def activate_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        knowledge_index_id: str,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
        expected_generation: int,
        action: str,
    ) -> ActiveKnowledgeIndexRecord:
        if action not in {"activate", "rollback", "reconcile"}:
            raise SourceControlPersistenceError(
                "source_control_activation_action_invalid"
            )
        require_sha256_digest(current_policy_snapshot_digest)
        active_index_id = derive_active_index_id(
            tenant_id=tenant_id,
            project_id=project_id,
            connection_id=connection_id,
        )
        try:
            with Session(self._engine) as db:
                with db.begin():
                    index = require_scoped_index(
                        db,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        owner_id=owner_id,
                        knowledge_index_id=knowledge_index_id,
                    )
                    if (
                        index.connection_id != connection_id
                        or index.status != "completed"
                        or index.source_revision_id
                        != current_source_revision_id
                        or index.policy_snapshot_digest
                        != current_policy_snapshot_digest
                    ):
                        raise SourceControlPersistenceError(
                            "source_control_index_activation_stale"
                        )
                    verified_run = db.exec(
                        select(KnowledgeIndexRunSourceBindingDB).where(
                            KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                            == knowledge_index_id,
                            KnowledgeIndexRunSourceBindingDB.status
                            == "completed",
                            KnowledgeIndexRunSourceBindingDB.artifacts_verified
                            == True,  # noqa: E712
                            KnowledgeIndexRunSourceBindingDB.artifact_manifest_digest
                            == index.artifact_manifest_digest,
                        )
                    ).first()
                    if verified_run is None:
                        raise SourceControlPersistenceError(
                            "source_control_index_artifacts_unverified"
                        )
                    active = db.get(ActiveKnowledgeIndexDB, active_index_id)
                    if active is None:
                        if expected_generation != 0:
                            raise SourceControlPersistenceError(
                                "source_control_generation_conflict"
                            )
                        generation = 1
                        previous_id = None
                        active = ActiveKnowledgeIndexDB(
                            active_index_id=active_index_id,
                            tenant_id=tenant_id,
                            project_id=project_id,
                            owner_id=owner_id,
                            connection_id=connection_id,
                            source_revision_id=index.source_revision_id,
                            policy_snapshot_digest=index.policy_snapshot_digest,
                            knowledge_index_id=knowledge_index_id,
                            previous_knowledge_index_id=None,
                            generation=generation,
                            updated_at_epoch=float(self._clock()),
                        )
                        db.add(active)
                        db.flush()
                    else:
                        if active.generation != expected_generation:
                            raise SourceControlPersistenceError(
                                "source_control_generation_conflict"
                            )
                        if active.knowledge_index_id == knowledge_index_id:
                            db.execute(
                                update(KnowledgeIndexSourceBindingDB)
                                .where(
                                    KnowledgeIndexSourceBindingDB.knowledge_index_id
                                    == knowledge_index_id
                                )
                                .values(activation_requested=False)
                            )
                            return active_index_record(active)
                        previous_id = active.knowledge_index_id
                        generation = expected_generation + 1
                        result = db.execute(
                            update(ActiveKnowledgeIndexDB)
                            .where(
                                ActiveKnowledgeIndexDB.active_index_id
                                == active_index_id,
                                ActiveKnowledgeIndexDB.generation
                                == expected_generation,
                            )
                            .values(
                                source_revision_id=index.source_revision_id,
                                policy_snapshot_digest=index.policy_snapshot_digest,
                                knowledge_index_id=knowledge_index_id,
                                previous_knowledge_index_id=previous_id,
                                generation=generation,
                                updated_at_epoch=float(self._clock()),
                            )
                        )
                        if result.rowcount != 1:
                            raise SourceControlPersistenceError(
                                "source_control_generation_conflict"
                            )
                    db.execute(
                        update(KnowledgeIndexSourceBindingDB)
                        .where(
                            KnowledgeIndexSourceBindingDB.knowledge_index_id
                            == knowledge_index_id
                        )
                        .values(activation_requested=False)
                    )
                    if self._activation_fault_hook is not None:
                        self._activation_fault_hook()
                    event = ActiveKnowledgeIndexEventDB(
                        event_id=stable_id(
                            "event",
                            {
                                "action": action,
                                "active_index_id": active_index_id,
                                "generation": generation,
                                "knowledge_index_id": knowledge_index_id,
                            },
                        ),
                        active_index_id=active_index_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        connection_id=connection_id,
                        action=action,
                        from_knowledge_index_id=previous_id,
                        to_knowledge_index_id=knowledge_index_id,
                        generation=generation,
                        occurred_at_epoch=float(self._clock()),
                    )
                    db.add(event)
                    event_type = {
                        "activate": "index_activated",
                        "rollback": "index_rolled_back",
                        "reconcile": "index_reconciled",
                    }[action]
                    db.add(
                        SourceControlJobEventOutboxDB(
                            event_id=event.event_id,
                            tenant_id=tenant_id,
                            project_id=project_id,
                            resource_id=connection_id,
                            job_id=knowledge_index_id,
                            event_type=event_type,
                            status="completed",
                            reason_code=None,
                            trace_id=event.event_id,
                            occurred_at_epoch=event.occurred_at_epoch,
                            created_at_epoch=event.occurred_at_epoch,
                        )
                    )
                return self.get_active_index(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    connection_id=connection_id,
                ) or self._missing_active()
        except IntegrityError:
            raise SourceControlPersistenceError(
                "source_control_generation_conflict"
            ) from None

    def get_active_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
    ) -> ActiveKnowledgeIndexRecord | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(ActiveKnowledgeIndexDB).where(
                    ActiveKnowledgeIndexDB.tenant_id == tenant_id,
                    ActiveKnowledgeIndexDB.project_id == project_id,
                    ActiveKnowledgeIndexDB.owner_id == owner_id,
                    ActiveKnowledgeIndexDB.connection_id == connection_id,
                )
            ).first()
            return None if row is None else active_index_record(row)

    def project_index_lifecycle(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        knowledge_index_id: str,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
    ) -> IndexLifecycleProjection:
        with Session(self._engine) as db:
            index = require_scoped_index(
                db,
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=owner_id,
                knowledge_index_id=knowledge_index_id,
            )
            active = db.exec(
                select(ActiveKnowledgeIndexDB).where(
                    ActiveKnowledgeIndexDB.tenant_id == tenant_id,
                    ActiveKnowledgeIndexDB.project_id == project_id,
                    ActiveKnowledgeIndexDB.owner_id == owner_id,
                    ActiveKnowledgeIndexDB.connection_id == connection_id,
                )
            ).first()
            verified = db.exec(
                select(KnowledgeIndexRunSourceBindingDB).where(
                    KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                    == knowledge_index_id,
                    KnowledgeIndexRunSourceBindingDB.status == "completed",
                    KnowledgeIndexRunSourceBindingDB.artifacts_verified
                    == True,  # noqa: E712
                )
            ).first()
            return derive_index_lifecycle(
                binding=index_binding_record(index),
                active=None if active is None else active_index_record(active),
                current_source_revision_id=current_source_revision_id,
                current_policy_snapshot_digest=current_policy_snapshot_digest,
                has_verified_run=verified is not None,
            )

    def reconcile_activation(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
        connection_id: str,
        current_source_revision_id: str,
        current_policy_snapshot_digest: str,
    ) -> ActivationReconciliationResult:
        with Session(self._engine) as db:
            candidate = db.exec(
                select(KnowledgeIndexSourceBindingDB)
                .where(
                    KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                    KnowledgeIndexSourceBindingDB.project_id == project_id,
                    KnowledgeIndexSourceBindingDB.owner_id == owner_id,
                    KnowledgeIndexSourceBindingDB.connection_id
                    == connection_id,
                    KnowledgeIndexSourceBindingDB.source_revision_id
                    == current_source_revision_id,
                    KnowledgeIndexSourceBindingDB.policy_snapshot_digest
                    == current_policy_snapshot_digest,
                    KnowledgeIndexSourceBindingDB.status == "completed",
                    KnowledgeIndexSourceBindingDB.activation_requested
                    == True,  # noqa: E712
                )
                .order_by(
                    KnowledgeIndexSourceBindingDB.updated_at_epoch.desc(),
                    KnowledgeIndexSourceBindingDB.knowledge_index_id.desc(),
                )
            ).first()
        active = self.get_active_index(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            connection_id=connection_id,
        )
        if candidate is None:
            return ActivationReconciliationResult(
                repaired=False,
                reason_code="no_pending_activation",
                active=active,
            )
        expected_generation = 0 if active is None else active.generation
        repaired = self.activate_index(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            connection_id=connection_id,
            knowledge_index_id=candidate.knowledge_index_id,
            current_source_revision_id=current_source_revision_id,
            current_policy_snapshot_digest=current_policy_snapshot_digest,
            expected_generation=expected_generation,
            action="reconcile",
        )
        return ActivationReconciliationResult(
            repaired=True,
            reason_code="pending_activation_repaired",
            active=repaired,
        )

    def list_activation_events(
        self, *, active_index_id: str
    ) -> tuple[ActiveKnowledgeIndexEventRecord, ...]:
        with Session(self._engine) as db:
            rows = db.exec(
                select(ActiveKnowledgeIndexEventDB)
                .where(
                    ActiveKnowledgeIndexEventDB.active_index_id
                    == active_index_id
                )
                .order_by(ActiveKnowledgeIndexEventDB.generation)
            ).all()
            return tuple(activation_event_record(row) for row in rows)

    @staticmethod
    def _missing_active() -> ActiveKnowledgeIndexRecord:
        raise SourceControlPersistenceError(
            "source_control_activation_inconsistent"
        )
