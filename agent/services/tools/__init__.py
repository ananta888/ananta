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
            return codecompass_search(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.expand_graph":
            from agent.services.tools.codecompass_tools import codecompass_expand_graph
            return codecompass_expand_graph(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
        if name == "codecompass.architecture_query":
            from agent.services.tools.codecompass_tools import codecompass_architecture_query
            return codecompass_architecture_query(workspace_dir=workspace_dir, arguments=args, tool_call_id=tool_call_id)
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
    except Exception as exc:  # tool bugs must not crash the worker loop
        return build_tool_result(
            tool_name=name, tool_call_id=tool_call_id, status="error", error=f"tool_execution_failed:{exc}"
        )
    return build_tool_result(
        tool_name=name, tool_call_id=tool_call_id, status="error", error="tool_not_implemented"
    )
