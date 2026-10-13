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

from flask import current_app, jsonify, request

from agent.services.hub_tool_gateway import GatewayTimeout, audit_tool_call, run_bounded, timeout_seconds
from agent.services.meet_companion_tool_contract import (
    ANALYTICS_TEMPLATES as ANALYTICS_TEMPLATES,
)
from agent.services.meet_companion_tool_contract import (
    COMPANION_TOOLS as COMPANION_TOOLS,
)
from agent.services.meet_companion_tool_contract import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    SCHEMA,
    _result_payload,
    bounded_text,
    enabled_tools,
    evidence,
    normalize_arguments,
)
from agent.services.meet_companion_tool_contract import (
    MAX_QUERY_CHARS as MAX_QUERY_CHARS,
)
from agent.services.meet_companion_tool_contract import (
    MAX_RESULT_CHARS as MAX_RESULT_CHARS,
)
from agent.services.meet_companion_tool_contract import (
    RETRIEVE_MODES as RETRIEVE_MODES,
)
from agent.services.meet_contract import MeetError


def _timeout():
    return timeout_seconds("ANANTA_MEET_COMPANION_TOOL_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)


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
    audit_tool_call("meet_companion_tool_called", name, outcome=outcome, trace_id=trace_id,
                    target_scope={"project_id": project}, actor_role="meet_companion", details=details)


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
    details = {"arguments_keys": sorted(arguments), "operation_id": descriptor.operation_id if descriptor else None}
    try:
        result, snippets = run_bounded(app, lambda: _execute(name, arguments, capability, project), _timeout())
    except GatewayTimeout:
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
