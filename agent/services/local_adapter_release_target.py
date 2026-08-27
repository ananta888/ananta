"""Closed local-adapter release target contract.

The target is immutable lineage metadata.  It lets Hub policy distinguish
ordinary LoRA artifacts from candidates that require the stricter local
offline/shadow/canary lifecycle.
"""

from __future__ import annotations

from typing import Final

LOCAL_ADAPTER_RELEASE_TARGETS: Final = frozenset({"needle2", "lfm2.5-2.6b-agentic"})

_TARGET_BACKENDS: Final = {
    "needle2": "needle",
    "lfm2.5-2.6b-agentic": "peft_trl",
}


def normalize_local_adapter_release_target(
    value: object,
    *,
    job_type: str,
    backend: str,
) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in LOCAL_ADAPTER_RELEASE_TARGETS:
        raise ValueError("local_adapter_release_target_invalid")
    if job_type != "train_lora":
        raise ValueError("local_adapter_release_target_job_type_invalid")
    if backend != _TARGET_BACKENDS[normalized]:
        raise ValueError("local_adapter_release_target_backend_mismatch")
    return normalized


def requires_governed_local_release(value: object) -> bool:
    return str(value or "").strip().lower() in LOCAL_ADAPTER_RELEASE_TARGETS


__all__ = [
    "LOCAL_ADAPTER_RELEASE_TARGETS",
    "normalize_local_adapter_release_target",
    "requires_governed_local_release",
]
