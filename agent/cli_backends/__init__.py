"""agent.cli_backends — LLM-CLI backend subsystem public API.

This package is the **public API** for the LLM-CLI backend subsystem
(sgpt, opencode, codex, aider, mistral and optional coding agents). It is also the source of truth
for backend orchestration code after the SGDEC migration.

Service-owned dependencies are resolved through
``agent.cli_backends.context.CliBackendContext``.

See:
- .hermes/plans/decouple-sgpt-from-services.md
- todos/todo.sgpt-decouple-from-services.json
"""
from __future__ import annotations

__all__ = [
    "context",
    "coding_agent_contract",
    "coding_agent_process",
    "coding_agent_profiles",
    "coding_agent_selection",
    "jules",
    "helpers",
    "routing",
    "semaphore",
    "tool_loop",
    "workspace_mutation",
    "opencode",
    "architecture_scan",
    "sgpt",
]
