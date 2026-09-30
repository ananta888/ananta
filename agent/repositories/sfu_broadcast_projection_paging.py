"""Page filtering, ordering keys and opaque cursors for SFU broadcast projection adapters."""

from __future__ import annotations

import base64
import json
from typing import cast

from agent.models.sfu_broadcast_projection import (
    SfuProjectionEnvelope,
)
from agent.repositories.sfu_broadcast_projection_rules import (
    _LIVE_STATUSES,
    ProjectionT,
    SfuBroadcastRepositoryError,
)


def _filter_page(
    values: list[ProjectionT],
    *,
    mode: str,
    threshold: float | int | None,
) -> list[ProjectionT]:
    if mode == "all":
        return values
    if mode == "expired":
        return [
            value
            for value in values
            if value.status in _LIVE_STATUSES and value.expires_at <= cast(float, threshold)
        ]
    if mode == "reconciliation":
        return [
            value
            for value in values
            if value.room_state_revision < cast(int, threshold)
        ]
    if mode == "retention":
        return [
            value
            for value in values
            if value.retain_until <= cast(float, threshold)
            and value.retention_status != "purged"
        ]
    raise SfuBroadcastRepositoryError("projection_page_mode_invalid")


def _page_key(
    value: SfuProjectionEnvelope,
    mode: str,
    sort_attribute: str,
) -> tuple[str | float | int, str]:
    if mode == "expired":
        return value.expires_at, value.id
    if mode == "reconciliation":
        return value.room_state_revision, value.id
    if mode == "retention":
        return value.retain_until, value.id
    return getattr(value, sort_attribute), value.id


def _encode_cursor(mode: str, key: tuple[str | float | int, str]) -> str:
    payload = json.dumps([mode, key[0], key[1]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str, mode: str) -> tuple[str | float | int, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        decoded = json.loads(raw)
        if (
            not isinstance(decoded, list)
            or len(decoded) != 3
            or decoded[0] != mode
            or not isinstance(decoded[1], (str, int, float))
            or not isinstance(decoded[2], str)
        ):
            raise ValueError
        return decoded[1], decoded[2]
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SfuBroadcastRepositoryError("projection_cursor_invalid") from error


def _encode_retention_cursor(key: tuple[float, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_retention_cursor(cursor: str) -> tuple[float, str]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(raw, list) or len(raw) != 2 or not isinstance(raw[0], (int, float)) or not isinstance(raw[1], str):
            raise ValueError
        return float(raw[0]), raw[1]
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SfuBroadcastRepositoryError("audience_retention_cursor_invalid") from exc
