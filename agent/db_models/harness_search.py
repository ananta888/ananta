"""Immutable revisions of Hub Harness search state and dispatch intents."""

from sqlmodel import Field, SQLModel


class HarnessSearchRevisionDB(SQLModel, table=True):
    __tablename__ = "harness_search_revisions"
    session_id: str = Field(primary_key=True)
    revision: int = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    content_digest: str
    payload_json: str
