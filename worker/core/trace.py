from __future__ import annotations

import hashlib
from typing import Any

EXECUTION_CAPABLE_MODES = {"patch_propose", "patch_apply", "command_execute", "test_run", "verify"}


def stable_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def build_trace_metadata(
    *,
    trace_id: str,
    task_id: str,
    capability_id: str,
    context_hash: str,
    policy_decision_ref: dict[str, Any],
    approval_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_policy = dict(policy_decision_ref or {})
    metadata = {
        "trace_id": str(trace_id).strip(),
        "task_id": str(task_id).strip(),
        "capability_id": str(capability_id).strip(),
        "context_hash": str(context_hash).strip(),
        "policy_decision_ref": normalized_policy,
    }
    if approval_ref is not None:
        metadata["approval_ref"] = dict(approval_ref)
    return metadata


def ensure_trace_metadata(*, mode: str, metadata: dict[str, Any]) -> None:
    normalized_mode = str(mode or "").strip()
    if normalized_mode not in EXECUTION_CAPABLE_MODES:
        return
    required_fields = ("trace_id", "task_id", "capability_id", "context_hash", "policy_decision_ref")
    for field in required_fields:
        value = metadata.get(field)
        if isinstance(value, dict):
            if not value:
                raise ValueError(f"missing_trace_metadata:{field}")
            continue
        if not str(value or "").strip():
            raise ValueError(f"missing_trace_metadata:{field}")


def attach_trace_to_result(
    *,
    result: dict[str, Any],
    trace_metadata: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    ensure_trace_metadata(mode=mode, metadata=trace_metadata)
    merged = dict(result)
    merged["trace_id"] = str(trace_metadata["trace_id"]).strip()
    merged["task_id"] = str(trace_metadata["task_id"]).strip()
    merged["capability_id"] = str(trace_metadata["capability_id"]).strip()
    merged["context_hash"] = str(trace_metadata["context_hash"]).strip()
    merged["policy_decision_ref"] = dict(trace_metadata["policy_decision_ref"])
    if trace_metadata.get("approval_ref") is not None:
        merged["approval_ref"] = dict(trace_metadata["approval_ref"])
    return merged
