"""Bounded SQL source adapter for classified, reachable result-memory hashes."""

from typing import Protocol

from sqlalchemy import Text, cast, func, select

from agent.db_models.memory import MemoryEntryDB
from agent.db_models.tasks import TaskDB
from agent.services.memory_diff_service import memory_observation_digest
from ananta_contracts.memory_access import MemorySecurityMetadata, MemorySensitivity

MAX_SNAPSHOT_ENTRIES = 1000
MAX_SNAPSHOT_INPUT_CHARS = 4 * 1024 * 1024


class MemorySnapshotSourcePort(Protocol):
    def observe(self, session, namespace, *, sensitivity_ceiling) -> list[dict]: ...


class SQLResultMemorySnapshotSource:
    def observe(self, session, namespace, *, sensitivity_ceiling):
        metadata = MemoryEntryDB.memory_metadata
        statement = select(MemoryEntryDB).join(TaskDB, TaskDB.id == MemoryEntryDB.task_id).where(
            TaskDB.tenant_id == namespace.tenant_id, TaskDB.project_id == namespace.workspace_id,
            TaskDB.id == namespace.task_id,
            metadata['memory_security_schema'].as_string() == 'ananta.memory-security.v1',
            metadata['memory_security']['scope'].as_string() == 'task')
        levels = tuple(MemorySensitivity)
        allowed = [level.value for level in levels[:levels.index(sensitivity_ceiling) + 1]]
        statement = statement.where(metadata['memory_security']['sensitivity'].as_string().in_(allowed))
        for key, value in namespace.bindings(MemorySecurityMetadata().scope).items():
            column = metadata['memory_namespace'][key].as_string()
            statement = statement.where(column == value if value is not None else column.is_(None))
        # Bound hydration before loading content; aggregate lengths are computed by SQL.
        columns = (MemoryEntryDB.title, MemoryEntryDB.summary, MemoryEntryDB.content,
                   MemoryEntryDB.memory_metadata, MemoryEntryDB.artifact_refs, MemoryEntryDB.retrieval_tags)
        size = sum(func.coalesce(func.length(cast(column, Text)), 0) for column in columns)
        count, chars = session.execute(
            statement.with_only_columns(func.count(), func.coalesce(func.sum(size), 0))).one()
        if count > MAX_SNAPSHOT_ENTRIES or chars > MAX_SNAPSHOT_INPUT_CHARS:
            raise ValueError('memory_snapshot_observation_limit')
        rows = session.execute(statement.order_by(MemoryEntryDB.id).limit(MAX_SNAPSHOT_ENTRIES + 1)).scalars().all()
        if len(rows) > MAX_SNAPSHOT_ENTRIES:
            raise ValueError('memory_snapshot_observation_limit')
        result = []
        for row in rows:
            security = MemorySecurityMetadata.from_mapping(row.memory_metadata['memory_security'])
            content = {key: getattr(row, key)
                       for key in ('title', 'summary', 'content', 'artifact_refs', 'retrieval_tags')}
            result.append({'entry_id': row.id, 'content_digest': memory_observation_digest(content),
                           'metadata_digest': memory_observation_digest(row.memory_metadata),
                           'security': security.to_mapping()})
        return result

