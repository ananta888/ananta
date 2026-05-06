import json

from agent.services.wiki_import_checkpoint_service import WikiImportCheckpointService


def test_checkpoint_service_roundtrip(tmp_path):
    service = WikiImportCheckpointService(root=tmp_path)
    assert service.load(source_id="wiki", corpus_path="dump.xml.bz2", index_path="idx.txt.bz2") is None
    path = service.save(
        source_id="wiki",
        corpus_path="dump.xml.bz2",
        index_path="idx.txt.bz2",
        checkpoint={"items_processed": 5, "records_written": 9, "source_format": "xml"},
    )
    loaded = service.load(source_id="wiki", corpus_path="dump.xml.bz2", index_path="idx.txt.bz2")
    assert loaded is not None
    assert loaded["items_processed"] == 5
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["source_format"] == "xml"
