"""Re-Export-Shim for agent.common.sgpt_backend_semaphore.

DEPRECATED: Import from agent.cli_backends.semaphore instead.
This shim is removed in Welle 4 of the SGDEC migration.
"""
from __future__ import annotations

from agent.cli_backends.semaphore import (  # noqa: F401
    _BACKEND_SEMAPHORES,
    _SemaphoreTicket,
    _acquire_backend_permit,
    _get_backend_semaphore,
    _resolve_backend_parallel_limit,
)
