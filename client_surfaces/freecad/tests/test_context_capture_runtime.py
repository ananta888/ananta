from __future__ import annotations

from types import SimpleNamespace

from client_surfaces.freecad.workbench.context import capture_active_freecad_context, capture_context_from_freecad_document


class FakeSelection:
    def __init__(self, items):
        self._items = items

    def getSelection(self):
        return list(self._items)


def _fake_document():
    constraint = SimpleNamespace(Name="SketchConstraint", Type="Distance", Status="ok")
    shape = SimpleNamespace(Volume=42.5)
    visible_view = SimpleNamespace(Visibility=True)
    hidden_view = SimpleNamespace(Visibility=False)
    obj_a = SimpleNamespace(Label="Body", Name="Body001", TypeId="Part::Feature", ViewObject=visible_view, Shape=shape, Constraints=[constraint])
    obj_b = SimpleNamespace(Label="Sketch", Name="Sketch001", TypeId="Sketcher::SketchObject", ViewObject=hidden_view, Shape=SimpleNamespace(Volume=0.0), Constraints=[])
    return SimpleNamespace(Name="Assembly", FileName="/tmp/secret.FCStd", UnitSystem="mm", Objects=[obj_a, obj_b])


def test_capture_context_from_freecad_document_uses_object_attributes() -> None:
    document = _fake_document()
    payload = capture_context_from_freecad_document(document, selection_objects=[document.Objects[0]])

    assert payload["document"]["name"] == "Assembly"
    assert payload["document"]["path"] == "redacted"
    assert payload["objects"][0]["name"] == "Body"
    assert payload["objects"][0]["type"] == "Part::Feature"
    assert payload["selection"] == ["Body"]
    assert payload["constraints"][0]["name"] == "SketchConstraint"


def test_capture_active_freecad_context_reads_fake_runtime_modules() -> None:
    document = _fake_document()
    app_module = SimpleNamespace(ActiveDocument=document)
    gui_module = SimpleNamespace(Selection=FakeSelection([document.Objects[1]]))

    payload = capture_active_freecad_context(app_module=app_module, gui_module=gui_module)

    assert payload["document"]["name"] == "Assembly"
    assert len(payload["objects"]) == 2
    assert payload["selection"] == ["Sketch"]
