from __future__ import annotations

from client_surfaces.freecad.workbench.context import capture_bounded_document_context
from client_surfaces.freecad.workbench.context_preview import build_context_preview


def test_capture_bounded_document_context_handles_empty_document() -> None:
    payload = capture_bounded_document_context(None, None)
    assert payload["document"]["name"] == "Untitled"
    assert payload["objects"] == []
    assert payload["provenance"]["source"] == "freecad_workbench"


def test_capture_bounded_document_context_redacts_path_and_bounds_objects() -> None:
    objects = [{"name": f"Obj-{index}", "type": "Part", "visibility": True, "volume": index} for index in range(300)]
    payload = capture_bounded_document_context(
        {"name": "Assembly", "path": "/secret/path.FCStd"},
        objects,
        selection=["Obj-1"],
        max_objects=256,
        max_payload_bytes=4000,
    )
    preview = build_context_preview(payload)

    assert payload["document"]["path"] == "redacted"
    assert len(payload["objects"]) <= 256
    assert payload["provenance"]["redaction"] is True
    assert preview["redaction"] is True


def test_context_preview_marks_oversize_payloads() -> None:
    objects = [{"name": "VeryLong" * 50, "type": "Part", "visibility": True, "volume": 1.0} for _ in range(40)]
    payload = capture_bounded_document_context({"name": "Heavy"}, objects, max_objects=40, max_payload_bytes=65536)
    preview = build_context_preview(payload, max_preview_bytes=512)
    assert preview["oversize"] is True
