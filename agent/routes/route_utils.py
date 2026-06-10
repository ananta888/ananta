"""Shared helpers for route modules (error envelope, query parsing)."""

from agent.common.errors import api_response


def team_error(message: str, code: int, **extra):
    """Return standardized API response with legacy compatibility."""
    return api_response(status="error", message=message, code=code, data=extra if extra else None)


def parse_bool_query(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def parse_parts_query(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]
