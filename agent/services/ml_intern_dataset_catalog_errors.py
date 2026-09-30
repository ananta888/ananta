"""Stable-reason-code rejection raised by the ml_intern dataset catalog."""

from __future__ import annotations


class DatasetCatalogError(ValueError):
    """Dataset catalog rejection with a stable reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


__all__ = ["DatasetCatalogError"]
