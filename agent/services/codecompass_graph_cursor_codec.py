"""Opaque, revision- and scope-bound pagination cursors for graph reads."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping

from agent.services.codecompass_graph_read_models import (
    CodeCompassGraphReadError,
    GraphInventoryCursor,
)


def staged_graph_scope_digest(
    *,
    index_id: str,
    stage: str,
    domain_scope: object,
    include_subdomains: bool,
) -> str:
    return graph_cursor_scope_digest(
        {
            "view": "staged",
            "stage": stage,
            "domain_scope": domain_scope,
            "include_subdomains": include_subdomains,
            "index_id": index_id,
        }
    )


def inventory_graph_scope_digest(*, index_id: str, facet: str) -> str:
    return graph_cursor_scope_digest(
        {"view": "inventory", "facet": facet, "index_id": index_id}
    )


def graph_cursor_scope_digest(scope: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(scope),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def encode_graph_cursor(
    offset: int,
    *,
    graph_revision: str,
    scope_digest: str,
) -> str:
    payload = json.dumps(
        {
            "version": 1,
            "graph_revision": graph_revision,
            "scope_digest": scope_digest,
            "offset": int(offset),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_graph_cursor(
    value: object,
    *,
    graph_revision: str,
    scope_digest: str,
) -> int:
    if value in (None, ""):
        return 0
    payload = decode_graph_cursor_payload(
        value,
        graph_revision=graph_revision,
    )
    if payload.get("scope_digest") != scope_digest:
        raise CodeCompassGraphReadError("graph_cursor_scope_mismatch")
    return int(payload["offset"])


def decode_inventory_graph_cursor(
    value: object,
    *,
    graph_revision: str,
    index_id: str,
) -> GraphInventoryCursor:
    if value in (None, ""):
        return GraphInventoryCursor(None, 0)
    payload = decode_graph_cursor_payload(
        value,
        graph_revision=graph_revision,
    )
    scope_digest = payload.get("scope_digest")
    for facet in ("domains", "relations"):
        if scope_digest == inventory_graph_scope_digest(
            index_id=index_id,
            facet=facet,
        ):
            return GraphInventoryCursor(facet, int(payload["offset"]))
    raise CodeCompassGraphReadError("graph_cursor_scope_mismatch")


def decode_graph_cursor_payload(
    value: object,
    *,
    graph_revision: str,
) -> Mapping[str, object]:
    try:
        encoded = str(value)
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            raise ValueError
        if payload.get("graph_revision") != graph_revision:
            raise CodeCompassGraphReadError(
                "graph_cursor_stale",
                status_code=409,
            )
        offset = int(payload["offset"])
        if offset < 0:
            raise ValueError
        return payload
    except CodeCompassGraphReadError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise CodeCompassGraphReadError("graph_cursor_invalid") from exc


def inventory_next_graph_cursor(
    *,
    offset: int,
    total: int,
    graph_revision: str,
    index_id: str,
    facet: str,
) -> str | None:
    if offset >= total:
        return None
    return encode_graph_cursor(
        offset,
        graph_revision=graph_revision,
        scope_digest=inventory_graph_scope_digest(
            index_id=index_id,
            facet=facet,
        ),
    )


def encode_graph_offset(value: int) -> str:
    return base64.urlsafe_b64encode(str(value).encode("ascii")).decode("ascii").rstrip("=")


def decode_graph_offset(value: object) -> int:
    if value in (None, ""):
        return 0
    try:
        encoded = str(value)
        encoded += "=" * (-len(encoded) % 4)
        parsed = int(base64.urlsafe_b64decode(encoded).decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CodeCompassGraphReadError("graph_cursor_invalid") from exc
    if parsed < 0:
        raise CodeCompassGraphReadError("graph_cursor_invalid")
    return parsed
