"""Re-export of agent.common.sgpt_helpers as agent.cli_backends.helpers.

In Welle 1, this re-exports from agent.common.sgpt_helpers (which remains
the source of truth). In Welle 2 the source moves here and the re-export
direction flips.
"""
from __future__ import annotations

from agent.common.sgpt_helpers import (  # noqa: F401
    _classify_runtime_target,
    _get_agent_config,
    _get_runtime_default_provider,
    _get_runtime_provider_urls,
    _is_probably_local_base_url,
    _normalize_ollama_openai_base_url,
    _normalize_openai_base_url,
    _resolve_openai_compatible_base_url,
    _resolve_profile_api_key,
)
