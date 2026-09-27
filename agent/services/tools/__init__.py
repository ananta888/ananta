"""Deterministic tool executors for the ananta-worker tool calling loop.

``execute_ananta_tool`` is the single hub-side dispatch point: the tool
loop calls it only after the policy gate
(``agent/services/ananta_tool_policy_service.py``) allowed the request.
Unknown tools return an error ToolResult — they are already rejected by
the gate, this is defense in depth.
"""
from __future__ import annotations

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


def _dispatch_ananta_tool(name: str, args: dict[str, Any], cfg: dict[str, Any], workspace_dir: str,
                          tool_call_id: str) -> dict[str, Any]:
    try:
        if name == "repo.list_files":
            from agent.services.tools.repo_tools import repo_list_files
            return repo_list_files(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "repo.read_file_range":
            from agent.services.tools.repo_tools import repo_read_file_range
            return repo_read_file_range(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "repo.grep":
            from agent.services.tools.repo_tools import repo_grep
            return repo_grep(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "git.status":
            from agent.services.tools.repo_tools import git_status
            return git_status(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "git.diff_readonly":
            from agent.services.tools.repo_tools import git_diff_readonly
            return git_diff_readonly(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.search":
            from agent.services.tools.codecompass_tools import codecompass_search
            return codecompass_search(
                workspace_dir=workspace_dir,
                arguments=args,
                tool_call_id=tool_call_id,
                config=cfg,
            )
        if name == "codecompass.retrieve":
            from agent.services.tools.codecompass_tools import codecompass_retrieve
            return codecompass_retrieve(
                workspace_dir=workspace_dir,
                arguments=args,
                tool_call_id=tool_call_id,
                config=cfg,
            )
        if name == "codecompass.architecture_overview":
            from agent.services.tools.codecompass_architecture_tools import codecompass_architecture_overview
            return codecompass_architecture_overview(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.architecture_expand":
            from agent.services.tools.codecompass_architecture_tools import codecompass_architecture_expand
            return codecompass_architecture_expand(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.component_context":
            from agent.services.tools.codecompass_architecture_tools import codecompass_component_context
            return codecompass_component_context(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.architecture_dependencies":
            from agent.services.tools.codecompass_architecture_tools import codecompass_architecture_dependencies
            return codecompass_architecture_dependencies(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.symbol_context":
            from agent.services.tools.codecompass_architecture_tools import codecompass_symbol_context
            return codecompass_symbol_context(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.architecture_evidence":
            from agent.services.tools.codecompass_architecture_tools import codecompass_architecture_evidence
            return codecompass_architecture_evidence(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.architecture_diagram":
            from agent.services.tools.codecompass_architecture_tools import codecompass_architecture_diagram
            return codecompass_architecture_diagram(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.rlm_analyze":
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
        if name == "codecompass.resolve_context":
            from agent.services.tools.codecompass_tools import codecompass_resolve_context
            return codecompass_resolve_context(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.search_symbols":
            from agent.services.tools.codecompass_tools import codecompass_search_symbols
            return codecompass_search_symbols(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.plan_context":
            from agent.services.tools.codecompass_tools import codecompass_plan_context
            return codecompass_plan_context(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.expand_graph":
            from agent.services.tools.codecompass_tools import codecompass_expand_graph
            return codecompass_expand_graph(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.get_file_context":
            from agent.services.tools.codecompass_tools import codecompass_get_file_context
            return codecompass_get_file_context(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.get_domain_map":
            from agent.services.tools.codecompass_tools import codecompass_get_domain_map
            return codecompass_get_domain_map(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.architecture_query":
            from agent.services.tools.codecompass_tools import codecompass_architecture_query
            return codecompass_architecture_query(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.semantic_equivalents":
            from agent.services.tools.codecompass_tools import codecompass_semantic_equivalents
            return codecompass_semantic_equivalents(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.translation_plan":
            from agent.services.tools.codecompass_tools import codecompass_translation_plan
            return codecompass_translation_plan(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.verify_translation":
            from agent.services.tools.codecompass_tools import codecompass_verify_translation
            return codecompass_verify_translation(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.python_translation_plan":
            from agent.services.tools.codecompass_tools import codecompass_python_translation_plan
            return codecompass_python_translation_plan(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "test.discover":
            from agent.services.tools.test_tools import test_discover
            return test_discover(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "test.run":
            from agent.services.tools.test_tools import test_run
            return test_run(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id, config=cfg)
        if name == "repo.apply_patch":
            from agent.services.tools.workspace_mutation_tools import repo_apply_patch
            return repo_apply_patch(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id, config=cfg)
        if name == "repo.write_file":
            from agent.services.tools.workspace_mutation_tools import repo_write_file
            return repo_write_file(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id, config=cfg)
        if name == "workspace.diff":
            from agent.services.tools.workspace_mutation_tools import workspace_diff
            return workspace_diff(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id, config=cfg)
        if name in {"opencode.propose", "hermes.review", "aider.propose", "codex.propose"}:
            from agent.services.tools.external_backend_tools import run_external_backend_tool
            return run_external_backend_tool(
                tool_name=name,
                workspace_dir=workspace_dir,
                arguments=args,
                tool_call_id=tool_call_id,
                config=cfg,
            )
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
