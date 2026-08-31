"""Stable contracts for delegated organization-category research execution."""

from __future__ import annotations


class OrganizationCategoryResearchExecutionError(ValueError):
    """Stable fail-closed reason for an invalid delegated execution."""

    def __init__(self, reason_code: str, *, details: list[str] | None = None) -> None:
        self.reason_code = str(reason_code)
        self.details = list(details or [])
        super().__init__(self.reason_code)
