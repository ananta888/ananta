from __future__ import annotations

from typing import Any

from flask import g, request

from agent.common.errors import api_response
from agent.services.repository_registry import get_repository_registry


def _repos():
    return get_repository_registry()


def _team_error(message: str, code: int, **extra) -> tuple:
    """Return standardized API response with legacy compatibility."""
    return api_response(status="error", message=message, code=code, data=extra if extra else None) # type: ignore


def _parse_bool_query(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_parts_query(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]
