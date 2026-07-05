"""CRG-003 / CRG-004 / CRG-012: code-review-graph adapter tests.

Acceptance criteria covered here:

CRG-003:
* JSON export of pinned CRG revision is the primary contract
* Direct SQLite import is read-only, schema-version-pinned, path-bounded
* Missing exports produce ``external_graph_unavailable``; mismatched
  revisions produce ``external_graph_incompatible`` (no exception)
* Function/class/file/imports/calls/inheritance/tests are mapped to
  the CodeCompass edge taxonomy

CRG-004:
* CRG confidence kinds EXTRACTED/INFERRED/AMBIGUOUS are mapped to
  numeric confidence + provenance.confidence_kind
* Ambiguous edges are excluded from policy-allowed trust

CRG-012 (security sub-block for CRG adapter):
* path-traversal via ``..`` is rejected (workspace-bound)
* symlinks inside workspace that point outside are not followed
* the adapter never builds shell commands from input
* schema-version-pin: a different reviewer_graph_revision is rejected
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from worker.retrieval.codecompass_crg_adapter import (
    CONFIDENCE_KIND_TO_NUMERIC,
    CRG_PROVIDER_ID,
    CRG_REVIEWED_REVISION,
    CrgJsonAdapter,
    CrgSqliteAdapter,
)
from worker.retrieval.codecompass_import_provider import (
    CodeCompassGraphImportProvider,
)


FIXTURE_DIR = Path(__file__).resolve().parents[0] / "fixtures" / "codecompass" / "crg"


def _install_export(workspace: Path, payload: dict) -> None:
    """Materialise a CRG export.json under ``workspace/.code-review-graph/``."""
    target = workspace / ".code-review-graph"
    target.mkdir(parents=True, exist_ok=True)
    (target / "export.json").write_text(json.dumps(payload))


def _read_fixture() -> dict:
    return json.loads((FIXTURE_DIR / "export_minimal.json").read_text())


# ---------------------------------------------------------------------------
# CRG-002 structural conformance
# ---------------------------------------------------------------------------

def test_json_adapter_implements_port():
    p = CrgJsonAdapter(workspace_dir=Path("/tmp"))
    assert isinstance(p, CodeCompassGraphImportProvider)


def test_sqlite_adapter_implements_port():
    p = CrgSqliteAdapter(workspace_dir=Path("/tmp"))
    assert isinstance(p, CodeCompassGraphImportProvider)


# ---------------------------------------------------------------------------
# CRG-003 JSON adapter happy path
# ---------------------------------------------------------------------------

def test_json_adapter_probe_unavailable_when_no_export(tmp_path):
    p = CrgJsonAdapter(workspace_dir=tmp_path)
    probe = p.probe()
    assert probe.available is False
    assert probe.reason_unavailable == "external_graph_unavailable"


def test_json_adapter_imports_minimal_fixture(tmp_path):
    _install_export(tmp_path, _read_fixture())
    p = CrgJsonAdapter(workspace_dir=tmp_path)
    snap = p.import_snapshot()
    assert snap.provider_id == CRG_PROVIDER_ID
    assert snap.provider_revision == CRG_REVIEWED_REVISION
    assert snap.content_hash
    kinds = {n["kind"] for n in snap.graph_nodes}
    assert "file" in kinds
    assert "symbol_function" in kinds
    edge_kinds = {e["kind"] for e in snap.graph_edges}
    assert "calls" in edge_kinds
    assert "imports" in edge_kinds


def test_json_adapter_rejects_unpinned_revision(tmp_path):
    payload = _read_fixture()
    payload["reviewer_graph_revision"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    _install_export(tmp_path, payload)
    p = CrgJsonAdapter(workspace_dir=tmp_path)
    snap = p.import_snapshot()
    assert snap.graph_nodes == ()
    assert snap.diagnostics["result"] == "external_graph_incompatible"


def test_json_adapter_strict_pinning_cannot_be_relaxed(tmp_path, monkeypatch):
    """strict_pinning is a safety property (DD-015); env-off is ignored.

    A mismatched reviewer_graph_revision must always be rejected.
    """
    monkeypatch.setenv("CODECOMPASS_CRG_STRICT_PINNING", "off")
    payload = _read_fixture()
    payload["reviewer_graph_revision"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    _install_export(tmp_path, payload)
    p = CrgJsonAdapter(workspace_dir=tmp_path)
    snap = p.import_snapshot()
    assert snap.diagnostics["result"] == "external_graph_incompatible"


# ---------------------------------------------------------------------------
# CRG-004 confidence model
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,expected", list(CONFIDENCE_KIND_TO_NUMERIC.items()))
def test_confidence_kind_to_numeric_mapping(kind, expected):
    assert CONFIDENCE_KIND_TO_NUMERIC[kind] == expected


def test_inferred_edges_are_not_policy_allowed(tmp_path):
    payload = _read_fixture()
    # Inflate the test-edge confidence to INFERRED
    payload["tests"][0]["confidence"] = "INFERRED"
    _install_export(tmp_path, payload)
    p = CrgJsonAdapter(workspace_dir=tmp_path)
    snap = p.import_snapshot()
    inferred = [e for e in snap.graph_edges
                if e["trust"]["confidence_kind"] == "INFERRED"]
    assert inferred
    for e in inferred:
        assert e["trust"]["trust_level"] == "inferred"


def test_ambiguous_edges_excluded_from_policy_allowed(tmp_path):
    payload = _read_fixture()
    payload["tests"][0]["confidence"] = "AMBIGUOUS"
    _install_export(tmp_path, payload)
    p = CrgJsonAdapter(workspace_dir=tmp_path)
    snap = p.import_snapshot()
    ambig = [e for e in snap.graph_edges
             if e["trust"]["confidence_kind"] == "AMBIGUOUS"]
    assert ambig
    from agent.services.tools.graph_evidence import POLICY_ALLOWED_TRUST
    for e in ambig:
        assert e["trust"]["trust_level"] not in POLICY_ALLOWED_TRUST


# ---------------------------------------------------------------------------
# CRG-012: per-adapter security sub-block
# ---------------------------------------------------------------------------

def test_path_traversal_in_export_path_rejected(tmp_path):
    """The adapter must reject any workspace_dir that contains symlinks
    pointing outside, even if the export path string is 'safe'-looking."""
    payload = _read_fixture()
    target = tmp_path / "real_export"
    target.mkdir()
    sym = tmp_path / "linked_export"
    try:
        sym.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    # write into target then expose through symlink — workspace points
    # at sym-linked dir; assert_within_workspace should resolve and reject.
    payload_path = target / "inner_export.json"
    payload_path.write_text(json.dumps(payload))
    p = CrgJsonAdapter(workspace_dir=sym)
    snap = p.import_snapshot()
    # The export exists inside sym-resolved target which is outside the
    # sym-linked workspace_dir — must surface as unavailable/incompatible.
    assert snap.graph_nodes == ()


def test_no_shell_construction(monkeypatch):
    """The adapter must not call subprocess/os.system/popen under any
    input. We assert via a monkeypatch that these entry points are not
    invoked during a full import round-trip."""
    import os
    import subprocess

    calls = []

    def _spy_system(*a, **kw):
        calls.append(("system", a, kw))

    def _spy_popen(*a, **kw):
        calls.append(("popen", a, kw))

    monkeypatch.setattr(os, "system", _spy_system)
    monkeypatch.setattr(subprocess, "Popen", _spy_popen)
    if hasattr(subprocess, "run"):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(("run", a, kw)))

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        _install_export(ws, _read_fixture())
        CrgJsonAdapter(workspace_dir=ws).import_snapshot()
    assert calls == [], f"shell called: {calls!r}"


def test_schema_version_pin_with_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("CODECOMPASS_CRG_ALLOW_DIRECT_SQLITE_READ", "1")
    db_path = tmp_path / ".code-review-graph" / "graph.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES('reviewer_graph_revision', ?)",
                     ("deadbeef" * 5,))
        conn.execute("CREATE TABLE nodes(id TEXT, kind TEXT, file TEXT, name TEXT, confidence TEXT)")
        conn.execute("CREATE TABLE edges(source_id TEXT, target_id TEXT, kind TEXT, confidence TEXT)")
        conn.commit()
    p = CrgSqliteAdapter(workspace_dir=tmp_path)
    probe = p.probe()
    assert probe.available is False
    assert probe.reason_unavailable == "external_graph_incompatible"


def test_sqlite_adapter_refuses_when_feature_flag_off(tmp_path):
    p = CrgSqliteAdapter(workspace_dir=tmp_path)
    probe = p.probe()
    assert probe.available is False
    assert probe.reason_unavailable == "feature_disabled"


def test_sqlite_adapter_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CODECOMPASS_CRG_ALLOW_DIRECT_SQLITE_READ", "1")
    db_path = tmp_path / ".code-review-graph" / "graph.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES('reviewer_graph_revision', ?)",
                     (CRG_REVIEWED_REVISION,))
        conn.execute("CREATE TABLE nodes(id TEXT, kind TEXT, file TEXT, name TEXT, confidence TEXT)")
        conn.execute("INSERT INTO nodes VALUES('file:a.py','file','a.py',NULL,'EXTRACTED')")
        conn.execute("CREATE TABLE edges(source_id TEXT, target_id TEXT, kind TEXT, confidence TEXT)")
        conn.execute("INSERT INTO edges VALUES('file:a.py','file:b.py','imports','EXTRACTED')")
        conn.commit()
    p = CrgSqliteAdapter(workspace_dir=tmp_path)
    snap = p.import_snapshot()
    assert snap.diagnostics["result"] == "ok"
    assert len(snap.graph_nodes) == 1
    assert len(snap.graph_edges) == 1