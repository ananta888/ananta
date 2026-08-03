"""Fail-closed projections for general-purpose Task read models.

Detailed source catalogs and citation verification are exposed only by the
task-source endpoints, where tenant/project/organization ownership is checked.
Generic Task APIs must not become an authorization bypass for those endpoints.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_SOURCE_SECURITY_KEYS = frozenset(
    {
        "answer_verification",
        "source_catalog",
        "source_catalog_binding",
        "source_catalog_publication",
    }
)


def redact_task_source_security_metadata(value: Any) -> Any:
    """Copy a JSON-like Task projection while removing governed source data."""

    if isinstance(value, Mapping):
        return {
            str(key): redact_task_source_security_metadata(item)
            for key, item in value.items()
            if str(key) not in _SOURCE_SECURITY_KEYS
        }
    if isinstance(value, list):
        return [redact_task_source_security_metadata(item) for item in value]
    if isinstance(value, tuple):
        return tuple(
            redact_task_source_security_metadata(item) for item in value
        )
    return value


__all__ = ["redact_task_source_security_metadata"]
