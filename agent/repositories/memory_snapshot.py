"""Atomic immutable snapshot/audit boundaries with mandatory SQL project scope."""

import json
import time
from dataclasses import asdict

from sqlalchemy import select

from agent.db_models.memory_audit import MemoryAuditEventDB
from agent.db_models.memory_policy import MemoryRunPolicyDB
from agent.db_models.memory_snapshot import MemorySnapshotDB
from agent.db_models.tasks import TaskDB
from agent.repositories.memory_audit import SQLMemoryAuditRepository, validate_memory_audit_event
from agent.repositories.result_memory_snapshot_source import MemorySnapshotSourcePort, SQLResultMemorySnapshotSource
from agent.repositories.sqlalchemy_support import SQLAlchemyStoreSupport
from agent.services.memory_diff_service import memory_observation_digest
from agent.services.memory_policy_codec import decode_policy
from agent.services.memory_snapshot_service import snapshot_capture_grants, validate_snapshot_entries
from ananta_contracts.memory_access import MemorySecurityMetadata, MemorySensitivity, require_memory_timestamp


class SQLMemorySnapshotRepository:
    def __init__(self, engine, *, clock=time.time, source: MemorySnapshotSourcePort | None = None):
        self._sql = SQLAlchemyStoreSupport(engine)
        self._audit = SQLMemoryAuditRepository(engine)
        self._clock = clock
        self._source = source if source is not None else SQLResultMemorySnapshotSource()

    def capture(self, *, policy, phase, scope_outcome):
        grants = snapshot_capture_grants(policy, phase, scope_outcome)
        levels = tuple(MemorySensitivity)
        ceiling = max((grant.sensitivity_ceiling for grant in grants), key=levels.index)
        namespace = policy.namespace
        now = self._clock()
        require_memory_timestamp(now, 'snapshot_clock')
        identity = 'memory-snapshot-' + memory_observation_digest(
            [namespace.tenant_id, namespace.run_id, 'hub-result-memory', 'task', phase])
        with self._sql._transaction() as session:
            self._audit.lock_tenant(session, namespace.tenant_id)
            admitted = session.get(MemoryRunPolicyDB, namespace.run_id)
            task = session.get(TaskDB, namespace.task_id)
            if (admitted is None or admitted.policy_digest != policy.digest
                    or decode_policy(admitted.policy) != policy
                    or task is None or (task.tenant_id, task.project_id, task.goal_id) != (
                        namespace.tenant_id, namespace.workspace_id, namespace.goal_id)
                    or phase == 'before' and (admitted.revoked or now >= policy.expires_at)):
                raise ValueError('memory_snapshot_admission_invalid')
            existing = session.get(MemorySnapshotDB, identity)
            if existing is not None:
                snapshot = self._validated(session, existing)
                if snapshot['policy_digest'] != policy.digest or snapshot['scope_outcome'] != scope_outcome:
                    raise ValueError('memory_snapshot_replay_binding_invalid')
                return snapshot
            if phase == 'after':
                before_id = 'memory-snapshot-' + memory_observation_digest(
                    [namespace.tenant_id, namespace.run_id, 'hub-result-memory', 'task', 'before'])
                before = session.get(MemorySnapshotDB, before_id)
                if before is None or self._validated(session, before)['policy_digest'] != policy.digest:
                    raise ValueError('memory_snapshot_before_required')
            entries = self._source.observe(session, namespace, sensitivity_ceiling=ceiling)
            entries = validate_snapshot_entries(entries, sensitivity_ceiling=ceiling)
            body = {'schema': 'ananta.memory-snapshot.v1', 'snapshot_id': identity,
                    'run_id': namespace.run_id, 'channel_id': 'hub-result-memory', 'scope': 'task',
                    'phase': phase, 'namespace': asdict(namespace), 'policy_digest': policy.digest,
                    'scope_outcome': scope_outcome, 'entries': entries, 'captured_at': now}
            digest = memory_observation_digest(body)
            payload = {'schema_version': 'ananta.memory-audit-observation.v1',
                'actor': {'tenant_id': namespace.tenant_id, 'subject': 'hub:memory-observer'},
                'run_id': namespace.run_id, 'operation': 'snapshot', 'allowed': True,
                'rule_id': 'memory.scope-observation.v1', 'reason_code': 'memory_snapshot_captured',
                'policy_revision': policy.policy_revision, 'policy_digest': policy.digest,
                'trace_id': policy.trace_id, 'event_kind': 'memory_snapshot',
                'snapshot_id': identity, 'snapshot_digest': digest,
                'target': {'channel_id': 'hub-result-memory', 'namespace': asdict(namespace),
                           'security': MemorySecurityMetadata(sensitivity=ceiling).to_mapping()}}
            receipt = self._audit.append_in_transaction(session, payload, created_at=now,
                                                        retention_until=now + 90 * 86400)
            row = MemorySnapshotDB(id=identity, tenant_id=namespace.tenant_id,
                project_id=namespace.workspace_id, task_id=namespace.task_id, run_id=namespace.run_id,
                channel_id='hub-result-memory', scope='task', phase=phase, snapshot=body,
                snapshot_digest=digest, audit_event_id=receipt['event_id'])
            session.add(row)
            session.flush()
            return {**json.loads(json.dumps(body)), 'audit_event_id': row.audit_event_id}

    @staticmethod
    def _validated(session, row):
        body = row.snapshot
        if (memory_observation_digest(body) != row.snapshot_digest or body['snapshot_id'] != row.id
                or (row.tenant_id, row.project_id, row.task_id, row.run_id, row.channel_id, row.scope, row.phase) != (
                    body['namespace']['tenant_id'], body['namespace']['workspace_id'], body['namespace']['task_id'],
                    body['run_id'], body['channel_id'], body['scope'], body['phase'])):
            raise ValueError('memory_snapshot_integrity_invalid')
        event = session.get(MemoryAuditEventDB, row.audit_event_id)
        validate_memory_audit_event(event)
        if (event.tenant_id != row.tenant_id or event.payload.get('snapshot_id') != row.id
                or event.payload.get('snapshot_digest') != row.snapshot_digest):
            raise ValueError('memory_snapshot_audit_binding_invalid')
        return {**json.loads(json.dumps(body)), 'audit_event_id': row.audit_event_id}

    def read_run(self, *, tenant_id, project_id, query):
        query.assert_valid()
        if not query.run_id:
            raise ValueError('memory_snapshot_run_required')
        statement = select(MemorySnapshotDB).where(MemorySnapshotDB.tenant_id == tenant_id,
            MemorySnapshotDB.project_id == project_id, MemorySnapshotDB.run_id == query.run_id)
        for field, value in (('task_id', query.task_id), ('channel_id', query.channel_id)):
            if value is not None:
                statement = statement.where(getattr(MemorySnapshotDB, field) == value)
        if query.scope is not None:
            statement = statement.where(MemorySnapshotDB.scope == query.scope.value)
        with self._sql._read_session() as session:
            rows = session.execute(statement.order_by(MemorySnapshotDB.channel_id, MemorySnapshotDB.phase).limit(65))
            records = rows.scalars().all()
            if len(records) > 64:
                raise ValueError('memory_snapshot_observation_limit')
            validated = [self._validated(session, row) for row in records]
            return [record for record in validated if query.trace_id is None
                    or session.get(MemoryAuditEventDB, record['audit_event_id']).trace_id == query.trace_id]
