from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ViewCapabilityReport:
    view_id: str
    available: bool
    degraded: bool = False
    unavailable_reason: str = ""
    degraded_features: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def status_label(self) -> str:
        if not self.available:
            return f"unavailable: {self.unavailable_reason or 'unknown reason'}"
        if self.degraded:
            features = ", ".join(self.degraded_features) or "unknown"
            return f"degraded: {features}"
        return "ok"


def build_markdown_mermaid_capability_report(
    *,
    view_id: str = "markdown_mermaid_document",
    mermaid_status: dict[str, dict[str, Any]] | None = None,
) -> ViewCapabilityReport:
    """Build a ViewCapabilityReport for the markdown_mermaid_document view."""
    status = mermaid_status or {}
    image_backends = {k: v for k, v in status.items() if k != "fallback_codeblock"}
    mermaid_image_ok = any(v.get("available") for v in image_backends.values())

    if mermaid_image_ok:
        return ViewCapabilityReport(
            view_id=view_id,
            available=True,
            degraded=False,
        )

    degraded_reasons: list[str] = []
    for name, info in image_backends.items():
        if not info.get("available"):
            reason = info.get("reason") or "unavailable"
            degraded_reasons.append(f"Mermaid image: {reason}")

    return ViewCapabilityReport(
        view_id=view_id,
        available=True,
        degraded=bool(degraded_reasons),
        degraded_features=tuple(degraded_reasons),
        extra={"mermaid_status": status},
    )
