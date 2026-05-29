from __future__ import annotations

from client_surfaces.operator_tui.hub_loader import _fetch_templates
from client_surfaces.operator_tui.models import PanelState


def test_fetch_templates_uses_local_fallback_when_hub_unavailable(monkeypatch) -> None:
    def _fail(*_args, **_kwargs):
        raise OSError("hub unavailable")

    monkeypatch.setattr("client_surfaces.operator_tui.hub_loader._checked_get", _fail)

    result = _fetch_templates("http://localhost:5000", "token", 1.0)

    assert result.section_id == "templates"
    assert result.state in {PanelState.HEALTHY, PanelState.EMPTY}
    items = list((result.payload or {}).get("items") or [])
    assert items, "expected local template fallback items"
    assert "hub+local" in str(result.message)
