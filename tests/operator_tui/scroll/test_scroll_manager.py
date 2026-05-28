from __future__ import annotations

from client_surfaces.operator_tui.scroll.scroll_context import ScrollContext
from client_surfaces.operator_tui.scroll.scroll_manager import ScrollManager


def _ctx(id: str, content: int = 100, viewport: int = 20) -> ScrollContext:
    return ScrollContext(id=id, label=id, content_height=content, viewport_height=viewport)


def test_register_and_get():
    sm = ScrollManager()
    ctx = _ctx("chat_panel")
    sm.register(ctx)
    assert sm.get("chat_panel") is ctx


def test_register_replaces_existing():
    sm = ScrollManager()
    sm.register(_ctx("a"))
    ctx2 = _ctx("a", content=200)
    sm.register(ctx2)
    assert sm.get("a") is ctx2


def test_remove():
    sm = ScrollManager()
    sm.register(_ctx("a"))
    sm.remove("a")
    assert sm.get("a") is None


def test_remove_nonexistent_noop():
    sm = ScrollManager()
    sm.remove("does_not_exist")


def test_update():
    sm = ScrollManager()
    sm.register(_ctx("a"))
    sm.update("a", offset=10)
    assert sm.get("a").offset == 10


def test_update_missing_returns_false():
    sm = ScrollManager()
    assert sm.update("ghost", offset=5) is False


def test_focusable_contexts():
    sm = ScrollManager()
    sm.register(_ctx("a"))
    sm.register(ScrollContext(id="b", label="b", content_height=5, viewport_height=20, focusable=False))
    fc = sm.focusable_contexts()
    assert len(fc) == 1
    assert fc[0].id == "a"


def test_visible_contexts():
    sm = ScrollManager()
    sm.register(_ctx("a"))
    sm.register(ScrollContext(id="b", label="b", content_height=100, viewport_height=20, visible=False))
    vc = sm.visible_contexts()
    ids = [c.id for c in vc]
    assert "a" in ids
    assert "b" not in ids


def test_remove_stale():
    sm = ScrollManager()
    sm.register(_ctx("a"))
    sm.register(_ctx("b"))
    removed = sm.remove_stale({"a"})
    assert "b" in removed
    assert sm.get("b") is None
    assert sm.get("a") is not None


def test_context_lifecycle():
    sm = ScrollManager()
    sm.register(_ctx("x"))
    sm.update("x", content_height=50)
    assert sm.get("x").content_height == 50
    sm.remove("x")
    assert sm.get("x") is None


def test_diagnostics():
    sm = ScrollManager()
    sm.register(_ctx("a"))
    d = sm.diagnostics()
    assert d["total"] == 1
    assert "a" in d["contexts"]


def test_all_ids_order():
    sm = ScrollManager()
    for id in ["c", "a", "b"]:
        sm.register(_ctx(id))
    ids = sm.all_ids()
    assert ids == ["c", "a", "b"]
