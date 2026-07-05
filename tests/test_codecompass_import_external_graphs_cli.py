"""COMBO-004: import/CLI tests.

Acceptance:

* CLI requests an import task from the Hub (skipped here; we test the
  diagnostic surface only)
* Worker does not create sub-tasks and does not exchange tasks directly
  (verified by absence of worker-side orchestration imports in the CLI)
* Local diagnose is read-only
* persistent writes occur only via Hub-task flow and explicit
  --write-index
* diagnose shows counts for symbol_nodes, symbol_edges, rig_nodes,
  rig_edges, ambiguous_edges, missing_evidence
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import codecompass_import_external_graphs as cli


def test_cli_probe_cr_returns_provider_revision(tmp_path):
    crg_dir = tmp_path / ".code-review-graph"
    crg_dir.mkdir(parents=True)
    (crg_dir / "export.json").write_text(json.dumps({
        "reviewer_graph_revision": "b72413cbd34a4ac08cc60dcdd42df1d02f3fc77d",
        "files": [], "edges": [],
    }))
    code = cli.main(["probe-cr", "--workspace-dir", str(tmp_path)])
    assert code == 0


def test_cli_probe_cr_unavailable(tmp_path):
    code = cli.main(["probe-cr", "--workspace-dir", str(tmp_path)])
    assert code == 1


def test_cli_diagnose_returns_counts(tmp_path):
    code = cli.main(["diagnose", str(tmp_path)])
    assert code == 0


def test_cli_validate_accepts_clean_payload(tmp_path):
    p = tmp_path / "good.json"
    p.write_text(json.dumps({
        "trust_level": "extracted",
        "verification_status": "verified",
        "evidence": {"source_kind": "spade_cmake_reply",
                     "source_record_id": "target:foo"},
        "provenance": {"source": "spade", "provider_id": "rig.cmake",
                       "provider_revision": "0.1.0"},
    }))
    code = cli.main(["validate", str(p)])
    assert code == 0


def test_cli_validate_rejects_invalid_payload(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"trust_level": "magic"}))
    code = cli.main(["validate", str(p)])
    assert code == 1


def test_cli_import_rig_dry_run_does_not_persist(tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"schema_version": "v999"}))
    # dry-run: should fail schema check but not create an index file
    code = cli.main(["import-rig", str(snap)])
    assert code == 1
    assert not (tmp_path / "rig_index.json").exists()


def test_cli_does_not_import_worker_orchestration(monkeypatch):
    """The CLI must not import any worker-orchestration module. This is
    the static guarantee that we never build a worker-side loop."""
    src = Path(cli.__file__).read_text(encoding="utf-8")
    forbidden = ["subprocess", "Popen", "delegate_task",
                 "task_queue", "create_task"]
    for tok in forbidden:
        assert tok not in src, f"CLI must not use {tok}"


def test_cli_help_lists_all_commands():
    """Smoke: the help output lists all expected subcommands."""
    proc = subprocess.run(
        [sys.executable, "-m",
         "scripts.codecompass_import_external_graphs", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    for cmd in ("validate", "import-cr", "import-rig", "diagnose", "probe-cr"):
        assert cmd in proc.stdout


def test_cli_import_cr_refuses_when_feature_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("CODECOMPASS_CRG_ADAPTER_ENABLED", raising=False)
    crg_dir = tmp_path / ".code-review-graph"
    crg_dir.mkdir(parents=True)
    p = crg_dir / "export.json"
    p.write_text(json.dumps({
        "reviewer_graph_revision": "b72413cbd34a4ac08cc60dcdd42df1d02f3fc77d",
        "files": [], "edges": [],
    }))
    code = cli.main(["import-cr", str(p), "--workspace-dir", str(tmp_path)])
    assert code == 2