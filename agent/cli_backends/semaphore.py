"""Re-export of agent.common.sgpt_backend_semaphore as agent.cli_backends.semaphore."""
from __future__ import annotations

from agent.common.sgpt_backend_semaphore import (  # noqa: F401
    _BACKEND_SEMAPHORES,
    _SemaphoreTicket,
    _acquire_backend_permit,
    _get_backend_semaphore,
    _resolve_backend_parallel_limit,
)
