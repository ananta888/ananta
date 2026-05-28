from __future__ import annotations

from dataclasses import dataclass, field

from client_surfaces.operator_tui.visual.runtime.view_capability_report import ViewCapabilityReport


@dataclass
class ViewSwitcherOverlay:
    _reports: list[ViewCapabilityReport] = field(default_factory=list)
    _active_view_id: str = ""

    def update_reports(self, reports: list[ViewCapabilityReport]) -> None:
        self._reports = list(reports)

    def set_active_view(self, view_id: str) -> None:
        self._active_view_id = view_id

    def render_two_line(self, *, width: int = 80) -> tuple[str, str]:
        """Return the two overlay lines: (views_ok_line, status_detail_line)."""
        ok_views = [r for r in self._reports if r.available and not r.degraded]
        degraded_views = [r for r in self._reports if r.available and r.degraded]
        unavailable_views = [r for r in self._reports if not r.available]

        def _marker(vid: str) -> str:
            return "*" if vid == self._active_view_id else " "

        ok_parts = [f"[{_marker(r.view_id)}{r.view_id}]" for r in ok_views]
        line1 = "Views OK: " + "  ".join(ok_parts) if ok_parts else "Views OK: (none)"

        detail_parts: list[str] = []
        for r in degraded_views:
            features = ",".join(r.degraded_features) if r.degraded_features else "degraded"
            detail_parts.append(f"[~{r.view_id}: {features}]")
        for r in unavailable_views:
            reason = r.unavailable_reason or "unavailable"
            detail_parts.append(f"[!{r.view_id}: {reason}]")
        line2 = "  ".join(detail_parts)

        return line1[:width], line2[:width]

    def all_reports(self) -> list[ViewCapabilityReport]:
        return list(self._reports)
