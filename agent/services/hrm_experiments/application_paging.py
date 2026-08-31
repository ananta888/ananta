"""Stable cursor pagination for HRM control-plane projections."""

from __future__ import annotations

import base64
from typing import Any


class HrmCursorError(ValueError):
    """Raised when an opaque HRM page cursor cannot be decoded."""


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        value = int(raw)
    except (ValueError, UnicodeError) as exc:
        raise HrmCursorError("hrm.cursor_invalid") from exc
    if value < 0:
        raise HrmCursorError("hrm.cursor_invalid")
    return value


def encode_cursor(value: int) -> str:
    return base64.urlsafe_b64encode(str(value).encode()).decode().rstrip("=")


def page(items: list[Any], *, offset: int, limit: int) -> dict[str, Any]:
    selected = items[:limit]
    return {
        "items": selected,
        "next_cursor": encode_cursor(offset + limit) if len(items) > limit else None,
    }
