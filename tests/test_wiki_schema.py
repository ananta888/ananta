import json
from pathlib import Path


def test_wiki_codecompass_schema_contains_required_contract():
    schema_path = Path("schemas/worker/wiki_codecompass_record.v1.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$id"].endswith("wiki_codecompass_record.v1.json")
    required = set(schema["required"])
    assert {"id", "kind", "content", "source", "article_title", "chunk_index"} <= required

