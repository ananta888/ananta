"""Persistence boundary for immutable tenant-scoped validation references."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class SpreadsheetValidationReferenceRepositoryPort(Protocol):
    def create_reference(self, tenant_id: str, reference: Mapping[str, Any]) -> dict[str, Any]: ...

    def get_reference(self, tenant_id: str, reference_id: str) -> dict[str, Any]: ...

    def list_references(self, tenant_id: str, *, limit: int = 100) -> dict[str, Any]: ...


__all__ = ["SpreadsheetValidationReferenceRepositoryPort"]
