from agent.sources.open_notebook_source_reference import (
    build_source_reference,
    build_source_references_for_chunks,
    validate_source_reference_payload,
)


def _base_metadata(**overrides):
    metadata = {
        "source_type": "open_notebook",
        "source_system": "open_notebook",
        "registry_source_id": "open-notebook-abc123def456",
        "open_notebook_source_id": "src-1",
        "snapshot_id": "snap_1234567890abcdef",
        "chunk_id": "onb:abc123",
        "artifact_id": "art-1",
        "record_kind": "primary_source",
        "source_title": "Survey of Agents",
        "canonical_url": "https://example.org/survey",
        "content_hash": "hash-1",
        "notebook_ids": ["nb-1"],
        "imported_at": "2026-07-05T10:00:00Z",
    }
    metadata.update(overrides)
    return metadata


def test_url_source_reference_is_schema_valid():
    reference = build_source_reference(_base_metadata())
    assert validate_source_reference_payload(reference) == []
    assert reference["source_id"] == "open-notebook-abc123def456"
    assert reference["snapshot_id"] == "snap_1234567890abcdef"
    assert reference["canonical_url"] == "https://example.org/survey"
    assert reference["extensions"]["record_kind"] == "primary_source"
    assert reference["extensions"]["synthetic_snapshot"] is False
    assert "Survey of Agents" in reference["extensions"]["citation_label"]


def test_file_source_reference_uses_file_url():
    reference = build_source_reference(
        _base_metadata(canonical_url=None, file_path="documents/queue-notes.pdf")
    )
    assert validate_source_reference_payload(reference) == []
    assert reference["canonical_url"] == "file:///documents/queue-notes.pdf"
    assert reference["extensions"]["file_path"] == "documents/queue-notes.pdf"


def test_text_source_reference_falls_back_to_ananta_uri():
    reference = build_source_reference(_base_metadata(canonical_url=None, file_path=None))
    assert validate_source_reference_payload(reference) == []
    assert reference["canonical_url"].startswith("ananta://open-notebook/")


def test_note_reference_uses_parent_snapshot_and_label():
    reference = build_source_reference(
        _base_metadata(
            record_kind="note",
            note_type="human",
            snapshot_id=None,
            parent_source_snapshot_id="snap_feedfacefeedface",
            canonical_url=None,
        )
    )
    assert validate_source_reference_payload(reference) == []
    assert reference["snapshot_id"] == "snap_feedfacefeedface"
    assert reference["extensions"]["synthetic_snapshot"] is False
    assert reference["extensions"]["record_kind"] == "note"
    assert reference["extensions"]["note_type"] == "human"
    assert reference["extensions"]["citation_label"].startswith("[note]")


def test_insight_reference_keeps_parent_and_transformation():
    reference = build_source_reference(
        _base_metadata(
            record_kind="source_insight",
            snapshot_id=None,
            parent_source_snapshot_id="snap_feedfacefeedface",
            parent_source_id="src-1",
            transformation_name="Key Terms",
            insight_type="key_terms",
        )
    )
    assert validate_source_reference_payload(reference) == []
    # derived insights reference the parent source snapshot
    assert reference["snapshot_id"] == "snap_feedfacefeedface"
    assert reference["extensions"]["parent_source_id"] == "src-1"
    assert reference["extensions"]["transformation_name"] == "Key Terms"
    assert reference["extensions"]["citation_label"].startswith("[derived insight]")


def test_reference_rejects_missing_grounding_ids():
    import pytest

    with pytest.raises(ValueError, match="snapshot_id_missing"):
        build_source_reference(_base_metadata(record_kind="note", snapshot_id=None))
    with pytest.raises(ValueError, match="source_id_missing"):
        build_source_reference(_base_metadata(registry_source_id=None, source_id=None))
    with pytest.raises(ValueError, match="chunk_id_missing"):
        build_source_reference(_base_metadata(chunk_id=None))


def test_build_references_for_chunks_dedupes_and_filters():
    chunk = {"metadata": _base_metadata()}
    repo_chunk = {"metadata": {"source_type": "repo", "chunk_id": "repo:1"}}
    references = build_source_references_for_chunks([chunk, dict(chunk), repo_chunk])
    assert len(references) == 1
    assert references[0]["extensions"]["source_system"] == "open_notebook"
