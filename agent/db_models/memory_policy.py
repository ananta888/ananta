"""Hub-issued memory grants; identifiers here are not Evidence Registry identities."""

from sqlmodel import JSON, Column, Field, SQLModel


class MemoryRunPolicyDB(SQLModel, table=True):
    __tablename__ = "memory_run_policies"
    run_id: str = Field(primary_key=True, max_length=160)
    tenant_id: str = Field(index=True)
    task_id: str = Field(index=True)
    actor_subject: str
    policy: dict = Field(sa_column=Column(JSON, nullable=False))
    policy_digest: str
    expires_at: float
    revoked: bool = False
    worker_job_id: str | None = None
    dispatch_lease_id: str | None = None
