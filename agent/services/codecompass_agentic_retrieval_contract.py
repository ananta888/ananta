"""Canonical Agent/MCP/n8n contract for CodeCompass hybrid retrieval.

This module is the only place that names the public request/response
shape. Vector-store backends stay behind ports; Qdrant collection names
and credentials never appear in the envelope.
"""

from __future__ import annotations

from typing import Any, Mapping

SCHEMA_ID = "codecompass.agentic-retrieval.v1"
KIND_REQUEST = "request"
KIND_RESPONSE = "response"

MODE_AUTO = "auto"
MODE_HYBRID = "hybrid"
MODE_VECTOR = "vector"
MODE_EXACT = "exact"
MODE_GRAPH = "graph"
VALID_MODES = frozenset({MODE_AUTO, MODE_HYBRID, MODE_VECTOR, MODE_EXACT, MODE_GRAPH})
EXPLICIT_MODES = frozenset({MODE_HYBRID, MODE_VECTOR, MODE_EXACT, MODE_GRAPH})

SIGNAL_EXACT = "exact"
SIGNAL_GRAPH = "graph"
SIGNAL_VECTOR = "vector"
VALID_SIGNALS = frozenset({SIGNAL_EXACT, SIGNAL_GRAPH, SIGNAL_VECTOR})

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_ERROR = "error"
STATUS_EMPTY = "empty"

REASON_QUERY_REQUIRED = "query_required"
REASON_UNKNOWN_MODE = "unknown_retrieval_mode"
REASON_INVALID_SIGNALS = "invalid_requested_signals"
REASON_EMPTY_SCOPE = "empty_scope"
REASON_SCOPE_WIDENING = "scope_widening_denied"
REASON_NO_RESULT = "no_result"
REASON_VECTOR_UNAVAILABLE = "vector_unavailable"
REASON_VECTOR_TIMEOUT = "vector_timeout"
REASON_VECTOR_STALE = "vector_stale_index"
REASON_VECTOR_DIMENSIONS = "vector_dimensions_mismatch"
REASON_VECTOR_FAIL_CLOSED = "vector_fail_closed"
REASON_GRAPH_UNAVAILABLE = "graph_unavailable"
REASON_EXACT_UNAVAILABLE = "exact_unavailable"

DEFAULT_TOP_K = 8
MAX_TOP_K = 20
DEFAULT_MAX_CHARS = 8000
MAX_MAX_CHARS = 32000
DEFAULT_MAX_TOKENS = 3000
DEFAULT_CANDIDATE_LIMIT = 24
MAX_CANDIDATE_LIMIT = 80
DEFAULT_GRAPH_DEPTH = 1

_BACKEND_REASON_MAP = {
    "qdrant_unavailable": REASON_VECTOR_UNAVAILABLE,
    "qdrant_timeout": REASON_VECTOR_TIMEOUT,
    "dimensions_mismatch": REASON_VECTOR_DIMENSIONS,
    "empty_collection": REASON_VECTOR_STALE,
    "stale_index": REASON_VECTOR_STALE,
    "vector_index_not_mounted": REASON_VECTOR_STALE,
    "incompatible_collection": REASON_VECTOR_FAIL_CLOSED,
    "qdrant_unauthorized": REASON_VECTOR_FAIL_CLOSED,
    "vector_store_compatibility_required": REASON_VECTOR_FAIL_CLOSED,
    "vector_scope_required": REASON_EMPTY_SCOPE,
    "vector_scope_conflict": REASON_SCOPE_WIDENING,
    "codecompass_graph_unavailable": REASON_GRAPH_UNAVAILABLE,
}


class AgenticRetrievalContractError(ValueError):
    """Typed, secret-free contract failure."""

    def __init__(self, reason: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.reason = str(reason or "agentic_retrieval_error")
        self.details = dict(details or {})
        super().__init__(self.reason)


def map_vector_backend_reason(reason: str | None) -> str:
    """Map store-level reasons onto the agent contract without leaking backends."""

    token = str(reason or "").strip().split(":", 1)[0]
    mapped = _BACKEND_REASON_MAP.get(token)
    if mapped:
        return mapped
    if token.startswith("qdrant_"):
        if "timeout" in token:
            return REASON_VECTOR_TIMEOUT
        if "unavailable" in token:
            return REASON_VECTOR_UNAVAILABLE
        return REASON_VECTOR_FAIL_CLOSED
    if "stale" in token or "manifest" in token:
        return REASON_VECTOR_STALE
    if token.startswith("vector_"):
        return token
    return REASON_VECTOR_UNAVAILABLE


def _clean_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit]
    return text


def _bounded_int(value: Any, default: int, lo: int, hi: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AgenticRetrievalContractError("invalid_budget") from exc
    return max(lo, min(parsed, hi))


def _normalize_signals(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple, set)):
        raise AgenticRetrievalContractError(REASON_INVALID_SIGNALS)
    signals: list[str] = []
    for item in raw:
        token = str(item or "").strip().lower()
        if token not in VALID_SIGNALS:
            raise AgenticRetrievalContractError(REASON_INVALID_SIGNALS)
        if token not in signals:
            signals.append(token)
    return signals


