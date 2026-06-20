"""Re-Export-Shim for agent.common.sgpt.

DEPRECATED: Import from agent.cli_backends.sgpt instead.
This shim is removed in Welle 4 of the SGDEC migration.
"""
from __future__ import annotations

from agent.cli_backends.sgpt import (  # noqa: F401
    CLI_BACKEND_CAPABILITIES,
    CLI_BACKEND_INSTALL_HINTS,
    CLI_BACKEND_VERIFY_COMMANDS,
    SUPPORTED_CLI_BACKENDS,
    _BACKEND_RUNTIME,
    _choose_candidates,
    _run_ananta_worker_iterative,
    get_cli_backend_capabilities,
    get_cli_backend_preflight,
    get_cli_backend_runtime_status,
    get_research_backend_preflight,
    normalize_backend_flags,
    resolve_codex_runtime_config,
    resolve_opencode_runtime_config,
    run_aider_command,
    run_codex_command,
    run_llm_cli_command,
    run_mistral_code_command,
    run_opencode_command,
    run_sgpt_command,
)
