"""Re-export of agent.common.sgpt as agent.cli_backends.sgpt.

The Welle 1 contract: agent.cli_backends.sgpt is the public API for
the LLM-CLI backend's top-level entry points (run_sgpt_command,
run_llm_cli_command, _run_ananta_worker_iterative). Source of truth
remains agent.common.sgpt in Welle 1.
"""
from __future__ import annotations

from agent.common.sgpt import (  # noqa: F401
    _run_ananta_worker_iterative,
    run_llm_cli_command,
    run_sgpt_command,
)
