"""Public errors raised by Visual Process assistant orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class VisualProcessAssistantError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 422,
        retry_after: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = int(status_code)
        self.retry_after = retry_after
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.reason_code,
            "error_code": self.reason_code,
            **self.details,
        }
