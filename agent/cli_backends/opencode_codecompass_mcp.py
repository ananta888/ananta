"""Expose the existing read-only CodeCompass tool boundary to an OpenCode turn."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy

from agent.cli_backends.codecompass_mcp_transport import McpEndpoint, RpcObject, serve_mcp
from agent.cli_backends.context import default_context as _ctx
from agent.cli_backends.tool_loop import get_tool_loop_config
from agent.config import settings


@contextmanager
def opencode_codecompass_mcp(*, workspace_dir: str, task_id: str | None = None) -> Iterator[McpEndpoint]:
    """Snapshot trusted task context before starting the HTTP request threads."""
    capabilities = _ctx.codecompass_task_capability
    cfg = deepcopy(capabilities.ensure_tool_context(get_tool_loop_config(), task_id))
    capability = cfg.get("codecompass_capability")
    bound_task_id = str(cfg.get("codecompass_task_id") or (capability or {}).get("task_id") or "")
    cfg["codecompass_task_id"] = bound_task_id
    registry = _ctx.ananta_tool_registry_service
    specs = {
        spec.name: spec for spec in registry.list_tools(category="read_only") if spec.name.startswith("codecompass.")
    }
    tools = [
        {"name": name, "description": spec.description, "inputSchema": spec.argument_schema}
        for name, spec in specs.items()
    ]

    def call(params: RpcObject, call_id: str) -> RpcObject:
        name = str(params.get("name") or "")
        arguments = params.get("arguments", {})
        error = ""
        if name not in specs or not isinstance(arguments, dict):
            error = "unknown_tool_or_invalid_arguments"
        elif not bound_task_id:
            error = "codecompass_task_required"
        else:
            decision = _ctx.ananta_tool_policy_service.evaluate(
                tool_name=name,
                arguments=arguments,
                allowed_tools=cfg.get("allowed_tools"),
                task_id=bound_task_id,
                mutation_mode="read_only",
            )
            if not decision.allowed:
                error = decision.reason
            mode = capabilities.effective_access_mode(cfg, name, has_capability=isinstance(capability, dict))
            local_execution = mode == "delegated" or (mode == "hub" and settings.role != "worker")
            if not error and local_execution:
                try:
                    _ctx.codecompass_retrieval_capability.verify_retrieval_capability(capability)
                except ValueError as refusal:
                    error = str(refusal)
                if not error and (
                    capability.get("task_id") != bound_task_id
                    or f"ananta.tool.{name}" not in capability.get("allowed_operations", [])
                ):
                    error = "capability_task_or_operation_not_allowed"
        if error:
            result = {"status": "error", "error": error}
        else:
            result = _ctx.ananta_tool_executor(
                tool_name=name, arguments=arguments, workspace_dir=workspace_dir, tool_call_id=call_id, config=cfg
            )
        return {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=True)}],
            "isError": result.get("status") == "error" or bool(result.get("error")),
        }

    def dispatch(request: RpcObject) -> RpcObject | None:
        request_id = request.get("id")
        method = request.get("method")
        if request_id is None:
            return None
        response: RpcObject = {"jsonrpc": "2.0", "id": request_id}
        params = request.get("params") or {}
        if request.get("jsonrpc") != "2.0" or not isinstance(params, dict):
            response["error"] = {"code": -32600, "message": "Invalid Request"}
        elif method == "initialize":
            response["result"] = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ananta-codecompass", "version": "1.0.0"},
            }
        elif method == "ping":
            response["result"] = {}
        elif method == "tools/list":
            response["result"] = {"tools": tools}
        elif method == "tools/call":
            response["result"] = call(params, str(request_id))
        else:
            response["error"] = {"code": -32601, "message": "Method not found"}
        return response

    with serve_mcp(dispatch) as endpoint:
        yield endpoint
