from __future__ import annotations

from types import SimpleNamespace

from client_surfaces.freecad.workbench.commands import (
    capture_active_context_command,
    preview_active_export_plan,
    preview_active_macro_plan,
    submit_active_document_goal,
)
from client_surfaces.freecad.workbench.settings import FreecadWorkbenchSettings
from client_surfaces.freecad.workbench.client import FreecadHubClient


class FakeSelection:
    def __init__(self, items):
        self._items = items

    def getSelection(self):
        return list(self._items)


def _runtime_modules():
    body = SimpleNamespace(Label="Body", Name="Body", TypeId="Part::Feature", ViewObject=SimpleNamespace(Visibility=True), Shape=SimpleNamespace(Volume=5.0), Constraints=[])
    document = SimpleNamespace(Name="Asm", FileName="/tmp/model.FCStd", UnitSystem="mm", Objects=[body])
    app_module = SimpleNamespace(ActiveDocument=document)
    gui_module = SimpleNamespace(Selection=FakeSelection([body]))
    return app_module, gui_module


def test_command_hooks_use_active_context_capture() -> None:
    app_module, gui_module = _runtime_modules()
    client = FreecadHubClient(FreecadWorkbenchSettings(endpoint="https://hub.local"))

    captured = capture_active_context_command(app_module=app_module, gui_module=gui_module)
    submitted = submit_active_document_goal(client, goal="Inspect body", app_module=app_module, gui_module=gui_module)
    export_preview = preview_active_export_plan(client, fmt="step", target_path="/tmp/out.step", app_module=app_module, gui_module=gui_module)
    macro_preview = preview_active_macro_plan(client, objective="lighten body", app_module=app_module, gui_module=gui_module)

    assert captured["status"] == "accepted"
    assert captured["context"]["document"]["name"] == "Asm"
    assert submitted["status"] == "accepted"
    assert export_preview["response"]["plan"]["selection_only"] is True
    assert macro_preview["response"]["plan"]["mode"] == "dry_run"
