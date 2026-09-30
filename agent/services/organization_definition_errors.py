"""Fail-closed error raised by Organization definition mutation and reconciliation."""

from __future__ import annotations


class OrganizationDefinitionMutationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


__all__ = [
    "OrganizationDefinitionMutationError",
]
