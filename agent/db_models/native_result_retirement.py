"""Hub-owned retirement permits, confirmed slot bindings and bounded scan cursors."""

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class NativeResultRetirementManifestDB(SQLModel, table=True):
    __tablename__ = 'native_result_retirement_manifests'

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    manifest: dict = Field(sa_column=Column(JSON, nullable=False))
    manifest_digest: str
    issued_at: float
    state: str = 'issued'
    reason_code: str = ''
    updated_at: float


class NativeResultRetirementMarkerDB(SQLModel, table=True):
    __tablename__ = 'native_result_retirement_markers'

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    worker_id: str
    worker_url: str
    store_binding_id: str
    task_id: str = Field(index=True)
    binding_digest: str
    maintenance_task_id: str
    confirmed_at: float


class NativeResultRetirementCursorDB(SQLModel, table=True):
    __tablename__ = 'native_result_retirement_cursors'

    id: str = Field(primary_key=True)
    tenant_id: str
    project_id: str
    owner_digest: str
    after_updated_at: float
    after_id: str
