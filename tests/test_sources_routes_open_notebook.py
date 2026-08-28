from __future__ import annotations

import io
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures" / "open_notebook"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _import(client, headers, payload):
    return client.post("/sources/import/open-notebook", headers=headers, json=payload)


def test_import_open_notebook_json_body(client, admin_auth_header, monkeypatch, tmp_path):
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    res = _import(client, admin_auth_header, _fixture("minimal_export.json"))
    assert res.status_code == 200
    data = res.json["data"]
    assert data["status"] in {"completed", "completed_with_issues"}
    assert data["imported"]["sources"] == 1
    assert data["registry_source_id"].startswith("open-notebook-")
    assert data["import_key"]
    assert data["snapshot_ids"]


def test_import_open_notebook_multipart_file(client, admin_auth_header, monkeypatch, tmp_path):
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    payload = json.dumps(_fixture("minimal_export.json")).encode("utf-8")
    res = client.post(
        "/sources/import/open-notebook",
        headers=admin_auth_header,
        data={"file": (io.BytesIO(payload), "export.json")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert res.json["data"]["imported"]["sources"] == 1


def test_imported_source_appears_in_listing_snapshots_and_citation(client, admin_auth_header, monkeypatch, tmp_path):
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    sync = client.post("/sources/actions/sync-builtins", headers=admin_auth_header, json={})
    assert sync.status_code == 200
    imported = _import(client, admin_auth_header, _fixture("minimal_export.json")).json["data"]
    registry_source_id = imported["registry_source_id"]

    listing = client.get("/sources", headers=admin_auth_header)
    assert listing.status_code == 200
    rows = {item["source_id"]: item for item in listing.json["data"]}
    assert registry_source_id in rows
    assert rows[registry_source_id]["source_type"] == "open_notebook"
    # builtin descriptor is synced too
    assert "open-notebook-local-export" in rows

    snapshots = client.get(f"/sources/{registry_source_id}/snapshots", headers=admin_auth_header)
    assert snapshots.status_code == 200
    snapshot_rows = snapshots.json["data"]
    assert len(snapshot_rows) == 1
    assert snapshot_rows[0]["status"] == "indexed"

    citation = client.get(f"/sources/{registry_source_id}/citation", headers=admin_auth_header)
    assert citation.status_code == 200
    assert "OpenNotebook" in citation.json["data"]["long"]


def test_repeated_import_is_idempotent_via_api(client, admin_auth_header, monkeypatch, tmp_path):
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    payload = _fixture("minimal_export.json")
    first = _import(client, admin_auth_header, payload).json["data"]
    second = _import(client, admin_auth_header, payload).json["data"]
    assert second["registry_source_id"] == first["registry_source_id"]
    assert second["imported"]["sources"] == 0
    assert second["skipped"]["sources"] == 1

    listing = client.get("/sources", headers=admin_auth_header)
    ids = [item["source_id"] for item in listing.json["data"]]
    assert ids.count(first["registry_source_id"]) == 1


def test_invalid_export_returns_validated_error(client, admin_auth_header, monkeypatch, tmp_path):
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    res = _import(client, admin_auth_header, _fixture("invalid_export_missing_source_id.json"))
    assert res.status_code == 400
    assert res.json["data"]["reason_code"] == "invalid_open_notebook_export"


def test_invalid_payload_type_is_rejected(client, admin_auth_header, monkeypatch, tmp_path):
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    res = client.post("/sources/import/open-notebook", headers=admin_auth_header, json=[1, 2, 3])
    assert res.status_code == 400


def test_broken_multipart_file_is_rejected(client, admin_auth_header, monkeypatch, tmp_path):
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    res = client.post(
        "/sources/import/open-notebook",
        headers=admin_auth_header,
        data={"file": (io.BytesIO(b"not json"), "export.json")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
