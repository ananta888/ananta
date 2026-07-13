"""Neutral, fail-closed contract for workflow runtime fallbacks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

FallbackSemanticClass = Literal["equivalent", "degraded", "incompatible"]

PROTECTED_RUNTIME_CAPABILITIES = frozenset(
    {
        "authorization",
        "policy",
        "durability",
        "resume",
        "side_effect_guard",
        "audit",
    }
)


@dataclass(frozen=True)
class RuntimeFallbackRequest:
    source_runtime: str
    target_runtime: str
    reason_code: str
    semantic_class: FallbackSemanticClass
    source_capabilities: frozenset[str]
    target_capabilities: frozenset[str]
    explicitly_enabled: bool = False

    @classmethod
    def create(
        cls,
        *,
        source_runtime: str,
        target_runtime: str,
        reason_code: str,
        semantic_class: FallbackSemanticClass,
        source_capabilities: Iterable[str],
        target_capabilities: Iterable[str],
        explicitly_enabled: bool = False,
    ) -> "RuntimeFallbackRequest":
        return cls(
            source_runtime=str(source_runtime or "").strip(),
            target_runtime=str(target_runtime or "").strip(),
            reason_code=str(reason_code or "").strip(),
            semantic_class=semantic_class,
            source_capabilities=frozenset(
                str(value).strip() for value in source_capabilities if str(value).strip()
            ),
            target_capabilities=frozenset(
                str(value).strip() for value in target_capabilities if str(value).strip()
            ),
            explicitly_enabled=bool(explicitly_enabled),
        )


@dataclass(frozen=True)
class RuntimeFallbackDecision:
    allowed: bool
    reason_code: str
    request: RuntimeFallbackRequest
    lost_capabilities: tuple[str, ...]
    gained_capabilities: tuple[str, ...]
    protected_capability_loss: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "source_runtime": self.request.source_runtime,
            "target_runtime": self.request.target_runtime,
            "fallback_reason_code": self.request.reason_code,
            "semantic_class": self.request.semantic_class,
            "explicitly_enabled": self.request.explicitly_enabled,
            "capability_difference": {
                "lost": list(self.lost_capabilities),
                "gained": list(self.gained_capabilities),
                "protected_loss": list(self.protected_capability_loss),
            },
        }


class WorkflowRuntimeFallbackPolicy:
    """Evaluate a fully described runtime transition without side effects."""

    def evaluate(self, request: RuntimeFallbackRequest) -> RuntimeFallbackDecision:
        lost = tuple(sorted(request.source_capabilities - request.target_capabilities))
        gained = tuple(sorted(request.target_capabilities - request.source_capabilities))
        protected_loss = tuple(sorted(set(lost) & PROTECTED_RUNTIME_CAPABILITIES))

        if not request.source_runtime or not request.target_runtime or not request.reason_code:
            reason_code = "runtime_fallback_description_invalid"
        elif not request.explicitly_enabled:
            reason_code = "runtime_fallback_not_explicitly_enabled"
        elif protected_loss:
            reason_code = "runtime_fallback_protected_capability_loss"
        elif request.semantic_class != "equivalent":
            reason_code = "runtime_fallback_semantics_not_equivalent"
        elif lost:
            reason_code = "runtime_fallback_capability_loss"
        else:
            reason_code = "runtime_fallback_allowed"

        return RuntimeFallbackDecision(
            allowed=reason_code == "runtime_fallback_allowed",
            reason_code=reason_code,
            request=request,
            lost_capabilities=lost,
            gained_capabilities=gained,
            protected_capability_loss=protected_loss,
        )


workflow_runtime_fallback_policy = WorkflowRuntimeFallbackPolicy()

