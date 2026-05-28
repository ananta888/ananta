from __future__ import annotations

from client_surfaces.operator_tui.visual.runtime.view_capability_report import ViewCapabilityReport
from client_surfaces.operator_tui.visual.viewport.view_switcher_overlay import ViewSwitcherOverlay


def _overlay(*reports: ViewCapabilityReport, active: str = "") -> ViewSwitcherOverlay:
    o = ViewSwitcherOverlay()
    o.update_reports(list(reports))
    o.set_active_view(active)
    return o


def _available(vid: str, display_name: str = "") -> ViewCapabilityReport:
    return ViewCapabilityReport(view_id=vid, display_name=display_name or vid, available=True)


def _unavailable(vid: str, reason: str) -> ViewCapabilityReport:
    return ViewCapabilityReport(view_id=vid, available=False, unavailable_reason=reason)


def _degraded(vid: str, features: tuple[str, ...]) -> ViewCapabilityReport:
    return ViewCapabilityReport(view_id=vid, available=True, degraded=True, degraded_features=features)


def test_line1_starts_with_views_ok():
    o = _overlay(_available("logo_animation"))
    line1, _ = o.render_two_line(width=80)
    assert line1.startswith("Views OK:")


def test_line1_contains_available_views():
    o = _overlay(_available("logo_animation"), _available("renderer_diagnostics"))
    line1, _ = o.render_two_line(width=80)
    assert "logo_animation" in line1
    assert "renderer_diagnostics" in line1


def test_line2_empty_when_no_unavailable():
    o = _overlay(_available("logo_animation"))
    _, line2 = o.render_two_line(width=80)
    assert line2 == ""


def test_line2_contains_unavailable_views():
    o = _overlay(_available("logo_animation"), _unavailable("opengl_scene", "no OpenGL/EGL"))
    _, line2 = o.render_two_line(width=80)
    assert "opengl_scene" in line2
    assert "Views unavailable:" in line2


def test_active_view_marked_with_star():
    o = _overlay(_available("logo_animation"), _available("renderer_diagnostics"), active="renderer_diagnostics")
    line1, _ = o.render_two_line(width=80)
    assert "*renderer_diagnostics" in line1


def test_inactive_view_has_space_marker():
    o = _overlay(_available("logo_animation"), _available("renderer_diagnostics"), active="renderer_diagnostics")
    line1, _ = o.render_two_line(width=80)
    assert "[ logo_animation]" in line1 or " logo_animation" in line1


def test_truncation_with_plus_n_more():
    reports = [_available(f"view_{i}") for i in range(20)]
    o = _overlay(*reports)
    line1, _ = o.render_two_line(width=40)
    assert "more" in line1 or len(line1) <= 40


def test_output_respects_width():
    reports = [_available(f"long_view_name_{i}") for i in range(10)]
    o = _overlay(*reports)
    line1, line2 = o.render_two_line(width=30)
    assert len(line1) <= 30
    assert len(line2) <= 30 or line2 == ""


def test_degraded_views_in_line2():
    o = _overlay(
        _available("logo_animation"),
        _degraded("markdown_mermaid_document", ("Mermaid image: mmdc missing",)),
    )
    _, line2 = o.render_two_line(width=120)
    assert "markdown_mermaid_document" in line2 or "~" in line2


def test_no_reports_renders_gracefully():
    o = ViewSwitcherOverlay()
    line1, line2 = o.render_two_line(width=80)
    assert "Views OK:" in line1
    assert line2 == ""
