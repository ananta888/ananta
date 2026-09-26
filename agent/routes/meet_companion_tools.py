"""Worker-only CodeCompass MCP tools for the Meet companion (``/internal/assist/tool``).

The media worker holds no user or backend credentials, so it cannot speak to
``/v1/mcp``. This route is the narrow substitute: one CodeCompass *read* tool
per request, authenticated with the scoped Meet worker key exactly like
``/internal/assist/retrieve``, executed server-side through the same MCP
registry dispatch the Hub-MCP uses.

Every request passes three gates, all fail-closed:

1. a hard allowlist of read-only CodeCompass tools (``COMPANION_TOOLS``), which
   the operator may narrow with ``ANANTA_MEET_COMPANION_MCP_TOOLS`` but never
   widen;
2. the companion identity, which exists only for a project inside the media
   scope list (``MeetTurnService.companion_principal``);
3. the operator's operation policy (``OperationPolicyService.decide``) for the
   registered ``mcp.tool`` descriptor, evaluated as a non-admin machine caller
   without approval, so a denied, write/admin or high-risk operation never runs.

Arguments are closed and bounded per tool, the call has a wall-clock timeout
and the result is pruned and serialized to a bounded text block.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from flask import current_app, jsonify, request

from agent.services.meet_contract import MeetError

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

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="meet-companion-tool")


def enabled_tools(environ=None):
    """The hard allowlist, optionally narrowed by the operator (never widened)."""
    raw = (environ if environ is not None else os.environ).get("ANANTA_MEET_COMPANION_MCP_TOOLS")
    if raw is None or not raw.strip() or raw.strip().lower() == "all":
        return frozenset(COMPANION_TOOLS)
    if raw.strip().lower() == "none":
        return frozenset()
    return frozenset(name.strip() for name in raw.split(",")) & frozenset(COMPANION_TOOLS)


def _timeout():
    try:
        value = float(os.environ.get("ANANTA_MEET_COMPANION_TOOL_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return max(1.0, min(60.0, value))


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


def _decide(name):
    """Operator operation policy for the registered MCP descriptor (non-admin machine caller)."""
    from agent.services.operation_policy_service import OperationAuthContext, get_operation_policy_service
    from agent.services.operation_registry_service import get_operation_registry_service

    descriptor = get_operation_registry_service().get_for_target(transport="mcp.tool", target=name)
    policy_service = get_operation_policy_service()
    policy = policy_service.resolve_policy(current_app.config.get("AGENT_CONFIG", {}) or {})
    decision = policy_service.decide(
        descriptor, policy, OperationAuthContext(auth_source="agent_auth", is_admin=False, approval_granted=False)
    )
    if decision.allowed and (
        descriptor is None or descriptor.access_class != "read" or descriptor.side_effecting
    ):
        # Belt and braces: a registry change must not turn a companion tool into a mutation.
        return descriptor, decision, False, policy
    return descriptor, decision, decision.allowed, policy


def _audit(name, *, outcome, trace_id, project, details):
    from agent.common.audit import log_audit
    from agent.services.execution_audit_service import get_execution_audit_service

    log_audit("meet_companion_tool_called", {"tool": name, "outcome": outcome, "trace_id": trace_id, **details})
    get_execution_audit_service().emit_tool_call(
        trace_id=trace_id,
        parent_trace_id=None,
        tool_name=name,
        target_scope={"project_id": project},
        outcome=outcome,
        actor_role="meet_companion",
        details=details,
    )


def _execute(name, arguments, capability, project=None):
    """Dispatch through the Hub-MCP registry, or the assist retrieval path for retrieve."""
    if name == "codecompass.retrieve":
        # Same knowledge-index path as /internal/assist/retrieve: citable
        # snippets (path/line/symbol/revision) instead of the capability-sealed
        # agentic retrieval, which fails closed for a machine principal.
        from agent.routes.meet import assist_retrieval

        result = assist_retrieval(arguments["query"], arguments.get("limit", 5), project_id=project)
        return result, result["snippets"]
    from agent.services.mcp_registry_service import get_mcp_registry_service

    result = get_mcp_registry_service().call_tool(
        name=name, arguments=dict(arguments), context={"codecompass_capability": capability}
    )
    return _result_payload(result), None


def companion_tool():
    """POST ``/internal/assist/tool``: ``{project_id, tool, arguments}`` -> one bounded tool result."""
    from agent.common.logging import get_correlation_id
    from agent.routes.meet import _runtime
    from worker.meet_media.contract import authenticate

    _runtime()
    key = current_app.extensions.get("meet_media_worker_key")
    turns = current_app.extensions.get("meet_turn_service")
    if key is None or turns is None:
        raise MeetError("meet_media_disabled", 404)
    if request.args or request.content_length is None or not 0 < request.content_length <= MAX_BODY_BYTES:
        raise MeetError("meet_tool_payload_invalid")
    raw = request.get_data(cache=False)
    try:
        authenticate(key, raw, request.headers.get("X-Ananta-Task-Signature", ""))
        payload = json.loads(raw)
    except (ValueError, TypeError):
        raise MeetError("meet_tool_unauthorized", 401) from None
    if not isinstance(payload, dict) or not set(payload) <= {"project_id", "tool", "arguments"}:
        raise MeetError("meet_tool_payload_invalid")
    project, name = payload.get("project_id"), payload.get("tool")
    arguments = payload.get("arguments", {})
    if not isinstance(project, str) or not project or not isinstance(name, str) or not name:
        raise MeetError("meet_tool_payload_invalid")
    trace_id = get_correlation_id()
    if name not in enabled_tools():
        _audit(name[:64], outcome="blocked", trace_id=trace_id, project=project, details={"reason": "not_allowlisted"})
        raise MeetError("meet_tool_denied", 403)
    principal = turns.companion_principal(project)  # 403 outside the media scopes
    descriptor, decision, allowed, policy = _decide(name)
    from agent.services.operation_policy_observability_service import get_operation_policy_observability_service

    get_operation_policy_observability_service().record(
        decision,
        trace_id=trace_id,
        surface="meet_companion",
        emit_audit_event=bool(policy.get("emit_audit_events", True)),
    )
    if not allowed:
        reason = decision.reason_code if not decision.allowed else "operation_not_read_only"
        _audit(name, outcome="blocked", trace_id=trace_id, project=project, details={"reason": reason})
        raise MeetError("meet_tool_denied", 403)
    try:
        arguments = normalize_arguments(name, arguments)
    except ValueError as error:
        _audit(name, outcome="invalid", trace_id=trace_id, project=project, details={"reason": str(error)})
        raise MeetError("meet_tool_arguments_invalid") from None

    from agent.services.codecompass_retrieval_capability_service import resolve_request_capability

    capability = resolve_request_capability(application=current_app, principal=principal, requested_scope={})
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            return _execute(name, arguments, capability, project)

    details = {"arguments_keys": sorted(arguments), "operation_id": descriptor.operation_id if descriptor else None}
    try:
        result, snippets = _EXECUTOR.submit(run).result(timeout=_timeout())
    except FutureTimeout:
        _audit(name, outcome="timeout", trace_id=trace_id, project=project, details=details)
        raise MeetError("meet_tool_timeout", 504) from None
    except KeyError as error:
        _audit(name, outcome="not_found", trace_id=trace_id, project=project, details={**details, "reason": str(error)})
        raise MeetError("meet_tool_not_found", 404) from None
    except ValueError as error:
        _audit(name, outcome="invalid", trace_id=trace_id, project=project, details={**details, "reason": str(error)})
        raise MeetError("meet_tool_arguments_invalid") from None
    except Exception as error:  # noqa: BLE001 -- a failing tool is a coded result, not a 500
        reason = type(error).__name__
        _audit(name, outcome="error", trace_id=trace_id, project=project, details={**details, "reason": reason})
        raise MeetError("meet_tool_error", 502) from None
    text, truncated = bounded_text(result)
    _audit(name, outcome="success", trace_id=trace_id, project=project, details={**details, "truncated": truncated})
    response = {
        "schema": SCHEMA,
        "tool": name,
        "text": text,
        "truncated": truncated,
        "sources": snippets if snippets is not None else evidence(result),
        "trace_id": trace_id,
    }
    if isinstance(result, dict) and type(result.get("total")) is int:
        # Additive: total matching records for a retrieve, beyond the returned head.
        response["total"] = result["total"]
    return jsonify(response)


def register_routes(blueprint):
    blueprint.add_url_rule(
        "/internal/assist/tool", endpoint="media_assist_tool", view_func=companion_tool, methods=["POST"]
    )
