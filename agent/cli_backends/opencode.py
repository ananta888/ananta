"""Re-export of agent.common.sgpt_opencode as agent.cli_backends.opencode."""
from __future__ import annotations

from agent.common.sgpt_opencode import (  # noqa: F401
    _build_codex_runtime_diagnostics,
    _build_opencode_runtime_diagnostics,
    _build_opencode_theless_agent_config,
    _infer_local_opencode_target,
    _normalize_opencode_execution_mode,
    _normalize_opencode_tool_mode,
    _run_opencode_subprocess,
    _split_cli_model_identifier,
    resolve_codex_runtime_config,
    resolve_opencode_runtime_config,
    run_aider_command,
    run_codex_command,
    run_mistral_code_command,
    run_opencode_command,
)
