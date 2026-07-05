import json
from pathlib import Path

from agent.sources.open_notebook_import_policy import OpenNotebookImportPolicy
from tests.open_notebook_test_fakes import build_importer

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "open_notebook"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_minimal_import_creates_artifact_collection_descriptor_and_snapshot(tmp_path):
    env = build_importer(tmp_path)
    result = env.importer.import_export(_load("minimal_export.json"), created_by="tester")

    assert result["status"] == "completed"
    assert result["reason_code"] == "ok"
    assert result["imported"]["sources"] == 1
    assert result["imported"]["notes"] == 1
    assert result["imported"]["collections"] == 1
    assert result["skipped"]["chat_sessions"] == 0

    upload = env.ingestion.uploads[0]
    assert upload["collection_name"] == "Ananta Research"
    assert upload["media_type"] == "text/markdown"

    source_artifact = env.artifact_repo.saved[result["artifact_ids"][0]]
    metadata = source_artifact.artifact_metadata
    assert metadata["ingestion_mode"] == "open_notebook_import"
    assert metadata["source_system"] == "open_notebook"
    assert metadata["open_notebook"]["source_id"] == "src-minimal-text-1"
    assert metadata["open_notebook"]["import_key"] == result["import_key"]

    descriptor = env.registry.get_source(result["registry_source_id"])
    assert descriptor is not None
    assert descriptor["source_type"] == "open_notebook"
    assert descriptor["extensions"]["import_key"] == result["import_key"]

    snapshots = env.snapshots.list_snapshots(source_id=result["registry_source_id"])
    assert len(snapshots) == 1
    assert snapshots[0]["status"] == "indexed"
    assert snapshots[0]["extensions"]["source_system"] == "open_notebook"
    assert snapshots[0]["extensions"]["citation_source"]["source_system"] == "OpenNotebook local/export"


def test_import_writes_knowledge_index_records(tmp_path):
    env = build_importer(tmp_path)
    result = env.importer.import_export(_load("minimal_export.json"))

    assert result["knowledge_index_id"]
    assert len(env.index_repo.saved) == 1
    knowledge_index = env.index_repo.saved[0]
    assert knowledge_index.source_scope == "open_notebook"
    assert knowledge_index.status == "completed"

    index_file = Path(knowledge_index.output_dir) / "index.jsonl"
    records = [json.loads(line) for line in index_file.read_text(encoding="utf-8").splitlines() if line]
    kinds = {record["kind"] for record in records}
    assert "open_notebook_source_chunk" in kinds
    assert "open_notebook_note_chunk" in kinds
    source_record = next(r for r in records if r["kind"] == "open_notebook_source_chunk")
    meta = source_record["import_metadata"]
    assert meta["source_type"] == "open_notebook"
    assert meta["record_kind"] == "primary_source"
    assert meta["snapshot_id"].startswith("snap_")
    assert meta["registry_source_id"] == result["registry_source_id"]


def test_repeated_import_is_idempotent(tmp_path):
    env = build_importer(tmp_path)
    payload = _load("minimal_export.json")
    first = env.importer.import_export(payload)
    second = env.importer.import_export(payload)

    assert first["registry_source_id"] == second["registry_source_id"]
    assert second["imported"]["sources"] == 0
    assert second["imported"]["notes"] == 0
    assert second["skipped"]["sources"] == 1
    assert second["skipped"]["notes"] == 1
    assert any(issue["reason_code"] == "duplicate_content_hash" for issue in second["issues"])
    # no duplicate descriptors, snapshots stay single-indexed
    assert len(env.registry.list_sources()) == 1
    snapshots = env.snapshots.list_snapshots(source_id=first["registry_source_id"])
    assert len([s for s in snapshots if s["status"] == "indexed"]) == 1
    # collections resolve by name: only one collection object created
    assert len(env.ingestion.collections) == 1
    assert len(env.ingestion.uploads) == 2
    assert len(env.index_repo.saved) == 1


def test_distinct_sources_with_same_content_are_not_deduplicated(tmp_path):
    env = build_importer(tmp_path)
    payload = _load("minimal_export.json")
    second_source = dict(payload["sources"][0])
    second_source["id"] = "src-same-content-other-id"
    second_source["title"] = "Independent source with shared text"
    payload["sources"].append(second_source)

    result = env.importer.import_export(payload)

    assert result["imported"]["sources"] == 2
    assert result["skipped"]["sources"] == 0
    assert len(result["snapshot_ids"]) == 2


def test_complex_import_links_shared_source_to_both_collections(tmp_path):
    env = build_importer(tmp_path)
    result = env.importer.import_export(_load("complex_export.json"))

    assert result["imported"]["sources"] == 3
    assert result["imported"]["notes"] == 2
    assert result["imported"]["insights"] == 2
    assert result["skipped"]["chat_sessions"] == 1
    assert any(issue["reason_code"] == "chat_sessions_import_disabled" for issue in result["issues"])
    assert result["imported"]["collections"] == 2

    # shared source got an extra collection link beyond its primary upload collection
    assert len(env.link_repo.links) >= 1
    link = env.link_repo.links[0]
    assert link.link_metadata["source"] == "open_notebook_import"


def test_chat_sessions_never_produce_artifacts_or_records(tmp_path):
    env = build_importer(tmp_path)
    env.importer.import_export(_load("complex_export.json"))
    # "scoped context envelopes" only appears in the fixture's chat session
    chat_only_phrase = b"scoped context envelopes"
    for upload in env.ingestion.uploads:
        assert chat_only_phrase not in upload["content"]
    index_file = Path(env.index_repo.saved[0].output_dir) / "index.jsonl"
    assert "scoped context envelopes" not in index_file.read_text(encoding="utf-8")


def test_invalid_export_returns_failed_result(tmp_path):
    env = build_importer(tmp_path)
    result = env.importer.import_export(_load("invalid_export_missing_source_id.json"))
    assert result["status"] == "failed"
    assert result["reason_code"] == "invalid_open_notebook_export"
    assert env.ingestion.uploads == []


def test_source_with_secret_value_is_skipped(tmp_path):
    env = build_importer(tmp_path)
    payload = _load("minimal_export.json")
    payload["sources"][0]["full_text"] = "leaked key sk-abcdef1234567890 inside"
    result = env.importer.import_export(payload)
    assert result["imported"]["sources"] == 0
    assert result["skipped"]["sources"] == 1
    assert any(issue["reason_code"] == "secret_like_value_blocked" for issue in result["issues"])


def test_notes_can_be_disabled_per_call(tmp_path):
    env = build_importer(tmp_path)
    result = env.importer.import_export(_load("minimal_export.json"), include_notes=False)
    assert result["imported"]["notes"] == 0
    assert result["skipped"]["notes"] == 1
    assert any(issue["reason_code"] == "notes_import_disabled" for issue in result["issues"])


def test_policy_disabled_sources_are_skipped(tmp_path):
    env = build_importer(tmp_path, policy=OpenNotebookImportPolicy(allow_sources=False))
    result = env.importer.import_export(_load("minimal_export.json"))
    assert result["imported"]["sources"] == 0
    assert result["skipped"]["sources"] == 1
    assert any(issue["reason_code"] == "sources_import_disabled" for issue in result["issues"])
