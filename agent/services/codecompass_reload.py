"""CCARI-009 + CCARI-011: context_reload_request parser and Hub handler.

Pure parsing / validation / dispatch. The actual chunk retrieval is delegated
to ``ContextDeliveryService`` (see ``agent/services/context_delivery_service.py``)
so this module never touches the filesystem or the LLM.

Contract reference: ``docs/contracts/codecompass-context-reload-request.md``.
"""
from __future__ import annotations

import os
from typing import Any


class ReloadRequestError(ValueError):
    """Raised when a context_reload_request fails validation.

    Attributes:
        code: machine-readable error code (e.g. ``policy_blocked``,
            ``invalid_request_shape``, ``invalid_entry_type``,
            ``invalid_query_type``, ``absolute_path_not_allowed``).
        message: human-readable explanation.
    """

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


# CCARI-002: the canonical list of supported requested_context types.
VALID_TYPES: set[str] = {
    "file_range",
    "symbol",
    "codecompass_search",
    "graph_expand",
    "architecture_query",
}

# CCARI-002: the whitelisted architecture query types (mirrors
# ``codecompass_architecture_query.VALID_QUERY_TYPES`` but duplicated here to
# avoid a circular import between the parser and the engine).
VALID_ARCH_QUERY_TYPES: set[str] = {
    "dto-impact",
    "controller-test-coverage",
    "field-policy-impact",
    "service-dependency-chain",
}

MAX_REQUESTED_ENTRIES: int = 10
MAX_REASON_LENGTH: int = 500


def _dedup_key(entry: dict[str, Any]) -> tuple[str, str]:
    """Stable dedup key for a requested_context entry.

    The key is ``(type, query-or-path-or-seed)`` so two semantically equivalent
    entries collapse to one.
    """
    entry_type = str(entry.get("type") or "").strip()
    if entry_type == "file_range":
        return (entry_type, str(entry.get("path") or "").strip())
    if entry_type == "symbol":
        return (entry_type, str(entry.get("query") or "").strip())
    if entry_type == "codecompass_search":
        return (entry_type, str(entry.get("query") or "").strip())
    if entry_type == "graph_expand":
        return (entry_type, str(entry.get("seed") or "").strip())
    if entry_type == "architecture_query":
        return (
            entry_type,
            f"{str(entry.get('query_type') or '').strip()}::{str(entry.get('seed') or '').strip()}",
        )
    return (entry_type, str(entry.get("query") or entry.get("seed") or "").strip())


