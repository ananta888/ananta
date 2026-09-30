"""Deterministic tool executors for the ananta-worker tool calling loop.

``execute_ananta_tool`` is the single hub-side dispatch point: the tool
loop calls it only after the policy gate
(``agent/services/ananta_tool_policy_service.py``) allowed the request.
Unknown tools return an error ToolResult — they are already rejected by
the gate, this is defense in depth.
"""
from __future__ import annotations

import importlib
from typing import Any

from agent.services.tools._evidence import build_tool_result

# tools that read the capability from their arguments (the MCP route passes it that way)
_CAPABILITY_ARGUMENT_TOOLS = frozenset({
    "codecompass.architecture_overview", "codecompass.architecture_expand", "codecompass.component_context",
    "codecompass.architecture_dependencies", "codecompass.symbol_context", "codecompass.architecture_evidence",
    "codecompass.architecture_diagram",
})


def _codecompass_route(name: str, cfg: dict[str, Any]) -> str:
    """``local``, ``hub`` (path A, only on a worker) or ``off`` for a CodeCompass tool call (WCRB-011)."""
    if not name.startswith("codecompass."):
        return "local"
    from agent.services.codecompass_task_capability import effective_access_mode

    mode = effective_access_mode(cfg, name, has_capability=isinstance(cfg.get("codecompass_capability"), dict))
    if mode == "off":
        return "off"
    if mode == "hub":
        from agent.config import settings

        return "hub" if settings.role == "worker" else "local"
    return "local"


def _guard_codecompass_authority(name: str, args: dict[str, Any], cfg: dict[str, Any],
                                 tool_call_id: str) -> dict[str, Any] | None:
    """Authority is server-owned (WCRB-007): a model never supplies a capability, token or collection, and
    the architecture tools get the trusted capability from the Hub-issued config only (set into ``args``)."""
    if not name.startswith("codecompass."):
        return None
    from agent.services.codecompass_authority_policy import contains_client_authority

    if contains_client_authority(args):
        return build_tool_result(tool_name=name, tool_call_id=tool_call_id, status="error",
                                 error="client_authority_forbidden")
    trusted = cfg.get("codecompass_capability")
    if isinstance(trusted, dict) and name in _CAPABILITY_ARGUMENT_TOOLS:
        args["capability"] = dict(trusted)
    return None


def execute_ananta_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None,
    workspace_dir: str,
    tool_call_id: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = str(tool_name or "").strip()
    args = dict(arguments or {})
    cfg = dict(config or {})
    route = _codecompass_route(name, cfg)
    if route == "off":
        return build_tool_result(tool_name=name, tool_call_id=tool_call_id, status="error",
                                 error="codecompass_access_off")
    if route == "hub":
        cfg.pop("codecompass_capability", None)  # the Hub issues its own; nothing is injected here
    refusal = _guard_codecompass_authority(name, args, cfg, tool_call_id)
    if refusal is not None:
        return refusal
    if route == "hub":
        from agent.services.codecompass_hub_tool_client import hub_tool_gateway_client

        return hub_tool_gateway_client().execute(task_id=str(cfg.get("codecompass_task_id") or ""),
                                                 tool_name=name, arguments=args, tool_call_id=tool_call_id)
    trusted = cfg.get("codecompass_capability")
    if not name.startswith("codecompass.") or not isinstance(trusted, dict):
        return _dispatch_ananta_tool(name, args, cfg, workspace_dir, tool_call_id)
    from agent.services.codecompass_task_capability import task_capability_scope

    # the trusted capability of this call, e.g. for fetching Hub graph artifacts on a worker (WCRB-010)
    with task_capability_scope(dict(trusted)):
        return _dispatch_ananta_tool(name, args, cfg, workspace_dir, tool_call_id)


