"""Transactional Hub retirement journal and fair bounded candidate cursor."""

import math
from contextlib import contextmanager

from sqlalchemy import and_, exists, or_, select, text

from agent.db_models.native_dispatch import NativeDispatchDB
from agent.db_models.native_result_retirement import (
    NativeResultRetirementCursorDB,
    NativeResultRetirementManifestDB,
    NativeResultRetirementMarkerDB,
)
from agent.db_models.tasks import TaskDB
from agent.repositories.sqlalchemy_support import SQLAlchemyStoreSupport, stable_row_id
from ananta_contracts.native_context_bundle import native_context_digest


def retirement_marker_key(*, tenant_id, project_id, owner, task_id):
    return stable_row_id('native-result-retired', tenant_id, project_id,
        owner['worker_id'], owner['worker_url'], owner['store_binding_id'], task_id)


class SQLNativeResultRetirementRepository(SQLAlchemyStoreSupport):
    @contextmanager
    def transaction(self):
        with self._transaction() as session:
            self.lock_sqlite(session)
            if self._dialect_name == 'postgresql':
                for setting, ceiling in (('lock_timeout', 5000), ('statement_timeout', 15000)):
                    current = int(session.execute(text(
                        'SELECT setting FROM pg_settings WHERE name=:name'), {'name': setting}).scalar_one())
                    session.execute(text('SELECT set_config(:name, :value, true)'),
                        {'name': setting, 'value': str(min(current or ceiling, ceiling))})
            yield session

    def lock_sqlite(self, session):
        if self._dialect_name == 'sqlite':
            session.execute(text('BEGIN IMMEDIATE'))

    def candidates(self, *, session, tenant_id, project_id, owner, exclude_task_id, limit):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('native_result_retirement_scan_invalid')
        owner_digest = native_context_digest(owner)
        key = stable_row_id('native-result-retirement-cursor', tenant_id, project_id, owner_digest)
        if self._dialect_name == 'postgresql':
            # Serialize initially absent scoped cursors without an independent
            # orchestration loop or a process-local lock assumption.
            session.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                            {'key': int(owner_digest[:15], 16)})
        cursor = session.execute(self._for_update(select(NativeResultRetirementCursorDB).where(
            NativeResultRetirementCursorDB.id == key))).scalar_one_or_none()
        if cursor is not None and (cursor.tenant_id != tenant_id or cursor.project_id != project_id
                or cursor.owner_digest != owner_digest or not math.isfinite(cursor.after_updated_at)
                or not isinstance(cursor.after_id, str)):
            raise ValueError('native_result_retirement_cursor_invalid')
        confirmed = exists(select(NativeResultRetirementMarkerDB.id).where(
            NativeResultRetirementMarkerDB.tenant_id == tenant_id,
            NativeResultRetirementMarkerDB.project_id == project_id,
            NativeResultRetirementMarkerDB.worker_id == owner['worker_id'],
            NativeResultRetirementMarkerDB.worker_url == owner['worker_url'],
            NativeResultRetirementMarkerDB.store_binding_id == owner['store_binding_id'],
            NativeResultRetirementMarkerDB.task_id == NativeDispatchDB.task_id))
        base = select(NativeDispatchDB.task_id, NativeDispatchDB.updated_at, NativeDispatchDB.id).join(
            TaskDB, and_(TaskDB.id == NativeDispatchDB.task_id, TaskDB.tenant_id == NativeDispatchDB.tenant_id)).where(
            NativeDispatchDB.tenant_id == tenant_id, TaskDB.project_id == project_id,
            NativeDispatchDB.worker_id == owner['worker_id'], NativeDispatchDB.worker_url == owner['worker_url'],
            NativeDispatchDB.state == 'accepted', TaskDB.status.in_(('completed', 'failed', 'cancelled')),
            TaskDB.id != exclude_task_id, ~confirmed).order_by(NativeDispatchDB.updated_at, NativeDispatchDB.id)
        after = (base.where(or_(NativeDispatchDB.updated_at > cursor.after_updated_at,
                 and_(NativeDispatchDB.updated_at == cursor.after_updated_at, NativeDispatchDB.id > cursor.after_id)))
                 if cursor is not None else base)
        rows = session.execute(after.limit(limit)).all()
        if not rows and cursor is not None:
            rows = session.execute(base.limit(limit)).all()
        if rows:
            if cursor is None:
                cursor = NativeResultRetirementCursorDB(id=key, tenant_id=tenant_id, project_id=project_id,
                    owner_digest=owner_digest, after_updated_at=rows[-1][1], after_id=rows[-1][2])
            else:
                cursor.after_updated_at, cursor.after_id = rows[-1][1], rows[-1][2]
            session.add(cursor)
        return tuple(row[0] for row in rows)

    @staticmethod
    def require_manifest_row(row, task):
        if (row is None or row.id != task.id or row.tenant_id != task.tenant_id or row.project_id != task.project_id
                or row.state not in {'issued', 'confirmed'} or type(row.issued_at) not in (int, float)
                or not math.isfinite(row.issued_at) or row.manifest_digest != native_context_digest(row.manifest)):
            raise ValueError('native_result_retirement_journal_binding_denied')
        return row

    def pending_completed(self, *, tenant_id=None, project_id=None, limit):
        with self._read_session() as session:
            statement = select(NativeResultRetirementManifestDB.id).join(
                TaskDB, TaskDB.id == NativeResultRetirementManifestDB.id).join(NativeDispatchDB, and_(
                NativeDispatchDB.task_id == TaskDB.id, NativeDispatchDB.tenant_id == TaskDB.tenant_id)).where(
                NativeDispatchDB.state == 'accepted',
                NativeResultRetirementManifestDB.state == 'issued', TaskDB.status == 'completed')
            if tenant_id is not None:
                statement = statement.where(NativeResultRetirementManifestDB.tenant_id == tenant_id,
                                            NativeResultRetirementManifestDB.project_id == project_id)
            return tuple(session.execute(statement
                .order_by(NativeResultRetirementManifestDB.issued_at, NativeResultRetirementManifestDB.id)
                .limit(limit)).scalars())

    def block_acknowledgement(self, task_id):
        with self.transaction() as session:
            row = session.execute(self._for_update(select(NativeResultRetirementManifestDB).where(
                NativeResultRetirementManifestDB.id == task_id))).scalar_one_or_none()
            if row is not None and row.state == 'issued':
                row.state, row.reason_code = 'blocked', 'native_result_retirement_acknowledgement_denied'
                session.add(row)
