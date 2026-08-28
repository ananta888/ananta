"""Deterministic inference resource admission for local expert residency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class KnowledgeExpertResourcePlan:
    admitted: bool
    reason_code: str
    maximum_active_experts: int
    vram_required_bytes: int
    vram_usable_bytes: int
    ram_required_bytes: int
    disk_required_bytes: int
    fallback_mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.knowledge-expert-resource-plan.v1",
            "admitted": self.admitted,
            "reason_code": self.reason_code,
            "maximum_active_experts": self.maximum_active_experts,
            "vram_required_bytes": self.vram_required_bytes,
            "vram_usable_bytes": self.vram_usable_bytes,
            "ram_required_bytes": self.ram_required_bytes,
            "disk_required_bytes": self.disk_required_bytes,
            "fallback_mode": self.fallback_mode,
        }


class KnowledgeExpertResourcePlanner:
    def plan(
        self,
        *,
        base_model_vram_bytes: int,
        kv_cache_vram_bytes: int,
        adapter_sizes: tuple[int, ...],
        vram_capacity_bytes: int,
        vram_reserve_bytes: int,
        ram_capacity_bytes: int,
        disk_capacity_bytes: int,
        warm_cache_experts: int,
    ) -> KnowledgeExpertResourcePlan:
        values = (
            base_model_vram_bytes,
            kv_cache_vram_bytes,
            vram_capacity_bytes,
            vram_reserve_bytes,
            ram_capacity_bytes,
            disk_capacity_bytes,
            warm_cache_experts,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in values
        ) or any(
            isinstance(size, bool) or not isinstance(size, int) or size < 1
            for size in adapter_sizes
        ):
            raise ValueError("knowledge_expert_resource_input_invalid")
        ordered = tuple(sorted((int(size) for size in adapter_sizes)))
        usable = max(0, int(vram_capacity_bytes) - int(vram_reserve_bytes))
        fixed = int(base_model_vram_bytes) + int(kv_cache_vram_bytes)
        available_for_experts = max(0, usable - fixed)
        active = 0
        active_bytes = 0
        for size in ordered:
            if active_bytes + size > available_for_experts:
                break
            active += 1
            active_bytes += size
        ram_required = sum(ordered[: max(active, min(len(ordered), int(warm_cache_experts)))])
        disk_required = sum(ordered)
        admitted = (
            bool(ordered) and active > 0 and ram_required <= ram_capacity_bytes and disk_required <= disk_capacity_bytes
        )
        if not ordered:
            reason = "expert_bank_empty"
        elif fixed > usable:
            reason = "base_and_kv_vram_exceeded"
        elif active < 1:
            reason = "expert_vram_exceeded"
        elif ram_required > ram_capacity_bytes:
            reason = "expert_ram_exceeded"
        elif disk_required > disk_capacity_bytes:
            reason = "expert_disk_exceeded"
        else:
            reason = "knowledge_expert_resources_admitted"
        return KnowledgeExpertResourcePlan(
            admitted=admitted,
            reason_code=reason,
            maximum_active_experts=active,
            vram_required_bytes=fixed + active_bytes,
            vram_usable_bytes=usable,
            ram_required_bytes=ram_required,
            disk_required_bytes=disk_required,
            fallback_mode="expert" if admitted else "rag_only",
        )


__all__ = ["KnowledgeExpertResourcePlan", "KnowledgeExpertResourcePlanner"]
