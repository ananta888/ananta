"""Dependency-neutral value contracts for business controlling imports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkbookRiskMetadata:
    has_macros: bool = False
    has_external_links: bool = False
    has_formula_cells: bool = False
    has_unsupported_objects: bool = False


@dataclass(frozen=True)
class TabularProfileRequest:
    tenant_id: str
    project_id: str
    source_revision_id: str
    revision_digest: str
    source_format: str
    headers: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    risk: WorkbookRiskMetadata = WorkbookRiskMetadata()


@dataclass(frozen=True)
class ColumnProfile:
    header: str
    inferred_type: str
    null_count: int
    invalid_count: int
    invalid_locators: tuple[str, ...] = ()


@dataclass(frozen=True)
class TabularProfile:
    source_revision_id: str
    revision_digest: str
    row_count: int
    duplicate_row_count: int
    columns: tuple[ColumnProfile, ...]
    profile_digest: str


@dataclass(frozen=True)
class MappingConfirmation:
    profile_digest: str
    column_mapping: tuple[tuple[str, str], ...]
    confirmed_by: str
    confirmation_digest: str


__all__ = [
    "ColumnProfile",
    "MappingConfirmation",
    "TabularProfile",
    "TabularProfileRequest",
    "WorkbookRiskMetadata",
]
