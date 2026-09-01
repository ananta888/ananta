"""Persistence boundary for Hub-owned spreadsheet documents and proposals."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class SpreadsheetDocumentRepositoryPort(Protocol):
    """Small persistence interface consumed by the Hub saga."""

    def create_document(self, tenant_id: str, document: Mapping[str, Any]) -> dict[str, Any]: ...

    def get_document(self, tenant_id: str, document_id: str) -> dict[str, Any]: ...

    def get_version(self, tenant_id: str, document_id: str, version: int) -> dict[str, Any]: ...

    def list_documents(self, tenant_id: str, *, limit: int = 100) -> dict[str, Any]: ...

    def list_versions(self, tenant_id: str, document_id: str, *, limit: int = 100) -> dict[str, Any]: ...

    def get_proposal(self, tenant_id: str, proposal_id: str) -> dict[str, Any] | None: ...

    def finalize_proposal(
        self,
        tenant_id: str,
        proposal_id: str,
        result: Mapping[str, Any],
        *,
        document_id: str,
        expected_version: int,
        promoted_document: Mapping[str, Any] | None,
    ) -> dict[str, Any]: ...


__all__ = ["SpreadsheetDocumentRepositoryPort"]
