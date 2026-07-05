"""COMBO-002: graph evidence / trust policy import-edge tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.tools.graph_evidence import (
    MAX_RIG_ENTITIES_PER_KIND,
    MAX_RIG_SNAPSHOT_BYTES,
    POLICY_ALLOWED_TRUST,
    enforce_import_invariants,
    validate_graph_evidence,
    validate_repository_intelligence_snapshot,
)


# ---------------------------------------------------------------------------
# graph-evidence schema (DD-016)
# ---------------------------------------------------------------------------

def _good_evidence() -> dict:
    return {
        "trust_level": "extracted",
        "verification_status": "verified",
        "confidence": 0.95,
        "evidence": {
            "source_file": "CMakeLists.txt",
            "source_kind": "spade_cmake_reply",
            "source_record_id": "target:foo",
            "source_run_id": "run:abc123",
        },
        "provenance": {
            "source": "spade",
            "provider_id": "rig.cmake",
            "provider_revision": "6306e203732f7c4553d1564c5250396b7f84a315",
            "extractor_id": "spade.cmake.file_api",
            "extractor_version": "0.1.0",
            "build_system": "cmake",
        },
    }


def test_validate_graph_evidence_happy_path():
    res = validate_graph_evidence(_good_evidence())
    assert res.ok, res.as_dict()


def test_validate_graph_evidence_rejects_missing_required():
    res = validate_graph_evidence({"trust_level": "extracted"})
    assert not res.ok
    reasons = {f.reason for f in res.failures}
    assert "required" in reasons


def test_validate_graph_evidence_rejects_unknown_trust_level():
    bad = _good_evidence()
    bad["trust_level"] = "magic"
    res = validate_graph_evidence(bad)
    assert not res.ok
    assert any(f.path.startswith("/trust_level") for f in res.failures)


def test_inferred_must_have_low_confidence():
    ev = _good_evidence()
    ev["trust_level"] = "inferred"
    ev["confidence"] = 0.95  # too high
    res = validate_graph_evidence(ev)
    assert not res.ok
    assert any("confidence" in f.path for f in res.failures)


def test_ambiguous_cannot_be_policy_allowed():
    """ambiguous entries must not be policy-allowed regardless of verification."""
    ev = _good_evidence()
    ev["trust_level"] = "ambiguous"
    ev["confidence"] = 0.4
    assert "ambiguous" not in POLICY_ALLOWED_TRUST


def test_manual_must_have_reason_and_manual_provenance():
    ev = _good_evidence()
    ev["trust_level"] = "manual"
    # missing evidence.reason
    res = validate_graph_evidence(ev)
    assert not res.ok
    # now fix reason but wrong provenance source
    ev["evidence"]["reason"] = "manual_fixture"
    ev["provenance"]["source"] = "spade"
    res = validate_graph_evidence(ev)
    assert not res.ok


def test_validation_collects_all_failures_not_short_circuit():
    bad = {
        "trust_level": "unknown_level",
        "verification_status": "broken",
        # missing evidence + provenance
    }
    res = validate_graph_evidence(bad)
    assert len(res.failures) >= 3


def test_non_object_input_is_rejected():
    res = validate_graph_evidence("just a string")  # type: ignore[arg-type]
    assert not res.ok
    assert res.failures[0].reason == "not_an_object"


# ---------------------------------------------------------------------------
# RIG snapshot schema (DD-014)
# ---------------------------------------------------------------------------

def _good_rig() -> dict:
    return {
        "schema_version": "codecompass.repository-intelligence.v1",
        "snapshot_id": "snap_a1b2c3d4e5f6",
        "extractor": {
            "id": "spade.cmake.file_api",
            "version": "0.1.0",
            "build_system": "cmake",
            "reviewed_revision": "6306e203732f7c4553d1564c5250396b7f84a315",
        },
        "repository": {
            "repository_id": "ananta",
            "workspace_dir": "/workspace",
        },
        "coverage": {
            "status": "complete",
            "unsupported_features": [],
        },
        "entities": {
            "package_managers": [{"id": "pm:cmake", "kind": "cmake", "version": "3.27"}],
            "external_packages": [],
            "buildable_components": [{"id": "bc:foo", "name": "foo", "kind": "library"}],
            "aggregators": [],
            "runners": [{"id": "rn:ctest", "kind": "ctest"}],
            "tests": [{"id": "t:foo_test", "name": "foo_test"}],
        },
        "edges": [
            {
                "kind": "tested_by",
                "from_id": "bc:foo",
                "to_id": "rn:ctest",
                "evidence": {
                    "source_file": "/workspace/CMakeLists.txt",
                    "source_kind": "spade_cmake_reply",
                    "source_record_id": "target:foo",
                },
                "trust": _good_evidence(),
            }
        ],
        "generated_at": "2026-07-05T12:00:00Z",
    }


def test_validate_rig_snapshot_happy_path():
    res = validate_repository_intelligence_snapshot(_good_rig())
    assert res.ok, res.as_dict()


def test_validate_rig_snapshot_rejects_wrong_schema_version():
    bad = _good_rig()
    bad["schema_version"] = "codecompass.repository-intelligence.v999"
    res = validate_repository_intelligence_snapshot(bad)
    assert not res.ok
    assert any("schema_version" in f.path for f in res.failures)


def test_validate_rig_snapshot_rejects_too_many_entities():
    bad = _good_rig()
    bad["entities"]["buildable_components"] = [
        {"id": f"bc:{i}", "name": f"bc{i}", "kind": "library"} for i in range(MAX_RIG_ENTITIES_PER_KIND + 1)
    ]
    res = validate_repository_intelligence_snapshot(bad)
    assert not res.ok
    assert any(f.reason == "payload_too_large" for f in res.failures)


# ---------------------------------------------------------------------------
# enforce_import_invariants (DD-013 / DD-016 fail-closed)
# ---------------------------------------------------------------------------

def test_enforce_import_invariants_path_outside_workspace(tmp_path):
    snap = _good_rig()
    snap["repository"]["workspace_dir"] = "/etc/passwd"
    res = enforce_import_invariants(snapshot=snap, workspace_dir=tmp_path)
    assert not res.ok
    assert any(f.reason == "path_outside_workspace" for f in res.failures)


def test_enforce_import_invariants_path_traversal(tmp_path):
    snap = _good_rig()
    snap["edges"][0]["evidence"]["source_file"] = str(tmp_path) + "/../etc/passwd"
    res = enforce_import_invariants(snapshot=snap, workspace_dir=tmp_path)
    assert not res.ok
    assert any(f.reason == "path_outside_workspace" for f in res.failures)


def test_enforce_import_invariants_symlink_escape(tmp_path):
    """Symlink inside workspace pointing outside must be rejected.

    We create a symlink under tmp_path that points to /etc/passwd and
    verify the validator refuses to follow it.
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    sym = real_dir / "linked_etc"
    try:
        sym.symlink_to("/etc/passwd")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    snap = _good_rig()
    snap["repository"]["workspace_dir"] = str(tmp_path)
    snap["edges"][0]["evidence"]["source_file"] = str(sym)
    res = enforce_import_invariants(snapshot=snap, workspace_dir=tmp_path)
    assert not res.ok
    assert any(f.reason == "path_outside_workspace" for f in res.failures)


