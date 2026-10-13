"""Bounded operator-only migration transactions, separate from memory retrieval."""

from copy import deepcopy
from dataclasses import asdict

from sqlalchemy import false, text, update
from sqlmodel import Session, select

from agent.db_models import MemoryEntryDB, MemoryMigrationBackupDB, MemoryRunPolicyDB, TaskDB
from ananta_contracts.memory_access import (
    MemoryNamespace,
    MemorySecurityMetadata,
    MemorySensitivity,
    normalize_legacy_memory_metadata,
    require_memory_identifier,
)


class MemoryMetadataMigrationRepository:
    def __init__(self, engine):
        self._engine = engine

    def _lock(self, session):
        if self._engine.dialect.name not in {"sqlite", "postgresql"}:
            raise ValueError("memory_migration_backend_unsupported")
        if self._engine.dialect.name == "sqlite":
            # Reserve the write transaction before snapshots, including across engines.
            session.exec(update(MemoryEntryDB).where(false()).values(
                memory_metadata=MemoryEntryDB.memory_metadata,
            ))
        else:
            # One Hub migration writer prevents concurrent reuse of a batch ID.
            session.exec(text("SELECT pg_advisory_xact_lock(731909484502116)")).all()

    def migrate(self, *, limit=1000, after_id=None, dry_run=False, batch_id=None, admit_namespaces=False):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(dry_run) is not bool:
            raise ValueError("memory_migration_limit_invalid")
        if type(admit_namespaces) is not bool:
            raise ValueError("memory_migration_admission_invalid")
        if after_id is not None:
            require_memory_identifier(after_id, "after_id")
        if batch_id is not None:
            require_memory_identifier(batch_id, "batch_id")
        statement = select(MemoryEntryDB).order_by(MemoryEntryDB.id).limit(limit)
        if after_id is not None:
            statement = statement.where(MemoryEntryDB.id > after_id)
        updated, unclassified = 0, []
        with Session(self._engine) as session:
            if not dry_run:
                self._lock(session)
                statement = statement.with_for_update()
            if batch_id is not None and session.exec(select(MemoryMigrationBackupDB).where(
                MemoryMigrationBackupDB.batch_id == batch_id,
            ).limit(1)).first() is not None:
                raise ValueError("memory_migration_batch_exists")
            rows = session.exec(statement).all()
            for row in rows:
                try:
                    migrated = normalize_legacy_memory_metadata(row.memory_metadata)
                    if admit_namespaces:
                        migrated = self._admit_owner(session, row, migrated)
                        admission = migrated.get("memory_admission", {})
                        if admission.get("classification") == "quarantined":
                            unclassified.append({"entry_id": row.id, "reason_code": admission["reason_code"]})
                except ValueError:
                    if not dry_run:
                        raise
                    unclassified.append({"entry_id": row.id, "reason_code": "memory_security_metadata_invalid"})
                    continue
                if migrated == row.memory_metadata:
                    continue
                if not dry_run:
                    if batch_id is not None:
                        session.add(MemoryMigrationBackupDB(
                            batch_id=batch_id, entry_id=row.id, task_id=row.task_id, goal_id=row.goal_id,
                            original_metadata=deepcopy(row.memory_metadata or {}),
                            migrated_metadata=deepcopy(migrated),
                        ))
                    row.memory_metadata = migrated
                    session.add(row)
                updated += 1
            cursor = rows[-1].id if len(rows) == limit else None
            if not dry_run:
                session.commit()
        if dry_run:
            return {"scanned": len(rows), "updated": 0, "would_update": updated, "next_cursor": cursor,
                    "unclassified": unclassified, "dry_run": True, "production_release_evidence": False}
        return {"scanned": len(rows), "updated": updated, "next_cursor": cursor}

    @staticmethod
    def _admit_owner(session, row, metadata):
        """Task/project SQL ownership is authority; legacy approval/namespace labels are not."""
        task = session.get(TaskDB, row.task_id) if row.task_id else None
        stored_namespace = metadata.get("memory_namespace")
        if isinstance(stored_namespace, dict) and stored_namespace.get("run_id"):
            from agent.services.memory_policy_codec import decode_policy

            registered = session.get(MemoryRunPolicyDB, stored_namespace["run_id"])
            if registered is not None and registered.policy_digest == metadata.get("memory_policy_digest"):
                issued = decode_policy(registered.policy)
                if (issued.digest == registered.policy_digest and asdict(issued.namespace) == stored_namespace
                        and task is not None and row.goal_id == task.goal_id
                        and (task.tenant_id, task.project_id, task.id, task.goal_id) == (
                            issued.namespace.tenant_id, issued.namespace.workspace_id,
                            issued.namespace.task_id, issued.namespace.goal_id,
                        )):
                    return metadata
        result = dict(metadata)
        # Migration never restores an unchecked legacy policy or scope promotion.
        for field in ("memory_namespace", "memory_policy_digest", "memory_policy_revision"):
            result.pop(field, None)
        result["memory_security"] = MemorySecurityMetadata(
            sensitivity=MemorySensitivity(result["memory_security"]["sensitivity"]),
        ).to_mapping()
        if task is not None and task.tenant_id and task.project_id and row.goal_id == task.goal_id:
            try:
                namespace = MemoryNamespace(tenant_id=task.tenant_id, workspace_id=task.project_id,
                                            task_id=task.id, goal_id=task.goal_id)
            except ValueError:
                pass
            else:
                result["memory_namespace"] = asdict(namespace)
                result["memory_admission"] = {"classification": "admitted_untrusted_task",
                                               "authority": "hub_sql_task_owner_v1"}
                return result
        result["memory_admission"] = {"classification": "quarantined",
                                      "reason_code": "memory_owner_namespace_unclassified"}
        return result

    def rollback(self, batch_id):
        require_memory_identifier(batch_id, "batch_id")
        with Session(self._engine) as session:
            self._lock(session)
            backups = session.exec(select(MemoryMigrationBackupDB).where(
                MemoryMigrationBackupDB.batch_id == batch_id,
            ).order_by(MemoryMigrationBackupDB.entry_id).limit(1001).with_for_update()).all()
            if not backups:
                raise ValueError("memory_migration_batch_unknown")
            if len(backups) > 1000:
                raise ValueError("memory_migration_batch_too_large")
            restored = 0
            for backup in backups:
                if backup.restored:
                    continue
                row = session.exec(select(MemoryEntryDB).where(
                    MemoryEntryDB.id == backup.entry_id,
                ).with_for_update()).first()
                if row is None or (row.task_id, row.goal_id) != (backup.task_id, backup.goal_id):
                    raise ValueError("memory_migration_rollback_conflict")
                if row.memory_metadata != backup.migrated_metadata:
                    raise ValueError("memory_migration_rollback_conflict")
                row.memory_metadata = deepcopy(backup.original_metadata)
                backup.restored = True
                session.add(row)
                session.add(backup)
                restored += 1
            session.commit()
        return {"restored": restored, "production_release_evidence": False}