def _validate_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ReloadRequestError("invalid_entry_type", "entry must be an object")
    entry_type = str(entry.get("type") or "").strip()
    if entry_type not in VALID_TYPES:
        raise ReloadRequestError("invalid_entry_type", f"unknown type: {entry_type}")
    if entry_type == "file_range":
        path = str(entry.get("path") or "").strip()
        if not path:
            raise ReloadRequestError("invalid_entry_type", "file_range requires non-empty path")
        if os.path.isabs(path):
            raise ReloadRequestError("absolute_path_not_allowed", "file_range path must be repo-relative")
        try:
            start_line = int(entry.get("start_line"))  # type: ignore[arg-type]
            end_line = int(entry.get("end_line"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ReloadRequestError("invalid_entry_type", "file_range requires integer start_line/end_line")
        if start_line < 1 or end_line < start_line:
            raise ReloadRequestError("invalid_entry_type", "file_range requires start_line <= end_line, both >= 1")
        return {"type": "file_range", "path": path, "start_line": start_line, "end_line": end_line}
    if entry_type == "symbol":
        query = str(entry.get("query") or "").strip()
        if not query:
            raise ReloadRequestError("invalid_entry_type", "symbol requires non-empty query")
        return {"type": "symbol", "query": query}
    if entry_type == "codecompass_search":
        query = str(entry.get("query") or "").strip()
        if not query:
            raise ReloadRequestError("invalid_entry_type", "codecompass_search requires non-empty query")
        return {"type": "codecompass_search", "query": query}
    if entry_type == "graph_expand":
        seed = str(entry.get("seed") or "").strip()
        if not seed:
            raise ReloadRequestError("invalid_entry_type", "graph_expand requires non-empty seed")
        try:
            depth = int(entry.get("depth", 2))
        except (TypeError, ValueError):
            raise ReloadRequestError("invalid_entry_type", "graph_expand requires integer depth")
        direction = str(entry.get("direction") or "outgoing").strip().lower()
        if direction not in {"outgoing", "incoming", "both"}:
            direction = "outgoing"
        return {"type": "graph_expand", "seed": seed, "depth": depth, "direction": direction}
    if entry_type == "architecture_query":
        query_type = str(entry.get("query_type") or "").strip()
        if query_type not in VALID_ARCH_QUERY_TYPES:
            raise ReloadRequestError("invalid_query_type", f"unknown architecture query_type: {query_type}")
        seed = str(entry.get("seed") or "").strip()
        if not seed:
            raise ReloadRequestError("invalid_entry_type", "architecture_query requires non-empty seed")
        result: dict[str, Any] = {"type": "architecture_query", "query_type": query_type, "seed": seed}
        field = str(entry.get("field") or "").strip()
        if field:
            result["field"] = field
        return result
    # unreachable
    raise ReloadRequestError("invalid_entry_type", f"unhandled type: {entry_type}")


def parse_reload_request(raw: Any) -> dict[str, Any]:
    """Parse and validate a context_reload_request payload.

    Returns a normalized dict with keys:
    - ``kind``: ``"context_reload_request"``
    - ``reason``: trimmed reason text
    - ``risk``: ``"read_only"``
    - ``requested_context``: list of validated, deduplicated, clamped entries
    - ``warnings``: list of warning codes (e.g. ``entries_clamped_to_max``)

    Raises ``ReloadRequestError`` on any validation failure. The error's
    ``code`` is one of the canonical codes documented in the contract.
    """
    if not isinstance(raw, dict):
        raise ReloadRequestError("invalid_request_shape", "request must be an object")
    if str(raw.get("kind") or "").strip() != "context_reload_request":
        raise ReloadRequestError("invalid_request_shape", "kind must be 'context_reload_request'")
    if str(raw.get("risk") or "").strip() != "read_only":
        raise ReloadRequestError("policy_blocked", "risk must be 'read_only'")
    reason = str(raw.get("reason") or "").strip()
    if not reason:
        raise ReloadRequestError("invalid_request_shape", "reason must be non-empty")
    if len(reason) > MAX_REASON_LENGTH:
        reason = reason[:MAX_REASON_LENGTH]
    entries = raw.get("requested_context")
    if not isinstance(entries, list) or not entries:
        raise ReloadRequestError("invalid_request_shape", "requested_context must be a non-empty list")

    validated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    last_error: ReloadRequestError | None = None
    for raw_entry in entries:
        try:
            entry = _validate_entry(raw_entry)
        except ReloadRequestError as exc:
            # First error wins; the Hub returns the same code in the response.
            if last_error is None:
                last_error = exc
            continue
        key = _dedup_key(entry)
        if key in seen:
            continue
        seen.add(key)
        validated.append(entry)

    if last_error is not None and not validated:
        raise last_error
    if not validated:
        raise ReloadRequestError("invalid_request_shape", "no valid requested_context entries")

    warnings: list[str] = []
    if len(validated) > MAX_REQUESTED_ENTRIES:
        validated = validated[:MAX_REQUESTED_ENTRIES]
        warnings.append("entries_clamped_to_max")
    if last_error is not None:
        # Per-entry errors that were dropped silently with the first one
        # surfaced via the response. We do not raise here because the parser
        # recovered at least one valid entry; the Hub caller's handler is
        # responsible for surfacing the dropped-entry count.
        warnings.append("entries_dropped_due_to_error")

    return {
        "kind": "context_reload_request",
        "reason": reason,
        "risk": "read_only",
        "requested_context": validated,
        "warnings": warnings,
    }
