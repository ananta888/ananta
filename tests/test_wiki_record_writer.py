import json

from agent.services.wiki_record_writer import WikiRecordWriter


def test_wiki_record_writer_writes_valid_records(tmp_path):
    writer = WikiRecordWriter(tmp_path / "out.jsonl")
    writer.write_records(
        [
            {"id": "ok", "kind": "wiki_section_chunk", "content": "x", "source": "s"},
            {"id": "bad", "kind": "wiki_section_chunk"},
        ]
    )
    writer.close()
    lines = (tmp_path / "out.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == "ok"

