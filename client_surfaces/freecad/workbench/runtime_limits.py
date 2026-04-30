from __future__ import annotations

DEFAULT_TIMEOUT_SECONDS = 30
MAX_PROGRESS_STEPS = 10


def build_operation_state(*, status: str, progress_step: int = 0, cancel_requested: bool = False) -> dict[str, object]:
    normalized_step = max(0, min(progress_step, MAX_PROGRESS_STEPS))
    terminal = status in {"completed", "failed", "cancelled"}
    return {
        "status": status,
        "progress_step": normalized_step,
        "cancel_requested": bool(cancel_requested),
        "terminal": terminal,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
    }
