"""Closed contracts for Hub-owned Organization Source Catalog publication."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONNECTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,68}$")


class _ClosedContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
        str_strip_whitespace=True,
    )


class OrganizationSourceCatalogPublishCommand(_ClosedContract):
    """Caller-controlled retrieval intent without evidence identities."""

    connection_id: str = Field(min_length=1, max_length=69)
    queries: list[str] = Field(min_length=1, max_length=8)
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("connection_id")
    @classmethod
    def validate_connection_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if _CONNECTION_ID.fullmatch(normalized) is None:
            raise ValueError("organization_source_catalog_connection_id_invalid")
        return normalized

    @field_validator("queries")
    @classmethod
    def normalize_queries(cls, values: list[str]) -> list[str]:
        normalized = [str(value or "").strip() for value in values]
        if any(not value or len(value) > 4000 for value in normalized):
            raise ValueError("organization_source_catalog_query_invalid")
        if len(set(normalized)) != len(normalized):
            raise ValueError("organization_source_catalog_query_duplicate")
        # Query order is not authority and must not change idempotency.
        return sorted(normalized)


class OrganizationSourceCatalogPublishResult(_ClosedContract):
    schema_name: Literal["organization_source_catalog_publication.v1"] = Field(
        default="organization_source_catalog_publication.v1",
        alias="schema",
    )
    organization_id: str
    catalog_task_id: str
    catalog_id: str
    catalog_hash: str
    repository_revision: str
    manifest_hash: str
    source_allowlist_version: str
    source_scope: str
    source_count: int = Field(ge=1, le=400)
    replayed: bool = False


__all__ = [
    "OrganizationSourceCatalogPublishCommand",
    "OrganizationSourceCatalogPublishResult",
]
