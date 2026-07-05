"""RIG-003: SPADE / CMake File API importer tests.

Acceptance criteria (from RIG-003 + CRG-002 + CRG-012 + DD-012):

* Reads CMake File API codemodel-v2 + ctestInfo from a workspace, when
  present.
* Produces buildable_component, target, test, runner and dependency /
  coverage edges with evidence pointing at concrete JSON / CTest files.
* Does not construct shell commands; reads are read-only, path-bounded
  and size-limited.
* Missing or partial File API data yields degraded diagnostics and
  ``coverage_status=partial|unknown``; never hard-exception.
* Tests use a fixture traceable to the pinned SPADE revision
  (6306e203732f7c4553d1564c5250396b7f84a315).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from worker.retrieval.codecompass_rig_cmake_adapter import (
    PROVIDER_ID,
    PROVIDER_REVISION,
    SPADE_REVIEWED_REVISION,
    CmakeRigAdapter,
)
from worker.retrieval.codecompass_import_provider import (
    CodeCompassGraphImportProvider,
)


FIXTURE_DIR = Path(__file__).resolve().parents[0] / "fixtures" / "codecompass" / "rig" / "cmake"


def _install_workspace(workspace: Path, *, with_ctest: bool = True) -> None:
    cm = workspace / ".cmake" / "api" / "v1" / "reply"
    cm.mkdir(parents=True, exist_ok=True)
    (cm / "codemodel-v2-.json").write_text(
        (FIXTURE_DIR / "codemodel_v2.json").read_text())
    if with_ctest:
        (cm / "ctestInfo-.json").write_text(
            (FIXTURE_DIR / "ctest_info.json").read_text())


# ---------------------------------------------------------------------------
# structural conformance
# ---------------------------------------------------------------------------

def test_cmake_adapter_implements_port():
    p = CmakeRigAdapter(workspace_dir=Path("/tmp"))
    assert isinstance(p, CodeCompassGraphImportProvider)


def test_provider_id_is_stable():
    assert PROVIDER_ID == "rig.cmake.file_api"


def test_provider_revision_is_pinned_spade_commit():
    assert PROVIDER_REVISION == SPADE_REVIEWED_REVISION


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def test_probe_unavailable_when_no_codemodel(tmp_path):
    p = CmakeRigAdapter(workspace_dir=tmp_path)
    probe = p.probe()
    assert probe.available is False
    assert probe.reason_unavailable == "external_graph_unavailable"


def test_probe_available_when_codemodel_present(tmp_path):
    _install_workspace(tmp_path, with_ctest=False)
    p = CmakeRigAdapter(workspace_dir=tmp_path)
    probe = p.probe()
    assert probe.available is True
    assert probe.provider_revision == SPADE_REVIEWED_REVISION


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------

def test_import_extracts_targets_and_tests(tmp_path):
    _install_workspace(tmp_path, with_ctest=True)
    p = CmakeRigAdapter(workspace_dir=tmp_path)
    snap = p.import_snapshot()
    assert snap.diagnostics["result"] == "ok"
    assert snap.diagnostics["coverage_status"] == "complete"
    kinds = [n["kind"] for n in snap.graph_nodes]
    assert "buildable_component" in kinds
    assert "test" in kinds
    assert "runner" in kinds
    assert "package_manager" in kinds
    edge_kinds = [e["kind"] for e in snap.graph_edges]
    assert "runs" in edge_kinds
    assert "covers" in edge_kinds


def test_import_partial_coverage_when_ctest_missing(tmp_path):
    _install_workspace(tmp_path, with_ctest=False)
    p = CmakeRigAdapter(workspace_dir=tmp_path)
    snap = p.import_snapshot()
    assert snap.diagnostics["result"] == "ok"
    assert snap.diagnostics["coverage_status"] == "partial"
    assert "ctest_discovery" in snap.diagnostics["unsupported_features"]


def test_import_does_not_construct_shell_commands(tmp_path, monkeypatch):
    """The adapter must not call subprocess / os.system under any input."""
    import os
    import subprocess
    calls = []
    monkeypatch.setattr(os, "system", lambda *a, **kw: calls.append(("system", a, kw)))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(("popen", a, kw)))
    _install_workspace(tmp_path, with_ctest=True)
    CmakeRigAdapter(workspace_dir=tmp_path).import_snapshot()
    assert calls == [], f"shell was called: {calls!r}"


# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------

def test_path_traversal_in_workspace_rejected(tmp_path):
    p = CmakeRigAdapter(workspace_dir=tmp_path.parent / "outside")
    # codemodel file would be installed under tmp_path, not p.workspace_dir
    (tmp_path / "CMakeLists.txt").write_text("# nothing")
    snap = p.import_snapshot()
    assert snap.graph_nodes == ()
    assert snap.diagnostics["result"] == "external_graph_unavailable"


# ---------------------------------------------------------------------------
# coverage truth (CCRIG-DD-008): partial is not negative evidence
# ---------------------------------------------------------------------------

def test_partial_coverage_does_not_pretend_complete(tmp_path):
    _install_workspace(tmp_path, with_ctest=False)
    p = CmakeRigAdapter(workspace_dir=tmp_path)
    snap = p.import_snapshot()
    assert snap.diagnostics["coverage_status"] != "complete"
    # The port does not itself emit a `partial_coverage` warning; that
    # belongs to the consumer (RIG-005). Verify that the diagnostic
    # field is *present and not set to complete*.
    assert "coverage_status" in snap.diagnostics


# ---------------------------------------------------------------------------
# sqlite guard test (extra): the adapter does NOT touch sqlite
# ---------------------------------------------------------------------------

def test_does_not_call_sqlite(tmp_path, monkeypatch):
    import sqlite3 as _real
    opened = []
    def _spy_connect(*a, **kw):
        opened.append((a, kw))
        return _real.connect(*a, **kw)
    monkeypatch.setattr(_real, "connect", _spy_connect)
    _install_workspace(tmp_path, with_ctest=True)
    CmakeRigAdapter(workspace_dir=tmp_path).import_snapshot()
    assert opened == []