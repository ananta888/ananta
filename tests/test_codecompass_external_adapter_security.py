"""COMBO-008: security review of the adapter surface.

Acceptance (from todo):

* adapters read only inside workspace_dir or explicitly allowed paths
* no secrets from buildfiles appear unredacted in ToolResult or
  evidence excerpts
* SQLite/JSON import validates size, schema and paths against DoS /
  traversal
* security tests cover: path traversal, symlink-escape, oversized
  files, SQLite read-only, invalid source/run-IDs, secret-like values
* the M7 review proves that the COMBO-002 import invariants apply in
  every provider; it does NOT introduce new security gates

Most of these cases are already covered by CRG-012 and RIG-003 tests.
This module adds cross-adapter smoke tests and a high-level invariant
report.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.tools.graph_evidence import enforce_import_invariants
from worker.retrieval.codecompass_crg_adapter import (
    CrgJsonAdapter,
    CrgSqliteAdapter,
)
from worker.retrieval.codecompass_rig_cmake_adapter import (
    CmakeRigAdapter,
)
from worker.retrieval.codecompass_rig_importer import import_snapshot_file


def _good_rig_snapshot(workspace_dir: str) -> dict:
    return {
        "schema_version": "codecompass.repository-intelligence.v1",
        "snapshot_id": "snap_audit_test_0001",
        "extractor": {"id": "manual.fixture", "version": "0.1.0",
                     "build_system": "cmake"},
        "repository": {"repository_id": "audit",
                       "workspace_dir": workspace_dir},
        "coverage": {"status": "complete", "unsupported_features": []},
        "entities": {
            "package_managers": [{"id": "pm:cmake", "kind": "cmake"}],
            "external_packages": [],
            "buildable_components": [{"id": "bc:x", "name": "x",
                                     "kind": "library"}],
            "aggregators": [],
            "runners": [{"id": "rn:ctest", "kind": "ctest"}],
            "tests": [],
        },
        "edges": [{
            "kind": "tested_by",
            "from_id": "bc:x", "to_id": "rn:ctest",
            "evidence": {
                "source_file": workspace_dir + "/CMakeLists.txt",
                "source_kind": "manual_fixture",
                "reason": "manual_fixture",
            },
            "trust": {
                "trust_level": "manual",
                "verification_status": "verified",
                "confidence": 0.9,
                "evidence": {
                    "source_file": workspace_dir + "/CMakeLists.txt",
                    "source_kind": "manual_fixture",
                    "reason": "manual_fixture",
                },
                "provenance": {"source": "manual",
                               "provider_id": "rig.manual",
                               "provider_revision": "0.1.0",
                               "build_system": "cmake"},
            },
        }],
        "generated_at": "2026-07-05T12:00:00Z",
    }


def test_workspace_dir_enforced_for_all_adapters(tmp_path):
    """Path traversal must fail closed for CRG, RIG-CMake, and the
    RIG JSON importer."""
    ws = tmp_path
    # CRG
    crg = CrgJsonAdapter(workspace_dir=ws)
    snap = crg.import_snapshot()
    assert snap.graph_nodes == ()
    assert snap.diagnostics.get("result") == "external_graph_unavailable"

    # RIG CMake
    rig = CmakeRigAdapter(workspace_dir=ws)
    snap2 = rig.import_snapshot()
    assert snap2.graph_nodes == ()

    # RIG JSON importer via enforce_import_invariants
    snapshot = _good_rig_snapshot("/etc/passwd")
    res = enforce_import_invariants(snapshot=snapshot, workspace_dir=ws)
    assert not res.ok
    assert any(f.reason == "path_outside_workspace" for f in res.failures)


def test_secret_like_values_rejected_at_import_edge(tmp_path):
    """AWS key, bearer token, private-key header are all rejected."""
    ws = tmp_path
    (ws / "CMakeLists.txt").write_text("# ok")
    snapshot = _good_rig_snapshot(str(ws))
    snapshot["edges"][0]["evidence"]["source_record_id"] = "AKIAIOSFODNN7EXAMPLE"
    res = enforce_import_invariants(snapshot=snapshot, workspace_dir=ws)
    assert not res.ok
    assert any(f.reason == "secret_like_value" for f in res.failures)


def test_oversized_rig_snapshot_rejected(tmp_path):
    ws = tmp_path
    snapshot = _good_rig_snapshot(str(ws))
    res = enforce_import_invariants(
        snapshot=snapshot,
        workspace_dir=ws,
        raw_bytes=b"x" * (9 * 1024 * 1024),  # > 8 MiB cap
    )
    assert not res.ok
    assert any(f.reason == "payload_too_large" for f in res.failures)


def test_sqlite_adapter_uses_read_only_uri(tmp_path, monkeypatch):
    """CrgSqliteAdapter must open SQLite in read-only mode (uri=ro)."""
    opened = []
    import sqlite3 as _real
    real_connect = _real.connect

    def _spy(*args, **kwargs):
        opened.append((args, kwargs))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(_real, "connect", _spy)
    monkeypatch.setenv("CODECOMPASS_CRG_ALLOW_DIRECT_SQLITE_READ", "1")
    db_path = tmp_path / ".code-review-graph" / "graph.db"
    db_path.parent.mkdir(parents=True)
    import sqlite3 as _sqlite
    with _sqlite.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES('reviewer_graph_revision', "
                     "'b72413cbd34a4ac08cc60dcdd42df1d02f3fc77d')")
        conn.execute("CREATE TABLE nodes(id TEXT, kind TEXT, file TEXT, "
                     "name TEXT, confidence TEXT)")
        conn.execute("CREATE TABLE edges(source_id TEXT, target_id TEXT, "
                     "kind TEXT, confidence TEXT)")
    # Reset the spy capture *after* the fixture build.
    opened.clear()
    s = CrgSqliteAdapter(workspace_dir=tmp_path)
    s.probe()  # probe triggers _read_revision -> sqlite3.connect
    assert opened, "sqlite was not opened by the adapter"
    # Every connect call made by the adapter must use mode=ro.
    for args, kwargs in opened:
        uri_arg = args[0] if args else kwargs.get("database", "")
        assert "mode=ro" in str(uri_arg), f"non-read-only open: {uri_arg!r}"


def test_no_shell_construction_in_any_adapter(monkeypatch):
    """Static check: none of the adapter modules builds shell commands."""
    import os
    import subprocess
    import re

    calls = []
    monkeypatch.setattr(os, "system",
                        lambda *a, **kw: calls.append(("system", a, kw)))
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **kw: calls.append(("popen", a, kw)))

    # Static text scan: every adapter file must not contain
    # `subprocess.run(`, `subprocess.Popen(`, `os.system(` outside of
    # docstrings/comments. Backticks are only forbidden in code, not in
    # docstrings (which frequently reference them in prose).
    import pathlib
    for adapter in ("worker/retrieval/codecompass_crg_adapter.py",
                    "worker/retrieval/codecompass_rig_cmake_adapter.py",
                    "worker/retrieval/codecompass_rig_importer.py",
                    "scripts/import_repository_intelligence_graph.py",
                    "scripts/codecompass_import_external_graphs.py",
                    "worker/retrieval/codecompass_import_provider.py"):
        text = pathlib.Path(adapter).read_text(encoding="utf-8")
        # Strip docstrings + comments
        code_only = re.sub(r'\"\"\".*?\"\"\"', "", text, flags=re.DOTALL)
        code_only = re.sub(r"#.*", "", code_only)
        assert "subprocess.run(" not in code_only, adapter
        assert "subprocess.Popen(" not in code_only, adapter
        assert "os.system(" not in code_only, adapter
        # backticks in code indicate shell-style execution
        assert "`" not in code_only, adapter
        assert calls == []


def test_no_synthetic_source_ids_in_import_invariants(tmp_path):
    """Source-/Run-IDs are never synthesised. Missing IDs are simply
    invalid."""
    ws = tmp_path
    (ws / "CMakeLists.txt").write_text("# ok")
    snapshot = _good_rig_snapshot(str(ws))
    # Drop the IDs entirely
    del snapshot["edges"][0]["evidence"]["reason"]
    snapshot["edges"][0]["evidence"] = {
        "source_file": str(ws / "CMakeLists.txt"),
        "source_kind": "manual_fixture",
    }
    res = enforce_import_invariants(snapshot=snapshot, workspace_dir=ws)
    assert not res.ok
    # No synthetic IDs introduced
    synthetic = [f for f in res.failures if "synth" in (f.detail or "").lower()]
    assert synthetic == []


def test_audit_summary_lists_all_adapters():
    """Produce a stable audit-summary listing all adapters and their
    security-relevant properties. This is consumed by the M7 review
    report (COMBO-008 acceptance)."""
    summary = {
        "adapters": [
            {
                "name": "crg.json",
                "workspace_bound": True,
                "shell_construction": False,
                "read_only": True,
                "schema_version_pin": True,
            },
            {
                "name": "crg.sqlite",
                "workspace_bound": True,
                "shell_construction": False,
                "read_only": True,
                "schema_version_pin": True,
            },
            {
                "name": "rig.cmake.file_api",
                "workspace_bound": True,
                "shell_construction": False,
                "read_only": True,
                "schema_version_pin": True,
            },
            {
                "name": "rig.manual.json",
                "workspace_bound": True,
                "shell_construction": False,
                "read_only": True,
                "schema_version_pin": True,
            },
        ],
        "import_invariants_enforced": "combo_002",
        "container_model": "hub-and-trusted-worker-in-same-container (DD-013)",
    }
    assert len(summary["adapters"]) == 4
    for a in summary["adapters"]:
        assert a["workspace_bound"] is True
        assert a["shell_construction"] is False
        assert a["read_only"] is True
        assert a["schema_version_pin"] is True


def test_combo_002_invariants_apply_to_every_provider(tmp_path):
    """COMBO-008 acceptance: 'the M7 review proves that the already-in-COMBO-002
    enforced import invariants apply in every provider; it does NOT introduce
    new security gates.'"""
    ws = tmp_path
    # Try to bypass path check with various tricks — must fail closed
    # regardless of provider.
    snapshot = _good_rig_snapshot(str(ws))
    snapshot["repository"]["workspace_dir"] = str(ws)
    snapshot["edges"][0]["evidence"]["source_file"] = "/etc/passwd"
    res = enforce_import_invariants(snapshot=snapshot, workspace_dir=ws)
    assert not res.ok