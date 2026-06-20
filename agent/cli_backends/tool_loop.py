"""Re-export of agent.common.sgpt_tool_loop as agent.cli_backends.tool_loop.

In Welle 1, this re-exports from agent.common.sgpt_tool_loop (source of truth).
In Welle 2, the source migrated the service imports through CliBackendContext.
The shim is removed in Welle 3.
"""
from __future__ import annotations

from agent.common.sgpt_tool_loop import (  # noqa: F401
    KIND_CANNOT_CONTINUE,
    KIND_FINAL_ANSWER,
    KIND_NEEDS_APPROVAL,
    KIND_TOOL_REQUEST,
    TOOL_LOOP_SCHEMA,
    _extract_json_candidate,
    _format_tool_result_block,
    _validate_tool_arguments,
    build_tool_loop_instructions,
    build_tool_loop_prompt,
    get_tool_loop_config,
    parse_worker_tool_output,
    register_pending_approval_request,
    run_ananta_worker_tool_loop,
)
