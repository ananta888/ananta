"""Tests for SnapshotDeltaService — diffs compact DOM snapshots into
human-readable change lines for the visual snake log."""
from __future__ import annotations

import pytest

from agent.services.snapshot_delta import (
    SnapshotDelta,
    diff_snapshots,
    extract_path,
    extract_list_count,
    extract_focus,
)


# ── Pure functions ──────────────────────────────────────────────────────────


def test_extract_path_returns_first_slash_token():
    assert extract_path("/teams | nav:Teams*") == "/teams"


def test_extract_path_returns_empty_for_no_path():
    assert extract_path("h:Teams & Blueprints") == ""


def test_extract_list_count_parses_list_marker():
    assert extract_list_count("/chats | list:7 | h:Chats") == 7


def test_extract_list_count_returns_zero_when_absent():
    assert extract_list_count("/dashboard | h:Dashboard") == 0


def test_extract_focus_parses_input_with_value():
    focus = extract_focus('focus:input[Name]="My Team"')
    assert focus is not None
    assert focus["tag"] == "input"
    assert focus["label"] == "Name"
    assert focus["value"] == "My Team"


def test_extract_focus_returns_none_when_no_focus():
    assert extract_focus("/teams | h:Teams") is None


# ── diff_snapshots — the core delta logic ────────────────────────────────────


def test_diff_identical_snapshots_is_empty():
    snap = "/teams | nav:Teams* | h:Teams"
    d = diff_snapshots(snap, snap)
    assert d.lines == []
    assert d.changed_paths == []


def test_diff_route_change_appears_in_changed_paths():
    prev = "/teams | nav:Teams*"
    curr = "/chats | nav:Chats*"
    d = diff_snapshots(prev, curr)
    assert "/teams → /chats" in d.changed_paths
    assert any("/teams" in l and "/chats" in l for l in d.lines)


def test_diff_list_count_change():
    prev = "/teams | list:3"
    curr = "/teams | list:7"
    d = diff_snapshots(prev, curr)
    assert any("list" in l and "3" in l and "7" in l for l in d.lines)


def test_diff_focus_change():
    prev = "/teams | focus:input[Name]=\"\""
    curr = "/teams | focus:input[Name]=\"My Team\""
    d = diff_snapshots(prev, curr)
    assert any("focus" in l for l in d.lines)
    assert any("My Team" in l for l in d.lines)


def test_diff_preserves_unchanged_sections():
    prev = "/teams | nav:Dashboard|Chats|Teams* | h:Teams"
    curr = "/teams | nav:Dashboard|Chats|Teams* | h:Different Heading"
    d = diff_snapshots(prev, curr)
    # Only heading changed
    assert any("h:" in l for l in d.lines)
    assert not any("nav:" in l for l in d.lines)


def test_diff_multiple_changes_returned_together():
    prev = "/teams | list:3 | h:Teams"
    curr = "/chats | list:5 | h:Chats"
    d = diff_snapshots(prev, curr)
    # Path + list + heading all changed
    assert len(d.changed_paths) == 1
    assert any("list" in l for l in d.lines)
    assert any("h:" in l for l in d.lines)


def test_diff_with_empty_prev_returns_full_snapshot_as_delta():
    """First tick after page-load: no previous snapshot, treat all as new."""
    d = diff_snapshots("", "/teams | nav:Teams* | h:Teams")
    # We don't want to spam the log with the full initial snapshot
    # (that would defeat the purpose of delta-only logging).
    # The contract: empty prev → empty delta, log will just persist the raw tick.
    assert d.lines == []
    assert d.changed_paths == []


def test_diff_handles_unicode_in_snapshot():
    prev = "/teams | h:Blueprints | err:⚠️ Konflikt"
    curr = "/teams | h:Mitglieder | err:keiner"
    d = diff_snapshots(prev, curr)
    assert any("Blueprints" in l and "Mitglieder" in l for l in d.lines)
    assert any("⚠️" in l or "Konflikt" in l for l in d.lines)


def test_snapshot_delta_dataclass_is_immutable():
    d = SnapshotDelta(lines=["a"], changed_paths=["b"])
    with pytest.raises((AttributeError, TypeError)):
        d.lines = ["c"]  # type: ignore[misc]
