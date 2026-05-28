from __future__ import annotations

import pytest

from client_surfaces.operator_tui.visual.runtime.registry import OutputAdapterRegistry, RendererRegistry, ViewRegistry


def test_registries_resolve_registered_names() -> None:
    views = ViewRegistry()
    renderers = RendererRegistry()
    adapters = OutputAdapterRegistry()
    views.register_factory("diagnostics", lambda: {"view": "ok"})
    renderers.register_factory("ansi_blocks", lambda: {"renderer": "ok"})
    adapters.register_factory("ansi", lambda: {"adapter": "ok"})
    assert views.create("diagnostics")["view"] == "ok"
    assert renderers.create("ansi_blocks")["renderer"] == "ok"
    assert adapters.create("ansi")["adapter"] == "ok"


def test_registry_rejects_duplicate_registration() -> None:
    views = ViewRegistry()
    views.register_factory("logo", lambda: object())
    with pytest.raises(ValueError):
        views.register_factory("logo", lambda: object())


def test_registry_unknown_name_lists_options() -> None:
    renderers = RendererRegistry()
    renderers.register_factory("ansi_blocks", lambda: object())
    with pytest.raises(KeyError) as exc:
        renderers.create("cpu_raster")
    assert "ansi_blocks" in str(exc.value)