def test_enforce_import_invariants_size_limit():
    snap = _good_rig()
    res = enforce_import_invariants(
        snapshot=snap,
        workspace_dir=Path("/workspace"),
        raw_bytes=b"x" * (MAX_RIG_SNAPSHOT_BYTES + 1),
    )
    assert not res.ok
    assert any(f.reason == "payload_too_large" for f in res.failures)


def test_enforce_import_invariants_rejects_secret_like_value(tmp_path):
    snap = _good_rig()
    snap["edges"][0]["evidence"]["source_record_id"] = "AKIAIOSFODNN7EXAMPLE"
    res = enforce_import_invariants(snapshot=snap, workspace_dir=tmp_path)
    assert not res.ok
    assert any(f.reason == "secret_like_value" for f in res.failures)


def test_enforce_import_invariants_accepts_clean_snapshot(tmp_path):
    snap = _good_rig()
    snap["repository"]["workspace_dir"] = str(tmp_path)
    snap["edges"][0]["evidence"]["source_file"] = str(tmp_path / "CMakeLists.txt")
    # create the file so path-within-workspace resolves cleanly
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n")
    res = enforce_import_invariants(snapshot=snap, workspace_dir=tmp_path)
    assert res.ok, res.as_dict()


def test_unknown_source_ids_are_not_synthesized():
    """AGENTS.md: agents and workers must never invent source identifiers.

    The validator must not produce or accept synthetic IDs in place of
    missing source_record_id / source_run_id. Such entries are simply
    invalid.
    """
    bad = _good_evidence()
    # Drop all identifying IDs and reason — must fail.
    bad["evidence"] = {
        "source_file": "CMakeLists.txt",
        "source_kind": "spade_cmake_reply",
    }
    res = validate_graph_evidence(bad)
    assert not res.ok
    synthetic_ids = [f for f in res.failures if "synth" in (f.detail or "").lower()]
    assert synthetic_ids == []


def test_unknown_source_ids_in_rig_edge_fail():
    """Same guarantee at the RIG-edge level: edges with evidence lacking
    source_record_id/source_run_id must be rejected; never silently
    rewritten with a placeholder."""
    snap = _good_rig()
    snap["edges"][0]["evidence"] = {
        "source_file": "/workspace/CMakeLists.txt",
        "source_kind": "spade_cmake_reply",
        # source_record_id and source_run_id missing — must fail
    }
    res = validate_repository_intelligence_snapshot(snap)
    assert not res.ok
    synthetic_ids = [f for f in res.failures if "synth" in (f.detail or "").lower()]
    assert synthetic_ids == []