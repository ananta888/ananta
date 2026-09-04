"""Hub-side capability inventory and deterministic Worker selection."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ananta_contracts.research_training import STAGE_CAPABILITIES, canonical_digest, require_id


class ResearchTrainingWorkerRegistry:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._workers: dict[str, dict[str, Any]] = {}

    def report(self, value: Mapping[str, Any]) -> dict[str, Any]:
        expected = {
            "worker_id",
            "state",
            "capabilities",
            "backend_versions",
            "gpu_count",
            "vram_bytes_per_gpu",
            "compute_capability",
            "supported_dtypes",
            "distributed_available",
            "storage_headroom_bytes",
            "expires_at_epoch",
        }
        if set(value) != expected:
            raise ValueError("research_worker_inventory_fields_invalid")
        worker_id = require_id(value.get("worker_id"), "worker_id")
        state = str(value.get("state") or "").strip().lower()
        if state not in {"available", "degraded", "unavailable"}:
            raise ValueError("research_worker_inventory_state_invalid")
        capabilities = self._ids(value.get("capabilities"), "capabilities")
        known_capabilities = set(STAGE_CAPABILITIES.values()) | {
            "multi_gpu_training",
            "code_exec_eval",
        }
        if any(item not in known_capabilities for item in capabilities):
            raise ValueError("research_worker_inventory_capability_unknown")
        backend_versions = value.get("backend_versions")
        if not isinstance(backend_versions, Mapping) or not backend_versions:
            raise ValueError("research_worker_backend_versions_invalid")
        normalized_versions = {
            require_id(key, "backend_name"): require_id(item, "backend_version")
            for key, item in backend_versions.items()
        }
        dtypes = self._ids(value.get("supported_dtypes"), "supported_dtypes")
        if any(item not in {"float32", "float16", "bfloat16"} for item in dtypes):
            raise ValueError("research_worker_dtype_unknown")
        integers = {}
        for name, maximum in {
            "gpu_count": 1024,
            "vram_bytes_per_gpu": 1 << 50,
            "storage_headroom_bytes": 1 << 60,
        }.items():
            raw = value.get(name)
            if not isinstance(raw, int) or isinstance(raw, bool) or not 0 <= raw <= maximum:
                raise ValueError(f"research_worker_{name}_invalid")
            integers[name] = raw
        expires = value.get("expires_at_epoch")
        if not isinstance(expires, (int, float)) or isinstance(expires, bool):
            raise ValueError("research_worker_inventory_expiry_invalid")
        if not isinstance(value.get("distributed_available"), bool):
            raise ValueError("research_worker_distributed_flag_invalid")
        report = {
            "schema": "ananta.research-training-worker-inventory.v1",
            "worker_id": worker_id,
            "state": state,
            "capabilities": sorted(capabilities),
            "backend_versions": dict(sorted(normalized_versions.items())),
            **integers,
            "compute_capability": require_id(value.get("compute_capability"), "compute_capability"),
            "supported_dtypes": sorted(dtypes),
            "distributed_available": bool(value["distributed_available"]),
            "expires_at_epoch": float(expires),
        }
        report["report_digest"] = canonical_digest(report)
        self._workers[worker_id] = report
        return dict(report)

    def select(
        self,
        *,
        required_capability: str,
        world_size: int,
        precision: str,
        required_storage_bytes: int,
    ) -> dict[str, Any]:
        now = self._clock()
        candidates = [
            report
            for report in self._workers.values()
            if report["state"] == "available"
            and report["expires_at_epoch"] > now
            and required_capability in report["capabilities"]
            and precision in report["supported_dtypes"]
            and report["storage_headroom_bytes"] >= required_storage_bytes
            and (world_size == 1 or report["distributed_available"] is True)
            and (report["gpu_count"] >= world_size or (world_size == 1 and report["gpu_count"] == 0))
        ]
        if not candidates:
            raise LookupError("research_compatible_worker_unavailable")
        selected = min(
            candidates,
            key=lambda item: (
                item["gpu_count"],
                -item["storage_headroom_bytes"],
                item["worker_id"],
            ),
        )
        return dict(selected)

    def revalidate(self, *, worker_id: str, expected_report_digest: str) -> dict[str, Any]:
        report = self._workers.get(require_id(worker_id, "worker_id"))
        if report is None or report["expires_at_epoch"] <= self._clock():
            raise LookupError("research_worker_inventory_expired")
        if report["report_digest"] != expected_report_digest:
            raise ValueError("research_worker_capability_drift")
        return dict(report)

    @staticmethod
    def _ids(value: object, field: str) -> tuple[str, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > 64:
            raise ValueError(f"research_worker_{field}_invalid")
        result = tuple(require_id(item, field) for item in value)
        if len(result) != len(set(result)):
            raise ValueError(f"research_worker_{field}_duplicate")
        return result


__all__ = ["ResearchTrainingWorkerRegistry"]
