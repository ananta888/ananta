"""HTTP-mappable error of the Organization planning composition."""

from __future__ import annotations


class OrganizationPlanningCompositionError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


__all__ = [
    "OrganizationPlanningCompositionError",
]
