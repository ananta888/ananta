import json
from pathlib import Path

from agent.sources.source_snapshot_store import validate_source_snapshot_payload
from tests.open_notebook_test_fakes import build_importer

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "open_notebook"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_new_snapshot_is_indexed_and_schema_valid(tmp_path):
    env = build_importer(tmp_path)
    result = env.importer.import_export(_load("minimal_export.json"))
    snapshots = env.snapshots.list_snapshots(source_id=result["registry_source_id"])
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["status"] == "indexed"
    assert validate_source_snapshot_payload(snapshot) == []

    extensions = snapshot["extensions"]
    assert extensions["source_system"] == "open_notebook"
    assert extensions["open_notebook"]["source_id"] == "src-minimal-text-1"
    assert extensions["imported_at"]
    assert extensions["source_title"] == "Retrieval Augmented Generation Basics"
    assert extensions["notebook_refs"] == ["nb-minimal-1"]
    assert extensions["content_hash"]
    # fetch_source and citation_source are kept separate
    assert extensions["citation_source"]["source_system"] == "OpenNotebook local/export"
    assert extensions["fetch_source"]["mode"] == "export_import"


def test_duplicate_content_is_not_reindexed(tmp_path):
    env = build_importer(tmp_path)
    payload = _load("minimal_export.json")
    first = env.importer.import_export(payload)
    second = env.importer.import_export(payload)

    assert second["snapshot_ids"] == []
    snapshots = env.snapshots.list_snapshots(source_id=first["registry_source_id"])
    indexed = [item for item in snapshots if item["status"] == "indexed"]
    assert len(indexed) == 1


def test_changed_content_creates_new_indexed_snapshot(tmp_path):
    env = build_importer(tmp_path)
    payload = _load("minimal_export.json")
    first = env.importer.import_export(payload)

    payload["sources"][0]["full_text"] = "Completely new content about retrieval budgets and citations."
    second = env.importer.import_export(payload)

    assert second["imported"]["sources"] == 1
    assert first["registry_source_id"] != second["registry_source_id"]
    new_snapshots = env.snapshots.list_snapshots(source_id=second["registry_source_id"])
    assert len(new_snapshots) == 1
    assert new_snapshots[0]["status"] == "indexed"
    assert (
        new_snapshots[0]["extensions"]["content_hash"]
        != env.snapshots.list_snapshots(source_id=first["registry_source_id"])[0]["extensions"]["content_hash"]
    )


def test_snapshot_ids_are_stable_snapshot_store_format(tmp_path):
    env = build_importer(tmp_path)
    result = env.importer.import_export(_load("minimal_export.json"))
    for snapshot_id in result["snapshot_ids"]:
        assert snapshot_id.startswith("snap_")
        assert len(snapshot_id) == len("snap_") + 16
