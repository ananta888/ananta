"""WorkerResult v2: unified result type for native worker paths. AWF-T036."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_VALID_STATUSES = frozenset({
    "success", "partial_success", "denied", "needs_approval",
    "failed", "degraded", "cancelled", "timeout",
})

_NATIVE_STATUS_MAP = {
    "passed": "success",
    "ok": "success",
    "success": "success",
    "failed": "failed",
    "error": "failed",
    "degraded": "degraded",
    "blocked": "denied",
    "denied": "denied",
    "needs_approval": "needs_approval",
    "cancelled": "cancelled",
    "timeout": "timeout",
    "partial": "partial_success",
    "partial_success": "partial_success",
}


@dataclass
class WorkerResultV2:
    """Unified worker result. AWF-T036.

    Statuses: success | partial_success | denied | needs_approval | failed | degraded | cancelled | timeout
    """
    status: str
    reason_code: str | None = None
    summary: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    trace_bundle_ref: str | None = None
    warnings: list[str] = field(default_factory=list)
    degraded_state: dict[str, Any] | None = None
    policy_observations: list[str] = field(default_factory=list)
    no_side_effects_confirmed: bool = False
    follow_up_tasks: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"invalid_worker_result_status:{self.status!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "worker_result.v2",
            "status": self.status,
            "reason_code": self.reason_code,
            "summary": self.summary,
            "artifacts": list(self.artifacts),
            "trace_bundle_ref": self.trace_bundle_ref,
            "warnings": list(self.warnings),
            "degraded_state": self.degraded_state,
            "policy_observations": list(self.policy_observations),
            "no_side_effects_confirmed": self.no_side_effects_confirmed,
            "follow_up_tasks": list(self.follow_up_tasks),
        }


def map_native_result_to_v2(native_result: dict[str, Any]) -> WorkerResultV2:
    """Map a native runtime result dict to WorkerResultV2. AWF-T036."""
    raw_status = str(native_result.get("status") or "failed").lower().strip()
    status = _NATIVE_STATUS_MAP.get(raw_status, "failed")
    if status not in _VALID_STATUSES:
        status = "failed"

    return WorkerResultV2(
        status=status,
        reason_code=str(native_result.get("reason") or native_result.get("reason_code") or "").strip() or None,
        summary=str(native_result.get("summary") or native_result.get("reason") or "").strip() or None,
        artifacts=list(native_result.get("artifacts") or []),
        trace_bundle_ref=str(native_result.get("trace_id") or native_result.get("trace_bundle_ref") or "").strip() or None,
        warnings=list(native_result.get("warnings") or []),
        degraded_state=native_result.get("degraded_state"),
        no_side_effects_confirmed=bool(native_result.get("no_side_effects_confirmed", False)),
        policy_observations=list(native_result.get("policy_observations") or []),
        follow_up_tasks=list(native_result.get("follow_up_tasks") or []),
    )
