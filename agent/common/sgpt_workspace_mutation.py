"""Re-Export-Shim for agent.common.sgpt_workspace_mutation.

DEPRECATED: Import from agent.cli_backends.workspace_mutation instead.
This shim is removed in Welle 4 of the SGDEC migration.
"""
from __future__ import annotations

from agent.cli_backends.workspace_mutation import (  # noqa: F401
    KIND_PATCH_REQUEST,
    KIND_WORKSPACE_WRITE,
    build_iteration_prompt,
    build_mode_instructions,
    changes_signature,
    evidence_signature,
    get_workspace_mutation_config,
    parse_mutation_output,
    run_ananta_worker_workspace_mutation,
)
