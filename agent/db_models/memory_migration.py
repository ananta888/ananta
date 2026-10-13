"""Internal Hub migration journal; never an agent-readable memory channel."""

from sqlmodel import JSON, Column, Field, SQLModel


class MemoryMigrationBackupDB(SQLModel, table=True):
    __tablename__ = "memory_migration_backups"

    batch_id: str = Field(primary_key=True, max_length=160)
    entry_id: str = Field(primary_key=True)
    task_id: str | None = None
    goal_id: str | None = None
    original_metadata: dict = Field(sa_column=Column(JSON, nullable=False))
    migrated_metadata: dict = Field(sa_column=Column(JSON, nullable=False))
    restored: bool = False