def _normalize_scope(raw: Any) -> dict[str, Any]:
    data = dict(raw or {}) if isinstance(raw, dict) else {}
    paths: list[str] = []
    for item in list(data.get("allowed_paths") or []):
        path = str(item or "").replace("\\", "/").strip().strip("/")
        if path and path not in paths and ".." not in path.split("/"):
            paths.append(path)
        if len(paths) >= 64:
            break
    return {
        "tenant_id": _clean_text(data.get("tenant_id"), limit=256),
        "workspace_id": _clean_text(data.get("workspace_id"), limit=256),
        "repository_id": _clean_text(data.get("repository_id"), limit=256),
        "source_scope": _clean_text(data.get("source_scope"), limit=256),
        "revision": _clean_text(data.get("revision"), limit=256),
        "allowed_paths": paths,
    }


def validate_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(payload or {})
    schema = str(data.get("schema") or SCHEMA_ID).strip()
    if schema != SCHEMA_ID:
        raise AgenticRetrievalContractError("unknown_schema")
    kind = str(data.get("kind") or KIND_REQUEST).strip()
    if kind != KIND_REQUEST:
        raise AgenticRetrievalContractError("unknown_kind")
    query = _clean_text(data.get("query"), limit=4000)
    if not query:
        raise AgenticRetrievalContractError(REASON_QUERY_REQUIRED)
    mode = str(data.get("mode") or MODE_AUTO).strip().lower() or MODE_AUTO
    if mode not in VALID_MODES:
        raise AgenticRetrievalContractError(REASON_UNKNOWN_MODE)
    budget_raw = dict(data.get("budget") or {}) if isinstance(data.get("budget"), dict) else {}
    return {
        "schema": SCHEMA_ID,
        "kind": KIND_REQUEST,
        "query": query,
        "mode": mode,
        "requested_signals": _normalize_signals(data.get("requested_signals")),
        "task_kind": _clean_text(data.get("task_kind"), limit=64),
        "scope": _normalize_scope(data.get("scope")),
        "budget": {
            "top_k": _bounded_int(budget_raw.get("top_k"), DEFAULT_TOP_K, 1, MAX_TOP_K),
            "max_chars": _bounded_int(
                budget_raw.get("max_chars"), DEFAULT_MAX_CHARS, 256, MAX_MAX_CHARS
            ),
            "max_tokens": _bounded_int(
                budget_raw.get("max_tokens"), DEFAULT_MAX_TOKENS, 64, 200_000
            ),
            "candidate_limit": _bounded_int(
                budget_raw.get("candidate_limit"),
                DEFAULT_CANDIDATE_LIMIT,
                1,
                MAX_CANDIDATE_LIMIT,
            ),
            "graph_depth": _bounded_int(
                budget_raw.get("graph_depth"), DEFAULT_GRAPH_DEPTH, 0, 4
            ),
        },
        "continuation_handle": _clean_text(data.get("continuation_handle"), limit=512),
    }


def request_from_tool_args(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """Accept the existing tool argument shape plus optional contract fields."""

    args = dict(arguments or {})
    scope = dict(args.get("scope") or {}) if isinstance(args.get("scope"), dict) else {}
    for key in (
        "tenant_id",
        "workspace_id",
        "repository_id",
        "source_scope",
        "revision",
        "allowed_paths",
    ):
        if key in args and key not in scope:
            scope[key] = args.get(key)
    budget = dict(args.get("budget") or {}) if isinstance(args.get("budget"), dict) else {}
    if args.get("limit") is not None and budget.get("top_k") is None:
        budget["top_k"] = args.get("limit")
    if args.get("top_k") is not None:
        budget["top_k"] = args.get("top_k")
    if args.get("max_chars") is not None:
        budget["max_chars"] = args.get("max_chars")
    if args.get("max_tokens") is not None:
        budget["max_tokens"] = args.get("max_tokens")
    return validate_request(
        {
            "schema": SCHEMA_ID,
            "kind": KIND_REQUEST,
            "query": args.get("query"),
            "mode": args.get("mode") or MODE_AUTO,
            "requested_signals": args.get("requested_signals"),
            "task_kind": args.get("task_kind"),
            "scope": scope,
            "budget": budget,
            "continuation_handle": args.get("continuation_handle"),
        }
    )


def empty_response(
    *,
    query: str,
    status: str,
    reason_code: str,
    plan: Mapping[str, Any] | None = None,
    warnings: list[str] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA_ID,
        "kind": KIND_RESPONSE,
        "status": status,
        "reason_code": reason_code,
        "query": query,
        "plan": {
            "mode": str((plan or {}).get("mode") or MODE_AUTO),
            "signals": list((plan or {}).get("signals") or []),
            "rationale": str((plan or {}).get("rationale") or ""),
        },
        "evidence": [],
        "truncated": False,
        "continuation_handle": "",
        "warnings": list(warnings or []),
        "diagnostics": {
            "engines": dict((diagnostics or {}).get("engines") or {}),
            "index": dict((diagnostics or {}).get("index") or {}),
            "budget": dict((diagnostics or {}).get("budget") or {}),
            "scope": dict((diagnostics or {}).get("scope") or {}),
            "dedup": dict((diagnostics or {}).get("dedup") or {}),
            "fallback_reason": str((diagnostics or {}).get("fallback_reason") or ""),
        },
    }


def channel_to_signal(channel: str) -> str:
    token = str(channel or "").strip().lower()
    if token in {"codecompass_vector", "vector", "semantic_search"}:
        return SIGNAL_VECTOR
    if token in {"codecompass_graph", "graph"}:
        return SIGNAL_GRAPH
    return SIGNAL_EXACT


def signal_to_channel(signal: str) -> str:
    if signal == SIGNAL_VECTOR:
        return "codecompass_vector"
    if signal == SIGNAL_GRAPH:
        return "codecompass_graph"
    return "codecompass_fts"
