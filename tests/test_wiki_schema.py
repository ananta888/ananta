import json
from pathlib import Path


def test_wiki_codecompass_schema_contains_required_contract():
    schema_path = Path("schemas/worker/wiki_codecompass_record.v1.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$id"].endswith("wiki_codecompass_record.v1.json")
    required = set(schema["required"])
    assert {"schema", "kind", "record_id", "source", "article_title"} <= required
    assert "content" in schema["properties"]
