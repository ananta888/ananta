"""Tests for PolicySnapshotService (TRANS-002)."""
from __future__ import annotations

import json

from agent.services.policy_snapshot_service import PolicySnapshot, PolicySnapshotService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service() -> PolicySnapshotService:
    return PolicySnapshotService()


def _minimal_snap(run_id: str = "run-001") -> PolicySnapshot:
    svc = _make_service()
    return svc.capture(run_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_capture_has_stable_hash() -> None:
    """Same policy configuration must produce the same config_hash."""
    svc = _make_service()

    class FakeSettings:
        allowed_paths = ["/workspace"]
        denied_paths: list[str] = []
        allowed_tools = ["read_file"]
        denied_tools: list[str] = []
        allowed_providers = ["ollama"]
        model_policy = "local_only"
        network_policy = "deny_all"
        write_policy = "proposal_only"
        approval_gates: list[str] = []

    snap_a = svc.capture("run-abc", settings_obj=FakeSettings())
    snap_b = svc.capture("run-abc", settings_obj=FakeSettings())

    assert snap_a.config_hash == snap_b.config_hash
    assert snap_a.config_hash != ""


def test_hash_changes_with_policy() -> None:
    """Changing allowed_paths must produce a different config_hash."""
    svc = _make_service()

    class SettingsA:
        allowed_paths = ["/workspace/a"]
        denied_paths: list[str] = []
        allowed_tools: list[str] = []
        denied_tools: list[str] = []
        allowed_providers = ["ollama"]
        model_policy = "local_only"
        network_policy = "deny_all"
        write_policy = "proposal_only"
        approval_gates: list[str] = []

    class SettingsB:
        allowed_paths = ["/workspace/b"]
        denied_paths: list[str] = []
        allowed_tools: list[str] = []
        denied_tools: list[str] = []
        allowed_providers = ["ollama"]
        model_policy = "local_only"
        network_policy = "deny_all"
        write_policy = "proposal_only"
        approval_gates: list[str] = []

    snap_a = svc.capture("run-x", settings_obj=SettingsA())
    snap_b = svc.capture("run-x", settings_obj=SettingsB())

    assert snap_a.config_hash != snap_b.config_hash


def test_validate_missing_run_id() -> None:
    """A PolicySnapshot with empty run_id must produce a validation issue."""
    svc = _make_service()
    snap = svc.capture("")  # empty run_id
    result = svc.validate(snap)
    assert result["valid"] is False
    assert any("run_id" in issue for issue in result["issues"])


def test_validate_none_returns_missing() -> None:
    """validate(None) returns {"valid": False, "issues": ["missing"]}."""
    svc = _make_service()
    result = svc.validate(None)
    assert result["valid"] is False
    assert "missing" in result["issues"]


def test_serialize_roundtrip() -> None:
    """Serialized JSON must contain all PolicySnapshot fields and parse back correctly."""
    svc = _make_service()
    snap = _minimal_snap("run-serial")
    serialized = svc.serialize(snap)

    parsed = json.loads(serialized)
    assert parsed["run_id"] == "run-serial"
    assert "snapshot_id" in parsed
    assert "config_hash" in parsed
    assert "model_policy" in parsed
    assert "network_policy" in parsed
    assert "write_policy" in parsed
    assert isinstance(parsed["allowed_paths"], list)
    assert isinstance(parsed["approval_gates"], list)


def test_from_settings_defaults() -> None:
    """from_settings with no settings object must return sensible defaults."""
    snap = PolicySnapshot.from_settings("run-defaults", settings_obj=None)

    assert snap.run_id == "run-defaults"
    assert snap.model_policy in ("local_only", "any", "allowlist")
    assert snap.network_policy in ("deny_all", "restricted", "allowed")
    assert snap.write_policy in ("proposal_only", "approval_required", "unrestricted")
    assert isinstance(snap.allowed_providers, list)
    assert len(snap.allowed_providers) > 0
    assert snap.config_hash != ""


def test_validate_valid_snapshot() -> None:
    """A correctly-formed snapshot must pass validation."""
    svc = _make_service()
    snap = _minimal_snap("run-valid")
    result = svc.validate(snap)
    assert result["valid"] is True
    assert result["issues"] == []


def test_compute_hash_matches_stored() -> None:
    """compute_hash() must return the same value stored in config_hash."""
    svc = _make_service()
    snap = _minimal_snap("run-hash-check")
    assert svc.compute_hash(snap) == snap.config_hash


def test_snapshot_ids_are_unique() -> None:
    """Each captured snapshot gets a unique snapshot_id."""
    svc = _make_service()
    ids = {svc.capture("run-u").snapshot_id for _ in range(5)}
    assert len(ids) == 5
