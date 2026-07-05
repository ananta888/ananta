import json
from pathlib import Path

import pytest

from agent.sources.open_notebook_mapper import (
    OpenNotebookMapper,
    build_import_key,
    source_content_hash,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "open_notebook"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_map_minimal_export_produces_collection_and_artifact_plans():
    plan = OpenNotebookMapper().map_export(_load("minimal_export.json"))
    assert plan["source_system"] == "open_notebook"
    assert len(plan["collections"]) == 1
    assert plan["collections"][0]["external_id"] == "nb-minimal-1"
    assert len(plan["artifacts"]) == 1
    artifact = plan["artifacts"][0]
    assert artifact["external_id"] == "src-minimal-text-1"
    assert artifact["metadata"]["open_notebook"]["source_id"] == "src-minimal-text-1"
    assert artifact["metadata"]["open_notebook"]["notebook_ids"] == ["nb-minimal-1"]
    assert artifact["metadata"]["source_system"] == "open_notebook"
    assert artifact["has_inline_text"] is True
    assert artifact["content_hash"]


def test_map_complex_export_covers_url_file_and_text_sources():
    plan = OpenNotebookMapper().map_export(_load("complex_export.json"))
    by_id = {item["external_id"]: item for item in plan["artifacts"]}

    url_source = by_id["src-url-agent-survey"]
    assert url_source["url"] == "https://example.org/papers/autonomous-llm-agents-survey"
    assert url_source["collection_names"] == ["Autonomous Agents Research", "Infrastructure Reading"]

    file_source = by_id["src-file-queue-notes"]
    assert file_source["file_path"] == "documents/queue-design-notes.pdf"

    text_source = by_id["src-text-context-budget"]
    assert text_source["url"] is None
    assert text_source["file_path"] is None
    assert text_source["has_inline_text"] is True

    assert len(plan["notes"]) == 2
    assert len(plan["source_insights"]) == 2
    assert len(plan["chat_sessions"]) == 1


def test_url_only_source_gets_reference_stub_content():
    payload = _load("minimal_export.json")
    payload["sources"][0].pop("full_text")
    payload["sources"][0]["asset"] = {"url": "https://example.org/doc"}
    plan = OpenNotebookMapper().map_export(payload)
    artifact = plan["artifacts"][0]
    assert artifact["has_inline_text"] is False
    assert "https://example.org/doc" in artifact["content"]
    assert artifact["filename"].endswith(".reference.md")


def test_invalid_export_raises_validated_error():
    with pytest.raises(ValueError, match="invalid_open_notebook_export"):
        OpenNotebookMapper().map_export(_load("invalid_export_missing_source_id.json"))


def test_empty_source_raises_validated_error():
    payload = _load("minimal_export.json")
    payload["sources"][0]["full_text"] = ""
    payload["sources"][0]["asset"] = {}
    with pytest.raises(ValueError, match="invalid_open_notebook_export"):
        OpenNotebookMapper().map_export(payload)


def test_duplicate_source_id_raises():
    payload = _load("minimal_export.json")
    payload["sources"].append(dict(payload["sources"][0]))
    with pytest.raises(ValueError, match="duplicate_source_id"):
        OpenNotebookMapper().map_export(payload)


def test_import_key_is_deterministic_and_content_stable():
    payload = _load("minimal_export.json")
    key_one = build_import_key(payload)
    key_two = build_import_key(json.loads(json.dumps(payload)))
    assert key_one == key_two
    payload["sources"][0]["full_text"] = "changed content"
    assert build_import_key(payload) == key_one
    payload["sources"][0]["id"] = "different-source"
    assert build_import_key(payload) != key_one


def test_content_hash_normalizes_whitespace():
    source_a = {"full_text": "hello   world\n", "asset": {}}
    source_b = {"full_text": "hello world", "asset": {}}
    assert source_content_hash(source_a) == source_content_hash(source_b)


def test_content_hash_falls_back_to_asset_reference():
    source = {"full_text": "", "asset": {"url": "https://example.org/x"}}
    assert source_content_hash(source) == source_content_hash(dict(source))
    other = {"full_text": "", "asset": {"url": "https://example.org/y"}}
    assert source_content_hash(source) != source_content_hash(other)
