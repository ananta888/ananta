"""agent.cli_backends — Public API re-export layer for the LLM-CLI backend subsystem.

This package is the **public API** for the LLM-CLI backend subsystem
(sgpt, opencode, codex, aider, mistral). In Welle 1 of the SGDEC migration
it re-exports symbols from ``agent.common.sgpt_*`` which remains the
source of truth.

Migration plan:
- Welle 1: this package re-exports from agent.common.sgpt_*.
- Welle 2: source-of-truth moves into agent.cli_backends.*, shims flip.
- Welle 3: agent.common.sgpt_* shims deleted, this package is canonical.

See:
- .hermes/plans/decouple-sgpt-from-services.md
- todos/todo.sgpt-decouple-from-services.json
"""
from __future__ import annotations

__all__ = [
    "context",
    "helpers",
    "routing",
    "semaphore",
    "tool_loop",
    "workspace_mutation",
    "opencode",
    "architecture_scan",
    "sgpt",
]
