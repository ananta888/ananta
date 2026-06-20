"""Public re-exports for the workspace_mutation 4-split.

In Welle 2, this module re-exports from the 4 sub-modules
(signatures, prompts, loop, tools) AND from the source-of-truth
in agent.common.sgpt_workspace_mutation. The source migration
itself happens in Welle 3; for now, the sub-modules provide the
canonical implementations of the small helpers, and the main
agent.common.sgpt_workspace_mutation.py delegates to them.
"""
from __future__ import annotations

from agent.cli_backends.workspace_mutation.prompts import (  # noqa: F401
    build_iteration_prompt,
    build_mode_instructions,
    parse_mutation_output,
)
from agent.cli_backends.workspace_mutation.signatures import (  # noqa: F401
    changes_signature,
    evidence_signature,
)

# The main orchestrator + config resolver are still in the legacy source
# (agent.common.sgpt_workspace_mutation). Re-export them so callers can
# use the new namespace exclusively.
from agent.common.sgpt_workspace_mutation import (  # noqa: E402, F401
    KIND_PATCH_REQUEST,
    KIND_WORKSPACE_WRITE,
    get_workspace_mutation_config,
    run_ananta_worker_workspace_mutation,
)
