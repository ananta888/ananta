"""Shared hard SQL scope for Result-Memory reads and lifecycle mutations."""

from sqlmodel import select

from agent.db_models import MemoryEntryDB, TaskDB
from ananta_contracts.memory_access import MemoryScope, MemorySensitivity


def result_memory_scope_query(namespace, scope, sensitivity_ceiling):
    namespace.key(scope)
    metadata = MemoryEntryDB.memory_metadata
    statement = select(MemoryEntryDB).join(TaskDB, MemoryEntryDB.task_id == TaskDB.id).where(
        TaskDB.tenant_id == namespace.tenant_id,
        TaskDB.project_id == namespace.workspace_id,
        metadata["memory_security_schema"].as_string() == "ananta.memory-security.v1",
        metadata["memory_security"]["scope"].as_string() == scope.value,
    )
    for field, value in namespace.bindings(scope).items():
        column = metadata["memory_namespace"][field].as_string()
        statement = statement.where(column.is_(None) if value is None else column == value)
    if scope in (MemoryScope.run, MemoryScope.task):
        statement = statement.where(MemoryEntryDB.task_id == namespace.task_id)
        if namespace.goal_id is None:
            statement = statement.where(MemoryEntryDB.goal_id.is_(None), TaskDB.goal_id.is_(None))
        else:
            statement = statement.where(MemoryEntryDB.goal_id == namespace.goal_id,
                                        TaskDB.goal_id == namespace.goal_id)
    if scope == MemoryScope.goal:
        statement = statement.where(MemoryEntryDB.goal_id == namespace.goal_id,
                                    TaskDB.goal_id == namespace.goal_id)
    levels = tuple(MemorySensitivity)
    allowed = [level.value for level in levels[:levels.index(sensitivity_ceiling) + 1]]
    statement = statement.where(metadata["memory_security"]["sensitivity"].as_string().in_(allowed))
    return statement
