from __future__ import annotations

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.logging import get_correlation_id
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.services.system_health_service import build_system_health_payload

mcp_bp = Blueprint("mcp", __name__)


def _mcp_context() -> dict:
    services = get_core_services()
    repos = get_repository_registry()
    return {
        "health_builder": lambda basic_mode=True: build_system_health_payload(current_app, basic_mode=basic_mode),
        "openai_compat_service": services.openai_compat_service,
        "task_query_service": services.task_query_service,
        "task_repo": repos.task_repo,
        "artifact_repo": repos.artifact_repo,
        "knowledge_collection_repo": repos.knowledge_collection_repo,
        "evolution_service": services.evolution_service,
        "agent_config": current_app.config.get("AGENT_CONFIG", {}) or {},
        "evolution_config": dict((current_app.config.get("AGENT_CONFIG", {}) or {}).get("evolution") or {}),
    }


def _jsonrpc_error(*, req_id, code: int, message: str, data: dict | None = None, http_status: int = 200):
    payload = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    if isinstance(data, dict) and data:
        payload["error"]["data"] = data
    return payload, http_status


def _enforce_mcp_policy(operation: str):
    is_agent_auth = bool(getattr(g, "auth_payload", None))
    is_user_auth = bool(getattr(g, "user", None))
    decision = get_exposure_policy_service().evaluate_mcp_access(
        cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        is_agent_auth=is_agent_auth,
        is_user_auth=is_user_auth,
        is_admin=bool(getattr(g, "is_admin", False)),
        operation=operation,
    )
    if decision.allowed:
        return None
    if decision.policy.get("emit_audit_events", True):
        log_audit(
            "mcp_access_blocked",
            {"reason": decision.reason, "auth_source": decision.auth_source, "operation": operation},
        )
    return _jsonrpc_error(
        req_id=None,
        code=-32000,
        message="forbidden",
        data={"details": decision.reason, "auth_source": decision.auth_source, "operation": operation},
        http_status=403,
    )


@mcp_bp.route("/v1/mcp/capabilities", methods=["GET"])
@check_auth
def mcp_capabilities():
    blocked = _enforce_mcp_policy("capabilities")
    if blocked:
        return blocked
    policy = get_exposure_policy_service().resolve_mcp_policy(current_app.config.get("AGENT_CONFIG", {}) or {})
    registry = get_core_services().mcp_registry_service
    adapters = get_core_services().integration_registry_service.list_exposure_adapters(
        cfg=current_app.config.get("AGENT_CONFIG", {}) or {}
    )
    adapter_entry = next((item for item in adapters if item.get("adapter") == "mcp"), None)
    payload = {
        "object": "ananta.mcp.capabilities",
        "exposure_mode": "mcp",
        "policy": policy,
        "features": {
            "tools": True,
            "resources": True,
            "jsonrpc": True,
        },
        "counts": {
            "tools": len(registry.list_tools()),
            "resources": len(registry.list_resources()),
        },
        "adapter_registry": adapter_entry or {},
    }
    if policy.get("emit_audit_events", True):
        log_audit("mcp_capabilities_read", {"trace_id": get_correlation_id()})
    return payload


@mcp_bp.route("/v1/mcp", methods=["POST"])
@check_auth
def mcp_jsonrpc():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _jsonrpc_error(req_id=None, code=-32600, message="invalid_request", http_status=400)
    req_id = payload.get("id")
    method = str(payload.get("method") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if not method:
        return _jsonrpc_error(req_id=req_id, code=-32600, message="invalid_request", http_status=400)

    blocked = _enforce_mcp_policy(method)
    if blocked:
        forbidden_payload, status_code = blocked
        forbidden_payload["id"] = req_id
        return forbidden_payload, status_code

    registry = get_core_services().mcp_registry_service
    trace_id = get_correlation_id()
    try:
        if method == "tools/list":
            result = {"tools": registry.list_tools()}
        elif method == "resources/list":
            result = {"resources": registry.list_resources()}
        elif method == "tools/call":
            tool_name = str(params.get("name") or "").strip()
            if not tool_name:
                return _jsonrpc_error(
                    req_id=req_id, code=-32602, message="invalid_params", data={"details": "name_required"}
                )
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            result = registry.call_tool(name=tool_name, arguments=arguments, context=_mcp_context())
            log_audit("mcp_tool_called", {"tool": tool_name, "trace_id": trace_id})
        elif method == "resources/read":
            resource_uri = str(params.get("uri") or "").strip()
            if not resource_uri:
                return _jsonrpc_error(
                    req_id=req_id, code=-32602, message="invalid_params", data={"details": "uri_required"}
                )
            result = registry.read_resource(uri=resource_uri, context=_mcp_context())
            log_audit("mcp_resource_read", {"uri": resource_uri, "trace_id": trace_id})
        else:
            return _jsonrpc_error(req_id=req_id, code=-32601, message="method_not_found")
    except KeyError as exc:
        if str(exc) == "'unknown_tool'":
            return _jsonrpc_error(req_id=req_id, code=-32010, message="tool_not_found")
        if str(exc) == "'resource_not_found'":
            return _jsonrpc_error(req_id=req_id, code=-32011, message="resource_not_found")
        if str(exc) == "'task_not_found'":
            return _jsonrpc_error(req_id=req_id, code=-32012, message="task_not_found")
        return _jsonrpc_error(req_id=req_id, code=-32099, message="dispatch_error")
    except ValueError as exc:
        return _jsonrpc_error(req_id=req_id, code=-32602, message="invalid_params", data={"details": str(exc)})

    return {"jsonrpc": "2.0", "id": req_id, "result": result, "trace_id": trace_id}
