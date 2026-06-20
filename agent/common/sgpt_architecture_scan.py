"""Re-Export-Shim for agent.common.sgpt_architecture_scan.

DEPRECATED: Import from agent.cli_backends.architecture_scan instead.
This shim is removed in Welle 4 of the SGDEC migration.
"""
from __future__ import annotations

from agent.cli_backends.architecture_scan import (  # noqa: F401
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
