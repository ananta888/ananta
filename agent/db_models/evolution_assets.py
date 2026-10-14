"""Immutable scoped assets and append-only quarantine/lifecycle revisions."""

from sqlmodel import Field, SQLModel


class EvolutionAssetDB(SQLModel, table=True):
    __tablename__ = "evolution_assets"
    tenant_id: str = Field(primary_key=True)
    project_id: str = Field(primary_key=True)
    asset_digest: str = Field(primary_key=True)
    asset_json: str
    run_ref: str


class EvolutionAssetStateDB(SQLModel, table=True):
    __tablename__ = "evolution_asset_states"
    tenant_id: str = Field(primary_key=True)
    project_id: str = Field(primary_key=True)
    asset_digest: str = Field(primary_key=True)
    revision: int = Field(primary_key=True)
    state: str
    reason: str
