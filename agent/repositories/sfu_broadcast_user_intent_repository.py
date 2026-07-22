"""SQL implementation of atomic SFU broadcast command mutation and audit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent.db_models.sfu_broadcast_user_intents import (
    SfuBroadcastCommandAuditDB,
    SfuBroadcastUserIntentDB,
)
from agent.services.sfu_broadcast_command_repository_port import (
    SfuBroadcastCommandMutation,
    SfuBroadcastCommandMutationResult,
    SfuBroadcastCommandRepositoryConflict,
    SfuBroadcastCommandRepositoryError,
)


class _ConcurrentMutation(RuntimeError):
    pass


class SqlSfuBroadcastUserIntentRepository:
    """Persists a fixed command vocabulary and its audit as one transaction."""

    durable = True
    production_component = True

    def __init__(
        self,
        *,
        db_engine,
        retention: timedelta = timedelta(days=30),
        max_audits_per_tenant: int = 100_000,
        purge_batch_size: int = 500,
    ) -> None:
        if retention <= timedelta(0):
            raise ValueError("retention must be positive")
        if max_audits_per_tenant < 1 or purge_batch_size < 1:
            raise ValueError("retention bounds must be positive")
        self._engine = db_engine
        self._retention = retention
        self._max_audits_per_tenant = max_audits_per_tenant
        self._purge_batch_size = purge_batch_size

    def execute(
        self, mutation: SfuBroadcastCommandMutation
    ) -> SfuBroadcastCommandMutationResult:
        for attempt in range(3):
            try:
                return self._execute_once(mutation)
            except (_ConcurrentMutation, IntegrityError) as exc:
                if attempt == 2:
                    raise SfuBroadcastCommandRepositoryError(
                        "sfu_broadcast_command_concurrent_mutation"
                    ) from exc
        raise SfuBroadcastCommandRepositoryError("sfu_broadcast_command_store_failed")

    def _execute_once(
        self, mutation: SfuBroadcastCommandMutation
    ) -> SfuBroadcastCommandMutationResult:
        with Session(bind=self._engine, expire_on_commit=False) as session:
            with session.begin():
                replay = session.get(
                    SfuBroadcastCommandAuditDB, mutation.operation_id
                )
                if replay is not None:
                    if replay.request_digest != mutation.request_digest:
                        raise SfuBroadcastCommandRepositoryConflict(
                            "sfu_broadcast_operation_id_reused"
                        )
                    return self._result(replay, replayed=True)

                self._purge_for_tenant(
                    session,
                    tenant_id=mutation.tenant_id,
                    tenant_diagnostic_ref=mutation.tenant_diagnostic_ref,
                    now=mutation.now,
                )
                intent = session.execute(
                    select(SfuBroadcastUserIntentDB)
                    .where(
                        SfuBroadcastUserIntentDB.tenant_id == mutation.tenant_id,
                        SfuBroadcastUserIntentDB.room_id == mutation.room_id,
                    )
                    .with_for_update()
                ).scalar_one_or_none()
                current_version = int(intent.version) if intent is not None else 0
                current_state = intent.state if intent is not None else "inactive"

                if not mutation.policy.allowed:
                    audit = self._audit(
                        mutation,
                        intent=intent,
                        accepted=False,
                        version=current_version,
                        state="denied",
                        reason_code=mutation.policy.execution_reason,
                    )
                    session.add(audit)
                    return self._result(audit)

                if mutation.expected_version != current_version:
                    audit = self._audit(
                        mutation,
                        intent=intent,
                        accepted=False,
                        version=current_version,
                        state=current_state,
                        reason_code="sfu_broadcast_version_conflict",
                    )
                    session.add(audit)
                    return self._result(audit)

                target_state, changed, reason_code = self._transition(
                    intent, mutation
                )
                effective_version = current_version
                if changed:
                    effective_version = current_version + 1
                    values = {
                        "state": target_state,
                        "requested_action": mutation.action,
                        "data_saver": self._option(
                            mutation.data_saver,
                            intent.data_saver if intent is not None else None,
                        ),
                        "audio_only": self._option(
                            mutation.audio_only,
                            intent.audio_only if intent is not None else None,
                        ),
                        "quality_preference": self._option(
                            mutation.quality_preference,
                            intent.quality_preference if intent is not None else None,
                        ),
                        "policy_version": mutation.policy.policy_version,
                        "admission_epoch": mutation.policy.admission_epoch,
                        "membership_epoch": mutation.policy.membership_epoch,
                        "version": effective_version,
                        "last_operation_id": mutation.operation_id,
                        "updated_at": mutation.now,
                        "retain_until": mutation.retain_until,
                    }
                    if intent is None:
                        intent = SfuBroadcastUserIntentDB(
                            tenant_id=mutation.tenant_id,
                            room_id=mutation.room_id,
                            created_at=mutation.now,
                            **values,
                        )
                        session.add(intent)
                        session.flush()
                    else:
                        changed_rows = session.execute(
                            update(SfuBroadcastUserIntentDB)
                            .where(
                                SfuBroadcastUserIntentDB.id == intent.id,
                                SfuBroadcastUserIntentDB.version == current_version,
                            )
                            .values(**values)
                        ).rowcount
                        if changed_rows != 1:
                            raise _ConcurrentMutation()
                audit = self._audit(
                    mutation,
                    intent=intent,
                    accepted=True,
                    version=effective_version,
                    state=target_state,
                    reason_code=reason_code,
                )
                session.add(audit)
                return self._result(audit)

    @staticmethod
    def _option(requested, current):
        return current if requested is None else requested

    @staticmethod
    def _transition(intent, mutation: SfuBroadcastCommandMutation):
        state = intent.state if intent is not None else "inactive"
        if mutation.action == "stop":
            if state == "inactive":
                return state, False, "sfu_broadcast_command_noop"
            return "inactive", True, "sfu_broadcast_stopped"
        if mutation.action == "start":
            preference_change = SqlSfuBroadcastUserIntentRepository._preferences_changed(
                intent, mutation
            )
            if state == "active" and not preference_change:
                return state, False, "sfu_broadcast_command_noop"
            return "active", True, "sfu_broadcast_started"
        if not SqlSfuBroadcastUserIntentRepository._preferences_changed(intent, mutation):
            return state, False, "sfu_broadcast_command_noop"
        return state, True, "sfu_broadcast_preferences_updated"

    @staticmethod
    def _preferences_changed(intent, mutation: SfuBroadcastCommandMutation) -> bool:
        if intent is None:
            return any(
                value is not None
                for value in (
                    mutation.data_saver,
                    mutation.audio_only,
                    mutation.quality_preference,
                )
            )
        return any(
            requested is not None and requested != current
            for requested, current in (
                (mutation.data_saver, intent.data_saver),
                (mutation.audio_only, intent.audio_only),
                (mutation.quality_preference, intent.quality_preference),
            )
        )

    @staticmethod
    def _audit(
        mutation: SfuBroadcastCommandMutation,
        *,
        intent,
        accepted: bool,
        version: int,
        state: str,
        reason_code: str,
    ) -> SfuBroadcastCommandAuditDB:
        return SfuBroadcastCommandAuditDB(
            operation_id=mutation.operation_id,
            intent_id=intent.id if intent is not None else None,
            tenant_diagnostic_ref=mutation.tenant_diagnostic_ref,
            room_diagnostic_ref=mutation.room_diagnostic_ref,
            actor_diagnostic_ref=mutation.actor_diagnostic_ref,
            actor_role=mutation.actor_role,
            action=mutation.action,
            reason=mutation.reason,
            outcome="accepted" if accepted else "denied",
            reason_code=reason_code,
            request_digest=mutation.request_digest,
            expected_version=mutation.expected_version,
            effective_version=version,
            state=state,
            data_saver=mutation.data_saver,
            audio_only=mutation.audio_only,
            quality_preference=mutation.quality_preference,
            policy_version=mutation.policy.policy_version,
            admission_epoch=mutation.policy.admission_epoch,
            membership_epoch=mutation.policy.membership_epoch,
            accepted=accepted,
            created_at=mutation.now,
            retain_until=mutation.retain_until,
        )

    @staticmethod
    def _result(
        audit: SfuBroadcastCommandAuditDB, *, replayed: bool = False
    ) -> SfuBroadcastCommandMutationResult:
        return SfuBroadcastCommandMutationResult(
            accepted=bool(audit.accepted),
            effective_version=int(audit.effective_version),
            state=audit.state,
            reason_code=audit.reason_code,
            replayed=replayed,
        )

    def _purge_for_tenant(
        self,
        session: Session,
        *,
        tenant_id: str,
        tenant_diagnostic_ref: str,
        now: datetime,
    ) -> int:
        audit_ids = select(SfuBroadcastCommandAuditDB.operation_id).where(
            SfuBroadcastCommandAuditDB.retain_until <= now
        ).limit(self._purge_batch_size)
        audit_result = session.execute(
            delete(SfuBroadcastCommandAuditDB).where(
                SfuBroadcastCommandAuditDB.operation_id.in_(audit_ids)
            )
        )
        intent_ids = select(SfuBroadcastUserIntentDB.id).where(
            SfuBroadcastUserIntentDB.tenant_id == tenant_id,
            SfuBroadcastUserIntentDB.state == "inactive",
            SfuBroadcastUserIntentDB.retain_until <= now,
        ).limit(self._purge_batch_size)
        intent_result = session.execute(
            delete(SfuBroadcastUserIntentDB).where(
                SfuBroadcastUserIntentDB.id.in_(intent_ids)
            )
        )
        count = int(
            session.scalar(
                select(func.count())
                .select_from(SfuBroadcastCommandAuditDB)
                .where(
                    SfuBroadcastCommandAuditDB.tenant_diagnostic_ref
                    == tenant_diagnostic_ref
                )
            )
            or 0
        )
        overflow = count - self._max_audits_per_tenant + 1
        if overflow > 0:
            oldest = (
                select(SfuBroadcastCommandAuditDB.operation_id)
                .where(
                    SfuBroadcastCommandAuditDB.tenant_diagnostic_ref
                    == tenant_diagnostic_ref
                )
                .order_by(SfuBroadcastCommandAuditDB.created_at)
                .limit(min(overflow, self._purge_batch_size))
            )
            overflow_result = session.execute(
                delete(SfuBroadcastCommandAuditDB).where(
                    SfuBroadcastCommandAuditDB.operation_id.in_(oldest)
                )
            )
        else:
            overflow_result = None
        return sum(
            max(int(result.rowcount or 0), 0)
            for result in (audit_result, intent_result, overflow_result)
            if result is not None
        )

    def purge_expired(self, *, now: datetime | None = None) -> int:
        instant = now or datetime.now(timezone.utc)
        with Session(bind=self._engine) as session:
            with session.begin():
                audits = session.execute(
                    delete(SfuBroadcastCommandAuditDB).where(
                        SfuBroadcastCommandAuditDB.retain_until <= instant
                    )
                ).rowcount
                intents = session.execute(
                    delete(SfuBroadcastUserIntentDB).where(
                        SfuBroadcastUserIntentDB.state == "inactive",
                        SfuBroadcastUserIntentDB.retain_until <= instant,
                    )
                ).rowcount
                return max(int(audits or 0), 0) + max(int(intents or 0), 0)
