"""Persistence boundary for Hub-owned spreadsheet learning state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class SpreadsheetLearningConflict(RuntimeError):
    """An immutable command identity was replayed with different content."""


class SpreadsheetLearningRepository(Protocol):
    durable: bool

    def append_feedback(self, tenant_id: str, event: Mapping[str, Any]) -> dict[str, Any]: ...

    def get_feedback(self, tenant_id: str, event_id: str) -> dict[str, Any]: ...

    def append_consent(self, tenant_id: str, consent: Mapping[str, Any]) -> dict[str, Any]: ...

    def append_consent_with_impact(
        self,
        tenant_id: str,
        consent: Mapping[str, Any],
        impact: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def get_consent(self, tenant_id: str, consent_id: str) -> dict[str, Any]: ...

    def get_active_consent_for_feedback(self, tenant_id: str, feedback_id: str) -> dict[str, Any]: ...

    def append_dataset(self, tenant_id: str, dataset: Mapping[str, Any]) -> dict[str, Any]: ...

    def get_dataset(self, tenant_id: str, dataset_id: str) -> dict[str, Any]: ...

    def list_datasets(self, tenant_id: str) -> list[dict[str, Any]]: ...

    def append_training_lineage(self, tenant_id: str, lineage: Mapping[str, Any]) -> dict[str, Any]: ...

    def list_training_lineage(self, tenant_id: str) -> list[dict[str, Any]]: ...

    def append_revocation_impact(self, tenant_id: str, impact: Mapping[str, Any]) -> dict[str, Any]: ...


__all__ = ["SpreadsheetLearningConflict", "SpreadsheetLearningRepository"]
