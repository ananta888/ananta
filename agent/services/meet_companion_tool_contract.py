"""Shared closed read-tool vocabulary and bounded result projection."""

import json
import os

SCHEMA = "ananta.meet-companion-tool.v1"
MAX_BODY_BYTES = 16 * 1024
MAX_QUERY_CHARS = 1000
MAX_RESULT_CHARS = 6000
DEFAULT_TIMEOUT_SECONDS = 15.0
ANALYTICS_TEMPLATES = (
    "document_counts_by_kind",
    "paths_for_kind",
    "graph_relation_counts",
    "snapshot_identity",
)
RETRIEVE_MODES = ("auto", "hybrid", "vector", "exact", "graph")


def _text(limit):
    def check(value):
        if not isinstance(value, str):
            raise ValueError
        value = " ".join(value.split())
        if not value or len(value) > limit:
            raise ValueError
        return value

    return check


def _choice(options):
    def check(value):
        if value not in options:
            raise ValueError
        return value

    return check


def _integer(low, high):
    def check(value):
        if type(value) is not int:
            raise ValueError
        return max(low, min(high, value))

    return check


def _boolean(value):
    if not isinstance(value, bool):
        raise ValueError
    return value


def _manifest(value):
    if not isinstance(value, dict):
        raise ValueError
    return value


# name -> (required fields, {field: validator}). Read-only CodeCompass family
# only: no write/admin MCP tool is reachable from the companion, whatever the
# registry or the operator policy would allow.
COMPANION_TOOLS = {
    "codecompass.retrieve": (
        ("query",),
        {"query": _text(MAX_QUERY_CHARS), "mode": _choice(RETRIEVE_MODES), "limit": _integer(1, 8)},
    ),
    "codecompass.architecture_overview": (
        ("query",),
        {"query": _text(MAX_QUERY_CHARS), "profile": _text(64), "revision": _text(64)},
    ),
    "codecompass.architecture_expand": (
        ("handle",),
        {"handle": _text(256), "query": _text(MAX_QUERY_CHARS), "revision": _text(64)},
    ),
    "codecompass.architecture_intelligence": ((), {"snapshot_ref": _text(256), "revision": _text(64)}),
    "codecompass.layers_heads": ((), {"profile_id": _text(128)}),
    "codecompass.layers_plan": (
        ("old_manifest", "new_manifest"),
        {"old_manifest": _manifest, "new_manifest": _manifest, "profile_id": _text(128)},
    ),
    "codecompass.analytics_query": (
        ("template",),
        {"template": _choice(ANALYTICS_TEMPLATES), "kind": _text(128)},
    ),
    "codecompass.rlm_analyze": (
        ("query",),
        {
            "query": _text(MAX_QUERY_CHARS),
            "enabled": _boolean,
            "max_depth": _integer(1, 2),
            "max_fanout": _integer(1, 3),
        },
    ),
}

def enabled_tools(environ=None):
    """The hard allowlist, optionally narrowed by the operator (never widened)."""
    raw = (environ if environ is not None else os.environ).get("ANANTA_MEET_COMPANION_MCP_TOOLS")
    if raw is None or not raw.strip() or raw.strip().lower() == "all":
        return frozenset(COMPANION_TOOLS)
    if raw.strip().lower() == "none":
        return frozenset()
    return frozenset(name.strip() for name in raw.split(",")) & frozenset(COMPANION_TOOLS)


def normalize_arguments(name, arguments):
    """Closed, bounded arguments for ``name``; ``ValueError(reason)`` otherwise."""
    required, fields = COMPANION_TOOLS[name]
    if not isinstance(arguments, dict):
        raise ValueError("arguments_not_object")
    unknown = sorted(set(arguments) - set(fields))
    if unknown:
        raise ValueError("argument_unknown")
    from agent.services.codecompass_authority_policy import contains_client_authority

    if contains_client_authority(arguments):
        raise ValueError("client_authority_forbidden")
    normalized = {}
    for field, value in arguments.items():
        try:
            normalized[field] = fields[field](value)
        except ValueError:
            raise ValueError("argument_invalid:" + field) from None
    missing = [field for field in required if field not in normalized]
    if missing:
        raise ValueError("argument_missing:" + missing[0])
    return normalized


def _prune(value, depth=0):
    """Bound depth, breadth and string size before anything is serialized."""
    if depth >= 6:
        return "…"
    if isinstance(value, dict):
        items = list(value.items())[:40]
        return {str(key)[:64]: _prune(item, depth + 1) for key, item in items}
    if isinstance(value, (list, tuple)):
        return [_prune(item, depth + 1) for item in list(value)[:20]]
    if isinstance(value, str):
        return value[:400]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]


def bounded_text(value, limit=MAX_RESULT_CHARS):
    """``(text, truncated)``: compact JSON of the pruned value, at most ``limit`` chars."""
    text = json.dumps(_prune(value), ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def evidence(value, limit=6):
    """Citable ``{path, line, symbol, excerpt}`` entries found in a tool result (bounded walk)."""
    found, stack = [], [(value, 0)]
    while stack and len(found) < limit:
        item, depth = stack.pop(0)
        if depth > 6:
            continue
        if isinstance(item, dict):
            path = item.get("path")
            if isinstance(path, str) and path.strip():
                line = item.get("line") if item.get("line") is not None else item.get("line_start")
                found.append(
                    {
                        "path": path.strip()[:512],
                        "line": line if type(line) is int and 0 < line < 10_000_000 else None,
                        "symbol": str(item.get("symbol") or "")[:256],
                        "revision": str(item.get("revision") or "")[:64],
                        "excerpt": str(
                            item.get("excerpt") or item.get("short_summary") or item.get("title") or ""
                        )[:400],
                    }
                )
                continue
            stack.extend((nested, depth + 1) for nested in list(item.values())[:40])
        elif isinstance(item, (list, tuple)):
            stack.extend((nested, depth + 1) for nested in list(item)[:40])
    return found


def _result_payload(result):
    """The tool's JSON out of the registry's MCP ``content`` envelope."""
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list) and content and isinstance(content[0], dict) and "json" in content[0]:
        return content[0]["json"]
    return result