_REPO_TOOLS = "agent.services.tools.repo_tools"
_CODECOMPASS_TOOLS = "agent.services.tools.codecompass_tools"
_CODECOMPASS_ARCHITECTURE_TOOLS = "agent.services.tools.codecompass_architecture_tools"
_TEST_TOOLS = "agent.services.tools.test_tools"
_WORKSPACE_MUTATION_TOOLS = "agent.services.tools.workspace_mutation_tools"
_EXTERNAL_BACKEND_TOOLS = "agent.services.tools.external_backend_tools"

# How a registered executor is called:
#   "workspace"            -> fn(workspace_dir=, arguments=, tool_call_id=)
#   "workspace_config"     -> fn(workspace_dir=, arguments=, tool_call_id=, config=)
#   "named_workspace_config" -> fn(tool_name=, workspace_dir=, arguments=, tool_call_id=, config=)
_WORKSPACE = "workspace"
_WORKSPACE_CONFIG = "workspace_config"
_NAMED_WORKSPACE_CONFIG = "named_workspace_config"

# tool name -> (module, executor function, call style); modules are imported lazily at call time.
_TOOL_EXECUTORS: dict[str, tuple[str, str, str]] = {
    "repo.list_files": (_REPO_TOOLS, "repo_list_files", _WORKSPACE),
    "repo.read_file_range": (_REPO_TOOLS, "repo_read_file_range", _WORKSPACE),
    "repo.grep": (_REPO_TOOLS, "repo_grep", _WORKSPACE),
    "git.status": (_REPO_TOOLS, "git_status", _WORKSPACE),
    "git.diff_readonly": (_REPO_TOOLS, "git_diff_readonly", _WORKSPACE),
    "codecompass.search": (_CODECOMPASS_TOOLS, "codecompass_search", _WORKSPACE_CONFIG),
    "codecompass.retrieve": (_CODECOMPASS_TOOLS, "codecompass_retrieve", _WORKSPACE_CONFIG),
    "codecompass.architecture_overview": (
        _CODECOMPASS_ARCHITECTURE_TOOLS, "codecompass_architecture_overview", _WORKSPACE),
    "codecompass.architecture_expand": (
        _CODECOMPASS_ARCHITECTURE_TOOLS, "codecompass_architecture_expand", _WORKSPACE),
    "codecompass.component_context": (
        _CODECOMPASS_ARCHITECTURE_TOOLS, "codecompass_component_context", _WORKSPACE),
    "codecompass.architecture_dependencies": (
        _CODECOMPASS_ARCHITECTURE_TOOLS, "codecompass_architecture_dependencies", _WORKSPACE),
    "codecompass.symbol_context": (_CODECOMPASS_ARCHITECTURE_TOOLS, "codecompass_symbol_context", _WORKSPACE),
    "codecompass.architecture_evidence": (
        _CODECOMPASS_ARCHITECTURE_TOOLS, "codecompass_architecture_evidence", _WORKSPACE),
    "codecompass.architecture_diagram": (
        _CODECOMPASS_ARCHITECTURE_TOOLS, "codecompass_architecture_diagram", _WORKSPACE),
    "codecompass.resolve_context": (_CODECOMPASS_TOOLS, "codecompass_resolve_context", _WORKSPACE),
    "codecompass.search_symbols": (_CODECOMPASS_TOOLS, "codecompass_search_symbols", _WORKSPACE),
    "codecompass.plan_context": (_CODECOMPASS_TOOLS, "codecompass_plan_context", _WORKSPACE),
    "codecompass.expand_graph": (_CODECOMPASS_TOOLS, "codecompass_expand_graph", _WORKSPACE),
    "codecompass.get_file_context": (_CODECOMPASS_TOOLS, "codecompass_get_file_context", _WORKSPACE),
    "codecompass.get_domain_map": (_CODECOMPASS_TOOLS, "codecompass_get_domain_map", _WORKSPACE),
    "codecompass.architecture_query": (_CODECOMPASS_TOOLS, "codecompass_architecture_query", _WORKSPACE),
    "codecompass.semantic_equivalents": (_CODECOMPASS_TOOLS, "codecompass_semantic_equivalents", _WORKSPACE),
    "codecompass.translation_plan": (_CODECOMPASS_TOOLS, "codecompass_translation_plan", _WORKSPACE),
    "codecompass.verify_translation": (_CODECOMPASS_TOOLS, "codecompass_verify_translation", _WORKSPACE),
    "codecompass.python_translation_plan": (
        _CODECOMPASS_TOOLS, "codecompass_python_translation_plan", _WORKSPACE),
    "test.discover": (_TEST_TOOLS, "test_discover", _WORKSPACE),
    "test.run": (_TEST_TOOLS, "test_run", _WORKSPACE_CONFIG),
    "repo.apply_patch": (_WORKSPACE_MUTATION_TOOLS, "repo_apply_patch", _WORKSPACE_CONFIG),
    "repo.write_file": (_WORKSPACE_MUTATION_TOOLS, "repo_write_file", _WORKSPACE_CONFIG),
    "workspace.diff": (_WORKSPACE_MUTATION_TOOLS, "workspace_diff", _WORKSPACE_CONFIG),
    **{
        name: (_EXTERNAL_BACKEND_TOOLS, "run_external_backend_tool", _NAMED_WORKSPACE_CONFIG)
        for name in ("opencode.propose", "hermes.review", "aider.propose", "codex.propose")
    },
}


