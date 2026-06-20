"""Re-export of agent.common.sgpt_architecture_scan as agent.cli_backends.architecture_scan."""
from __future__ import annotations

from agent.common.sgpt_architecture_scan import (  # noqa: F401
    _MAX_LINE_WINDOW,
    _bounded_worker_int,
    _build_iteration_prompt,
    _format_block_header,
    _is_architecture_full_scan_context,
    _load_source_file_batches,
    _read_research_context,
    _resolve_repo_root,
    _run_architecture_full_scan,
)
