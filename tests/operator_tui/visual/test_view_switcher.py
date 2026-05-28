from __future__ import annotations

from client_surfaces.operator_tui.visual.runtime.view_switcher import ViewSwitcher


def test_initial_active_view_empty():
    s = ViewSwitcher()
    assert s.active_view_id() == ""


def test_set_views_selects_first_when_no_active():
    s = ViewSwitcher()
    s.set_views(["logo_animation", "renderer_diagnostics"])
    assert s.active_view_id() == "logo_animation"


def test_switch_to_available_view():
    s = ViewSwitcher()
    s.set_views(["logo_animation", "renderer_diagnostics"])
    assert s.switch_to("renderer_diagnostics") is True
    assert s.active_view_id() == "renderer_diagnostics"


def test_switch_to_unavailable_view_fails():
    s = ViewSwitcher()
    s.set_views(["logo_animation"], unavailable=["opengl_scene"])
    assert s.switch_to("opengl_scene") is False
    assert s.active_view_id() == "logo_animation"


def test_switch_to_unavailable_force_succeeds():
    s = ViewSwitcher()
    s.set_views(["logo_animation"], unavailable=["opengl_scene"])
    assert s.switch_to("opengl_scene", force=True) is True
    assert s.active_view_id() == "opengl_scene"


def test_next_view_cycles():
    s = ViewSwitcher()
    s.set_views(["a", "b", "c"])
    assert s.next_view() == "b"
    assert s.next_view() == "c"
    assert s.next_view() == "a"


def test_previous_view_cycles():
    s = ViewSwitcher()
    s.set_views(["a", "b", "c"])
    assert s.previous_view() == "c"
    assert s.previous_view() == "b"


def test_next_skips_unavailable():
    s = ViewSwitcher()
    s.set_views(["a", "b"])  # only available views
    s.next_view()
    assert s.active_view_id() in ("a", "b")


def test_toggle_overlay():
    s = ViewSwitcher()
    assert s.is_overlay_visible() is False
    assert s.toggle_overlay() is True
    assert s.is_overlay_visible() is True
    assert s.toggle_overlay() is False


def test_set_overlay_visible():
    s = ViewSwitcher()
    s.set_overlay_visible(True)
    assert s.is_overlay_visible() is True
    s.set_overlay_visible(False)
    assert s.is_overlay_visible() is False


def test_active_view_marker_updates_after_switch():
    s = ViewSwitcher()
    s.set_views(["logo_animation", "renderer_diagnostics"])
    s.switch_to("renderer_diagnostics")
    snap = s.state_snapshot()
    assert snap["active_view_id"] == "renderer_diagnostics"


def test_state_snapshot_contains_all_keys():
    s = ViewSwitcher()
    s.set_views(["a", "b"], unavailable=["c"])
    snap = s.state_snapshot()
    assert "active_view_id" in snap
    assert "available_views" in snap
    assert "unavailable_views" in snap
    assert "overlay_visible" in snap
