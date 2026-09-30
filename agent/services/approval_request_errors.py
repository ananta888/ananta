"""Lifecycle errors of the approval request service."""

from __future__ import annotations


class ApprovalDecisionError(ValueError):
    """Raised for invalid lifecycle transitions (maps to HTTP 400/404/409)."""

    def __init__(self, code: str, http_status: int = 400):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
