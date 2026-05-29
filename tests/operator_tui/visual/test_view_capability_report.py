from __future__ import annotations

from client_surfaces.operator_tui.visual.runtime.view_capability_report import (
    ViewCapabilityBundle,
    ViewCapabilityReport,
    build_full_capability_report,
    build_markdown_mermaid_capability_report,
)


def test_report_ok_status():
    r = ViewCapabilityReport(view_id="logo_animation", available=True)
    assert r.status_label() == "ok"


def test_report_unavailable_status():
    r = ViewCapabilityReport(view_id="opengl_view", available=False, unavailable_reason="no OpenGL/EGL")
    assert "unavailable" in r.status_label()
    assert "OpenGL" in r.status_label()


def test_report_degraded_status():
    r = ViewCapabilityReport(
        view_id="markdown_mermaid_document", available=True, degraded=True,
        degraded_features=("Mermaid image: mmdc missing",)
    )
    assert "degraded" in r.status_label()


def test_full_report_all_ansi_views_available():
    views = ["logo_animation", "renderer_diagnostics", "snake_debug_view"]
    bundle = build_full_capability_report(views, terminal_capabilities={"ansi": True})
    assert len(bundle.available) == 3
    assert bundle.unavailable == []


def test_full_report_raster_view_unavailable_without_capability():
    reqs = {
        "opengl_scene": {
            "display_name": "OpenGL Scene",
            "required_render_features": ["opengl_offscreen"],
        }
    }
    bundle = build_full_capability_report(
        ["logo_animation", "opengl_scene"],
        view_requirements=reqs,
        terminal_capabilities={"ansi": True, "opengl_offscreen": False},
    )
    avail_ids = [r.view_id for r in bundle.available]
    unavail_ids = [r.view_id for r in bundle.unavailable]
    assert "logo_animation" in avail_ids
    assert "opengl_scene" in unavail_ids


def test_full_report_active_view_id():
    bundle = build_full_capability_report(
        ["logo_animation", "renderer_diagnostics"],
        active_view_id="renderer_diagnostics",
    )
    assert bundle.active_view_id == "renderer_diagnostics"


def test_full_report_find_by_id():
    bundle = build_full_capability_report(["logo_animation"])
    r = bundle.find("logo_animation")
    assert r is not None
    assert r.view_id == "logo_animation"


def test_full_report_find_missing_returns_none():
    bundle = build_full_capability_report(["logo_animation"])
    assert bundle.find("does_not_exist") is None


def test_markdown_mermaid_available_when_no_image_renderer():
    report = build_markdown_mermaid_capability_report(
        mermaid_status={
            "mermaid_cli": {"available": False, "reason": "mmdc not found"},
            "playwright": {"available": False, "reason": "not installed"},
            "fallback_codeblock": {"available": True, "reason": ""},
        }
    )
    assert report.available is True
    assert report.degraded is True
    # degraded_features now uses "mermaid_renderer:" prefix
    assert any("mermaid_renderer" in f or "Mermaid" in f for f in report.degraded_features)


def test_markdown_mermaid_ok_when_image_renderer_available():
    # When image renderer is available but no terminal image protocol, it's still degraded
    # (image-rendered-but-adapter-unavailable) unless kitty/sixel caps are passed.
    report = build_markdown_mermaid_capability_report(
        mermaid_status={"mermaid_cli": {"available": True, "reason": ""}},
        image_output_caps={"raster_renderer_available": True, "kitty_supported": True, "sixel_supported": False},
    )
    assert report.available is True
    assert report.degraded is False
    assert report.extra.get("mermaid_renderer") is True
    assert report.extra.get("kitty_supported") is True


def test_unavailable_skipped_by_bundle_cycling():
    bundle = build_full_capability_report(
        ["logo_animation", "opengl_scene"],
        view_requirements={"opengl_scene": {"required_render_features": ["opengl_offscreen"]}},
        terminal_capabilities={"ansi": True, "opengl_offscreen": False},
    )
    avail = [r.view_id for r in bundle.available]
    assert "opengl_scene" not in avail
