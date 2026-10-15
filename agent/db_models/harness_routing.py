"""Immutable routing bindings have a lifecycle separate from genome content."""

from sqlmodel import Field, SQLModel


class HarnessRouteBindingDB(SQLModel, table=True):
    __tablename__ = "harness_route_bindings"

    binding_digest: str = Field(primary_key=True)
    genome_id: str = Field(foreign_key="harness_genomes.genome_id", index=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    binding_json: str
