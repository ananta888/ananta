"""Stable application error shared by Kanban service adapters."""

from __future__ import annotations

from typing import Any


class KanbanServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


__all__ = ["KanbanServiceError"]
