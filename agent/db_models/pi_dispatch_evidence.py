"""Closed Hub evidence projection retained with the durable Pi send claim."""

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class PiDispatchEvidenceDB(SQLModel, table=True):
    __tablename__ = 'pi_dispatch_evidence'

    tenant_id: str = Field(primary_key=True)
    task_id: str = Field(primary_key=True)
    project_id: str
    claim: str
    projection: dict = Field(sa_column=Column(JSON, nullable=False))
