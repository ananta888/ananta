"""Re-Export-Shim for agent.common.sgpt_backend_routing.

DEPRECATED: Import from agent.cli_backends.routing instead.
This shim is removed in Welle 4 of the SGDEC migration.
"""
from __future__ import annotations

from agent.cli_backends.routing import (  # noqa: F401
    _BACKEND_RUNTIME,
    _choose_candidates,
    _configured_backend_command,
    _health_score,
    _resolve_backend_binary,
    CLI_BACKEND_CAPABILITIES,
    CLI_BACKEND_INSTALL_HINTS,
    CLI_BACKEND_VERIFY_COMMANDS,
    SUPPORTED_CLI_BACKENDS,
    get_cli_backend_capabilities,
    get_cli_backend_preflight,
    get_cli_backend_runtime_status,
    get_research_backend_preflight,
    normalize_backend_flags,
)
