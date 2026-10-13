"""Hub-only durable cadence reservation; contains no private Worker result data."""

from sqlmodel import JSON, Column, Field, SQLModel


class NativeMaintenanceCadenceDB(SQLModel, table=True):
    __tablename__ = 'native_maintenance_cadence'
    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    schedule_id: str
    policy_digest: str
    policy_manifest: dict = Field(sa_column=Column(JSON, nullable=False))
    ordinal: int = 0
    next_due_at: float
    current_run_id: str | None = None
    last_run_id: str | None = None
    status: str = 'idle'
    reason_code: str = ''
    updated_at: float
