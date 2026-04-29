from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

_ALLOWED_RISK_CLASSES = {"low", "medium", "high", "critical"}


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_id: str
    provider_family: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    risk_class: str = "medium"
    enabled_by_default: bool = False
    display_name: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        provider_id = str(self.provider_id or "").strip()
        provider_family = str(self.provider_family or "").strip()
        risk_class = str(self.risk_class or "").strip().lower() or "medium"
        if not provider_id:
            raise ValueError("provider_id_required")
        if not provider_family:
            raise ValueError("provider_family_required")
        if risk_class not in _ALLOWED_RISK_CLASSES:
            raise ValueError(f"invalid_risk_class:{risk_class}")
        normalized_capabilities = tuple(
            str(item).strip().lower()
            for item in self.capabilities
            if str(item).strip()
        )
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "provider_family", provider_family)
        object.__setattr__(self, "risk_class", risk_class)
        object.__setattr__(self, "capabilities", normalized_capabilities)


@dataclass(frozen=True)
class ProviderHealthReport:
    status: str
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().lower()
        if status not in {"healthy", "degraded", "disabled", "unknown"}:
            raise ValueError(f"invalid_health_status:{status or '<missing>'}")
        object.__setattr__(self, "status", status)


@dataclass(frozen=True)
class ProviderStatusSnapshot:
    descriptor: ProviderDescriptor
    health: ProviderHealthReport
    available: bool


class ProviderRuntime(Protocol):
    descriptor: ProviderDescriptor

    def health(self) -> ProviderHealthReport: ...
