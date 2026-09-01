"""Ports between the Hub-owned spreadsheet saga and one delegated executor."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class SpreadsheetExecutionPort(Protocol):
    @property
    def capability(self) -> Mapping[str, Any]: ...

    def dry_run(self, *, snapshot: Mapping[str, Any], actions: tuple[Mapping[str, Any], ...]) -> Mapping[str, Any]: ...


class SpreadsheetImportPort(Protocol):
    def import_document(
        self,
        *,
        content: bytes,
        filename: str,
        media_type: str,
        document_version_id: str,
    ) -> Mapping[str, Any]: ...


__all__ = ["SpreadsheetExecutionPort", "SpreadsheetImportPort"]
