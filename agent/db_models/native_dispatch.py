"""Hub dispatch journal; no Worker credentials or executable payloads."""

from sqlmodel import Field, SQLModel


class NativeDispatchDB(SQLModel, table=True):
    __tablename__ = "native_dispatch_journal"

    id: str = Field(primary_key=True)
    task_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    binding_digest: str
    command_digest: str
    worker_id: str
    worker_url: str
    assignment_revision: int
    claim: str
    state: str = Field(index=True)
    created_at: float
    updated_at: float
