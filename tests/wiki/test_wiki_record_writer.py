import json

from agent.services.wiki_record_writer import write_wiki_jsonl_cache


def test_write_wiki_jsonl_cache_writes_all_records(tmp_path):
    out = write_wiki_jsonl_cache(
        records=[
            {"id": "ok", "kind": "wiki_section_chunk", "content": "x", "source": "s"},
            {"id": "ok2", "kind": "wiki_section_chunk", "content": "y", "source": "s"},
        ],
        cache_path=tmp_path / "out.jsonl",
    )
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == "ok"
