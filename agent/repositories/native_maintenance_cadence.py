"""Durable Hub cadence/outbox with bounded, immutable run reservations."""

import math
from dataclasses import dataclass

from sqlalchemy import func, select, text

from agent.db_models.native_maintenance import NativeMaintenanceCadenceDB
from agent.repositories.sqlalchemy_support import SQLAlchemyStoreSupport, stable_row_id
from agent.services.native_result_maintenance_policy import MaintenanceSchedule, maintenance_request
from agent.services.workflow_runtime.lease_fencing import validate_sqlalchemy_lease


@dataclass(frozen=True)
class MaintenanceReservation:
    schedule: MaintenanceSchedule
    run_id: str


class SQLMaintenanceCadenceRepository(SQLAlchemyStoreSupport):
    def _lock_sqlite(self, session):
        if self._dialect_name == 'sqlite':
            session.execute(text('BEGIN IMMEDIATE'))

    @staticmethod
    def _key(schedule):
        return stable_row_id('native-maintenance-schedule',
                             schedule.tenant_id, schedule.project_id, schedule.schedule_id)

    @staticmethod
    def _validate(row):
        schedule = MaintenanceSchedule.from_mapping(row.policy_manifest)
        if (row.id != SQLMaintenanceCadenceRepository._key(schedule)
                or row.policy_digest != schedule.digest or row.tenant_id != schedule.tenant_id
                or row.project_id != schedule.project_id or row.schedule_id != schedule.schedule_id
                or type(row.ordinal) is not int or row.ordinal < 0
                or type(row.next_due_at) not in (int, float) or not math.isfinite(row.next_due_at)
                or row.next_due_at < 0
                or row.status not in {'idle', 'pending', 'completed', 'failed', 'cancelled'}):
            raise ValueError('native_maintenance_journal_binding_invalid')
        expected = stable_row_id('native-maintenance-run', row.id, row.ordinal, row.policy_digest)
        if row.current_run_id is not None and (row.current_run_id != expected or row.status != 'pending'):
            raise ValueError('native_maintenance_journal_run_invalid')
        return schedule

    def pending(self, *, limit=64):
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError('native_maintenance_journal_limit_invalid')
        with self._read_session() as session:
            rows = session.execute(select(NativeMaintenanceCadenceDB).where(
                NativeMaintenanceCadenceDB.current_run_id.is_not(None))
                .order_by(NativeMaintenanceCadenceDB.updated_at, NativeMaintenanceCadenceDB.id).limit(limit)).scalars()
            return tuple(MaintenanceReservation(self._validate(row), row.current_run_id) for row in rows)

    def reserve(self, schedule, *, now):
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise ValueError('native_maintenance_clock_invalid')
        key = self._key(schedule)
        with self._transaction() as session:
            self._lock_sqlite(session)
            if self._dialect_name == 'postgresql':
                from sqlalchemy.dialects.postgresql import insert

                # Serialize the bounded global outbox admission across Hub
                # processes, including initially absent schedule rows.
                session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': 518032946724153})
                session.execute(insert(NativeMaintenanceCadenceDB).values(
                    id=key, tenant_id=schedule.tenant_id, project_id=schedule.project_id,
                    schedule_id=schedule.schedule_id, policy_digest=schedule.digest,
                    policy_manifest=schedule.to_mapping(), ordinal=0, next_due_at=now,
                    status='idle', reason_code='', updated_at=now).on_conflict_do_nothing(index_elements=['id']))
            row = session.execute(self._for_update(select(NativeMaintenanceCadenceDB).where(
                NativeMaintenanceCadenceDB.id == key))).scalar_one_or_none()
            if row is None:
                row = NativeMaintenanceCadenceDB(id=key, tenant_id=schedule.tenant_id,
                    project_id=schedule.project_id, schedule_id=schedule.schedule_id,
                    policy_digest=schedule.digest, policy_manifest=schedule.to_mapping(),
                    next_due_at=now, updated_at=now)
                session.add(row)
            self._validate(row)
            if row.current_run_id is not None:
                return MaintenanceReservation(MaintenanceSchedule.from_mapping(row.policy_manifest), row.current_run_id)
            if row.policy_digest != schedule.digest:
                row.policy_digest, row.policy_manifest = schedule.digest, schedule.to_mapping()
                row.next_due_at = now
            if now < row.next_due_at:
                return None
            count = session.execute(select(func.count()).select_from(NativeMaintenanceCadenceDB).where(
                NativeMaintenanceCadenceDB.current_run_id.is_not(None))).scalar_one()
            if count >= 64:
                raise ValueError('native_maintenance_pending_capacity_exceeded')
            row.ordinal += 1
            row.current_run_id = stable_row_id('native-maintenance-run', key, row.ordinal, schedule.digest)
            row.status, row.reason_code = 'pending', ''
            row.next_due_at, row.updated_at = now + schedule.interval_seconds, now
            return MaintenanceReservation(schedule, row.current_run_id)

    def require_current(self, schedule, *, run_id):
        with self._read_session() as session:
            row = session.get(NativeMaintenanceCadenceDB, self._key(schedule))
            if row is None:
                raise ValueError('native_maintenance_reservation_missing')
            self._validate(row)
            if row.current_run_id != run_id or row.policy_digest != schedule.digest:
                raise ValueError('native_maintenance_reservation_not_current')

    def finish(self, reservation, *, status, reason_code='', now, lease=None):
        if status not in {'completed', 'failed', 'cancelled'}:
            raise ValueError('native_maintenance_journal_status_invalid')
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise ValueError('native_maintenance_clock_invalid')
        with self._transaction() as session:
            self._lock_sqlite(session)
            if lease is not None:
                plan = maintenance_request(reservation.schedule, reservation.run_id).plan
                if (lease.checkpoint.run_id != reservation.run_id
                        or lease.checkpoint.tenant_id != plan.tenant_id
                        or lease.checkpoint.plan_hash != plan.plan_hash):
                    raise ValueError('native_maintenance_retirement_lease_mismatch')
                validate_sqlalchemy_lease(session, lease, lease.checkpoint)
            row = session.execute(self._for_update(select(NativeMaintenanceCadenceDB).where(
                NativeMaintenanceCadenceDB.id == self._key(reservation.schedule)))).scalar_one_or_none()
            if row is None:
                raise ValueError('native_maintenance_journal_missing')
            self._validate(row)
            if row.current_run_id is None and row.last_run_id == reservation.run_id:
                return  # A stale recorder cannot reopen or overwrite a terminal outcome.
            if row.current_run_id != reservation.run_id or row.policy_digest != reservation.schedule.digest:
                raise ValueError('native_maintenance_journal_receipt_stale')
            row.current_run_id, row.last_run_id = None, reservation.run_id
            row.status, row.reason_code, row.updated_at = status, reason_code, now
