from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ViewCapabilityReport:
    view_id: str
    available: bool
    display_name: str = ""
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

    def short_reason(self) -> str:
        if not self.available:
            r = self.unavailable_reason or "unavailable"
            return r[:40]
        if self.degraded and self.degraded_features:
            return self.degraded_features[0][:40]
        return ""


@dataclass
class ViewCapabilityBundle:
    available: list[ViewCapabilityReport] = field(default_factory=list)
    unavailable: list[ViewCapabilityReport] = field(default_factory=list)
    active_view_id: str = ""

    def all_reports(self) -> list[ViewCapabilityReport]:
        return self.available + self.unavailable

    def find(self, view_id: str) -> ViewCapabilityReport | None:
        for r in self.all_reports():
            if r.view_id == view_id:
                return r
        return None


def build_full_capability_report(
    view_ids: list[str],
    *,
    view_requirements: dict[str, dict[str, Any]] | None = None,
    terminal_capabilities: dict[str, bool] | None = None,
    active_view_id: str = "",
) -> ViewCapabilityBundle:
    caps = terminal_capabilities or {"ansi": True}
    reqs = view_requirements or {}
    available: list[ViewCapabilityReport] = []
    unavailable: list[ViewCapabilityReport] = []

    for vid in view_ids:
        req = reqs.get(vid, {})
        required_features = req.get("required_render_features") or []
        display_name = req.get("display_name") or vid
        missing: list[str] = [f for f in required_features if not caps.get(f, True)]
        if missing:
            unavailable.append(ViewCapabilityReport(
                view_id=vid,
                display_name=display_name,
                available=False,
                unavailable_reason=f"missing: {', '.join(missing)}",
            ))
        else:
            degraded_feats = [f for f in (req.get("optional_runtime_requirements") or []) if not caps.get(f, True)]
            available.append(ViewCapabilityReport(
                view_id=vid,
                display_name=display_name,
                available=True,
                degraded=bool(degraded_feats),
                degraded_features=tuple(degraded_feats),
            ))

    return ViewCapabilityBundle(available=available, unavailable=unavailable, active_view_id=active_view_id)


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
