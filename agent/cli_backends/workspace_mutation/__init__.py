"""Public re-exports for the workspace_mutation 4-split.

In Welle 2, this module re-exports from the 4 sub-modules
(signatures, prompts, loop, tools) AND from the source-of-truth
in agent.common.sgpt_workspace_mutation. The source migration
itself happens later; for now, the sub-modules provide the
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