def _call_registered_tool(name: str, args: dict[str, Any], cfg: dict[str, Any], workspace_dir: str,
                          tool_call_id: str) -> dict[str, Any]:
    module_name, function_name, call_style = _TOOL_EXECUTORS[name]
    executor = getattr(importlib.import_module(module_name), function_name)
    if call_style == _WORKSPACE:
        return executor(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
    if call_style == _WORKSPACE_CONFIG:
        return executor(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id, config=cfg)
    return executor(tool_name=name, workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id,
                    config=cfg)


def _codecompass_rlm_analyze(name: str, args: dict[str, Any], cfg: dict[str, Any],
                             tool_call_id: str) -> dict[str, Any]:
    from agent.services.codecompass_rlm_service import get_codecompass_rlm_service
    from agent.services.tools._evidence import build_tool_result

    query = str(args.get("query") or "").strip()
    if not query:
        return build_tool_result(tool_name=name, tool_call_id=tool_call_id, status="error", error="query_required")
    result = get_codecompass_rlm_service().analyze(
        query,
        capability=(
            cfg.get("codecompass_capability")
            if isinstance(cfg.get("codecompass_capability"), dict)
            else None
        ),
        enabled=bool(cfg.get("codecompass_rlm_enabled", args.get("enabled", False))),
        max_depth=int(args.get("max_depth") or 3),
        max_fanout=int(args.get("max_fanout") or 4),
    )
    return build_tool_result(
        tool_name=name,
        tool_call_id=tool_call_id,
        status="ok" if result.get("status") in {"executed", "eligible_false"} else "error",
        data={"rlm": result},
        warnings=list(result.get("warnings") or []),
        error=result.get("reason") if result.get("status") == "error" else None,
    )


def _dispatch_ananta_tool(name: str, args: dict[str, Any], cfg: dict[str, Any], workspace_dir: str,
                          tool_call_id: str) -> dict[str, Any]:
    try:
        if name in _TOOL_EXECUTORS:
            return _call_registered_tool(name, args, cfg, workspace_dir, tool_call_id)
        if name == "codecompass.rlm_analyze":
            return _codecompass_rlm_analyze(name, args, cfg, tool_call_id)
        if name.startswith("webcrawler."):
            from agent.services.tools.webcrawler_tools import run_webcrawler_tool

            return run_webcrawler_tool(
                tool_name=name,
                arguments=args,
                tool_call_id=tool_call_id,
                config=cfg,
            )
    except Exception as exc:  # tool bugs must not crash the worker loop
        return build_tool_result(
            tool_name=name, tool_call_id=tool_call_id, status="error", error=f"tool_execution_failed:{exc}"
        )
    return build_tool_result(
        tool_name=name, tool_call_id=tool_call_id, status="error", error="tool_not_implemented"
    )
