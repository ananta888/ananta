"""agent.cli_backends.workspace_mutation — workspace-mutation loop package.

The workspace-mutation loop is split into 4 sub-modules:
- signatures: stable-hashing helpers for evidence + change-sets
- prompts: parse_mutation_output, build_mode_instructions, build_iteration_prompt
- _orchestrator: the run_ananta_worker_workspace_mutation mega-function
  (kept in a sub-module to keep the main __init__ thin)

All public symbols are re-exported from agent.cli_backends.workspace_mutation
so callers can use the package as a single import surface.
"""
from __future__ import annotations

from agent.cli_backends.workspace_mutation._orchestrator import (  # noqa: F401, E402
    KIND_PATCH_REQUEST,
    KIND_WORKSPACE_WRITE,
    get_workspace_mutation_config,
    run_ananta_worker_workspace_mutation,
)
from agent.cli_backends.workspace_mutation.prompts import (  # noqa: F401
    build_iteration_prompt,
    build_mode_instructions,
    parse_mutation_output,
)
from agent.cli_backends.workspace_mutation.signatures import (  # noqa: F401
    changes_signature,
    evidence_signature,
)
