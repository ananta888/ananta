import json

from agent.services.wiki_import_checkpoint_service import WikiImportCheckpointService


def test_checkpoint_service_roundtrip(tmp_path):
    service = WikiImportCheckpointService(tmp_path / "checkpoint.json")
    assert service.load() is None
    service.save(items_processed=5, records_written=9, source_format="xml")
    loaded = service.load()
    assert loaded is not None
    assert loaded["items_processed"] == 5
    raw = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert raw["source_format"] == "xml"

