"""Re-export of agent.common.sgpt_workspace_mutation as agent.cli_backends.workspace_mutation."""
from __future__ import annotations

from agent.common.sgpt_workspace_mutation import (  # noqa: F401
    _build_iteration_prompt,
    _build_mode_instructions,
    _changes_signature,
    _evidence_signature,
    get_workspace_mutation_config,
    parse_mutation_output,
    run_ananta_worker_workspace_mutation,
)
