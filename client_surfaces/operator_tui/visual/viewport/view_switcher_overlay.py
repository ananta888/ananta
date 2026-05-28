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
        """Return (views_ok_line, views_unavailable_line), each truncated to width."""
        ok_views = [r for r in self._reports if r.available and not r.degraded]
        degraded_views = [r for r in self._reports if r.available and r.degraded]
        unavailable_views = [r for r in self._reports if not r.available]

        def _marker(vid: str) -> str:
            return "*" if vid == self._active_view_id else " "

        line1 = _build_line(
            label="Views OK:",
            items=[f"[{_marker(r.view_id)}{r.view_id}]" for r in ok_views],
            width=width,
        )
        unavail_items = [
            f"[~{r.view_id}: {r.short_reason()}]" for r in degraded_views
        ] + [
            f"[!{r.view_id}: {r.short_reason()}]" for r in unavailable_views
        ]
        if unavail_items:
            line2 = _build_line(label="Views unavailable:", items=unavail_items, width=width)
        else:
            line2 = ""

        return line1, line2

    def all_reports(self) -> list[ViewCapabilityReport]:
        return list(self._reports)


def _build_line(label: str, items: list[str], width: int) -> str:
    prefix = label + " "
    available_width = max(0, width - len(prefix))
    if not items:
        return (prefix + "(none)")[:width]

    parts: list[str] = []
    used = 0
    remaining = len(items)
    for item in items:
        remaining -= 1
        needed = len(item) + (1 if parts else 0)
        reserve = 0
        if remaining > 0:
            reserve = len(f"  +{remaining} more")
        if parts and used + needed + reserve > available_width:
            parts.append(f"+{remaining + 1} more")
            break
        if parts:
            used += 1
        parts.append(item)
        used += len(item)

    return (prefix + "  ".join(parts))[:width]
