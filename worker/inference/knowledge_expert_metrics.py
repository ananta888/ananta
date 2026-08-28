"""Low-cardinality Worker metrics for expert inference costs."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class KnowledgeExpertMetrics:
    router_decisions: dict[tuple[str, str], int] = field(default_factory=dict)
    latency_ms: dict[str, list[int]] = field(default_factory=dict)
    adapter_bytes_loaded: int = 0
    cost_micros_by_mode: dict[str, int] = field(default_factory=dict)

    def decision(self, *, mode: str, reason_code: str) -> None:
        allowed_modes = {"base_only", "rag_only", "expert_only", "expert_plus_rag", "off", "auto"}
        allowed_reasons = {
            "expert_selected",
            "expert_unavailable",
            "expert_disabled",
            "runtime_denied",
            "resource_denied",
            "policy_denied",
        }
        key = (mode if mode in allowed_modes else "unknown", reason_code if reason_code in allowed_reasons else "other")
        self.router_decisions[key] = self.router_decisions.get(key, 0) + 1

    def latency(self, *, tier: str, milliseconds: int) -> None:
        if tier not in {"cold", "warm", "hot", "retrieval", "inference"} or milliseconds < 0:
            raise ValueError("knowledge_expert_metric_invalid")
        self.latency_ms.setdefault(tier, []).append(min(milliseconds, 86_400_000))

    def loaded(self, size_bytes: int) -> None:
        if size_bytes < 0:
            raise ValueError("knowledge_expert_metric_invalid")
        self.adapter_bytes_loaded += size_bytes

    def cost(self, *, mode: str, micros: int) -> None:
        if mode not in {"rag_only", "expert_only", "expert_plus_rag", "base_only"} or micros < 0:
            raise ValueError("knowledge_expert_metric_invalid")
        self.cost_micros_by_mode[mode] = self.cost_micros_by_mode.get(mode, 0) + micros


__all__ = ["KnowledgeExpertMetrics"]
