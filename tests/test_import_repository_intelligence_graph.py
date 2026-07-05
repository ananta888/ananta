"""RIG-012: import CLI / script tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from worker.retrieval import codecompass_rig_importer as cli


FIXTURE_PATH = Path(__file__).resolve().parents[0] / "fixtures" / "codecompass" / "rig" / "cmake" / "snapshot_minimal.json"


@pytest.fixture
def fixture_workspace(tmp_path):
    """Materialise the fixture into a real workspace so DD-013 path-checks pass."""
    ws = tmp_path
    src = json.loads(FIXTURE_PATH.read_text())
    src["repository"]["workspace_dir"] = str(ws)
    for edge in src["edges"]:
        edge["evidence"]["source_file"] = str(ws / "CMakeLists.txt")
        if isinstance(edge.get("trust"), dict):
            tev = edge["trust"].get("evidence") or {}
            if tev.get("source_file"):
                edge["trust"]["evidence"]["source_file"] = str(ws / "CMakeLists.txt")
    (ws / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n")
    snap = ws / "snapshot.json"
    snap.write_text(json.dumps(src))
    return ws, snap


def test_import_default_is_validate_only(fixture_workspace):
    ws, snap = fixture_workspace
    res = cli.import_snapshot_file(snap, workspace_dir=ws,
                                   write_index=False, index_path=None)
    assert res.ok, res.failures
    assert res.snapshot_id == "snap_fixture0001"
    # 1 pm + 1 external_package + 1 bc + 1 runner + 1 test = 5 nodes
    assert len(res.rig_nodes) == 5
    assert len(res.rig_edges) == 2


def test_import_persists_index_when_write_index(fixture_workspace, tmp_path):
    ws, snap = fixture_workspace
    index = tmp_path / "rig_index.json"
    res = cli.import_snapshot_file(snap, workspace_dir=ws,
                                   write_index=True, index_path=index)
    assert res.ok, res.failures
    assert index.exists()
    payload = json.loads(index.read_text())
    assert payload["diagnostics"]["repository_intelligence"]["snapshot_id"] == "snap_fixture0001"
    assert len(payload["rig_nodes"]) == 5
    assert len(payload["rig_edges"]) == 2


def test_import_requires_index_path_when_writing(fixture_workspace):
    ws, snap = fixture_workspace
    res = cli.import_snapshot_file(snap, workspace_dir=ws,
                                   write_index=True, index_path=None)
    assert not res.ok
    assert any(f["reason"] == "missing_index_path" for f in res.failures)


def test_import_rejects_unsupported_schema_version(tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"schema_version": "v999"}))
    res = cli.import_snapshot_file(snap, workspace_dir=tmp_path,
                                   write_index=False, index_path=None)
    assert not res.ok
    assert any(f["reason"] == "schema_version_unsupported" for f in res.failures)


def test_import_rejects_invalid_json(tmp_path):
    snap = tmp_path / "broken.json"
    snap.write_text("{not valid json")
    res = cli.import_snapshot_file(snap, workspace_dir=tmp_path,
                                   write_index=False, index_path=None)
    assert not res.ok
    assert any(f["reason"] == "invalid_json" for f in res.failures)


def test_import_rejects_path_outside_workspace(tmp_path):
    """DD-013: source_file pointing outside workspace fails fast."""
    src = json.loads(FIXTURE_PATH.read_text())
    src["repository"]["workspace_dir"] = str(tmp_path)
    for edge in src["edges"]:
        edge["evidence"]["source_file"] = "/etc/passwd"
        if isinstance(edge.get("trust"), dict):
            edge["trust"]["evidence"]["source_file"] = "/etc/passwd"
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(src))
    res = cli.import_snapshot_file(snap, workspace_dir=tmp_path,
                                   write_index=False, index_path=None)
    assert not res.ok
    assert any(f["reason"] == "path_outside_workspace" for f in res.failures)


def test_import_rejects_missing_source_ids(tmp_path):
    """AGENTS.md: source IDs are never synthesized."""
    src = json.loads(FIXTURE_PATH.read_text())
    src["repository"]["workspace_dir"] = str(tmp_path)
    src["edges"][0]["evidence"] = {
        "source_file": str(tmp_path / "CMakeLists.txt"),
        "source_kind": "manual_fixture",
        # no source_record_id, no source_run_id, no reason
    }
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(src))
    res = cli.import_snapshot_file(snap, workspace_dir=tmp_path,
                                   write_index=False, index_path=None)
    assert not res.ok
    synthetic = [f for f in res.failures if "synth" in (f.get("detail") or "").lower()]
    assert synthetic == []


def test_cli_entrypoint_returns_0_on_ok(fixture_workspace, capsys, monkeypatch):
    ws, snap = fixture_workspace
    monkeypatch.setattr(sys, "argv", ["import_repository_intelligence_graph",
                                       str(snap), "--workspace-dir", str(ws)])
    code = cli.main()
    assert code == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out


def test_cli_entrypoint_returns_1_on_validation_failure(tmp_path, capsys, monkeypatch):
    snap = tmp_path / "bad.json"
    snap.write_text(json.dumps({"schema_version": "v999"}))
    monkeypatch.setattr(sys, "argv", ["import_repository_intelligence_graph", str(snap)])
    code = cli.main()
    assert code == 1
    out = capsys.readouterr().out
    assert '"ok": false' in out


def test_cli_entrypoint_returns_2_on_missing_file(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["import_repository_intelligence_graph",
                                       "/nope/does/not/exist.json"])
    code = cli.main()
    assert code == 2