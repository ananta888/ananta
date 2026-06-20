"""Re-export of agent.common.sgpt_backend_routing as agent.cli_backends.routing."""
from __future__ import annotations

from agent.common.sgpt_backend_routing import (  # noqa: F401
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
