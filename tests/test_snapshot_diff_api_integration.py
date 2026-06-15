"""VG-002: Integration tests for POST /api/snapshot/diff using the real Flask
blueprint and test client.  No LLM calls are made — the endpoint is pure-Python.
"""
from __future__ import annotations

import pytest
from flask import Flask
from agent.routes.snapshot_diff_api import snapshot_diff_bp


# ---------------------------------------------------------------------------
# Fixture — minimal Flask app with only the snapshot-diff blueprint
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(snapshot_diff_bp)
    return app.test_client()


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

class TestSnapshotDiffEndpointHappyPath:
    def test_returns_200_with_both_fields(self, client):
        rv = client.post("/api/snapshot/diff", json={
            "prev": "/teams | nav:Teams*",
            "curr": "/chats | nav:Chats*",
        })
        assert rv.status_code == 200

    def test_response_has_required_keys(self, client):
        rv = client.post("/api/snapshot/diff", json={
            "prev": "/teams | nav:Teams*",
            "curr": "/chats | nav:Chats*",
        })
        data = rv.get_json()
        assert "lines" in data
        assert "changed_paths" in data
        assert "is_empty" in data

    def test_route_change_detected(self, client):
        rv = client.post("/api/snapshot/diff", json={
            "prev": "/teams | nav:Teams*",
            "curr": "/chats | nav:Chats*",
        })
        data = rv.get_json()
        assert data["is_empty"] is False
        assert "/teams → /chats" in data["changed_paths"]

    def test_list_count_change_detected(self, client):
        rv = client.post("/api/snapshot/diff", json={
            "prev": "/teams | nav:A | list:3",
            "curr": "/teams | nav:A | list:7",
        })
        data = rv.get_json()
        assert data["is_empty"] is False
        assert any("list" in line and "3" in line and "7" in line
                   for line in data["lines"])

    def test_identical_snapshots_returns_is_empty_true(self, client):
        snap = "/teams | nav:Teams* | h:Teams"
        rv = client.post("/api/snapshot/diff", json={"prev": snap, "curr": snap})
        data = rv.get_json()
        assert data["is_empty"] is True
        assert data["lines"] == []
        assert data["changed_paths"] == []

    def test_empty_prev_returns_empty_delta(self, client):
        """First tick (no previous baseline) → delta must be empty per contract."""
        rv = client.post("/api/snapshot/diff", json={
            "prev": "",
            "curr": "/teams | nav:Teams* | list:3",
        })
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["lines"] == []
        assert data["changed_paths"] == []

    def test_lines_is_list_type(self, client):
        rv = client.post("/api/snapshot/diff", json={
            "prev": "/teams",
            "curr": "/chats",
        })
        data = rv.get_json()
        assert isinstance(data["lines"], list)

    def test_changed_paths_is_list_type(self, client):
        rv = client.post("/api/snapshot/diff", json={
            "prev": "/teams",
            "curr": "/chats",
        })
        data = rv.get_json()
        assert isinstance(data["changed_paths"], list)

    def test_is_empty_is_bool_type(self, client):
        rv = client.post("/api/snapshot/diff", json={
            "prev": "/teams",
            "curr": "/teams",
        })
        data = rv.get_json()
        assert isinstance(data["is_empty"], bool)


# ---------------------------------------------------------------------------
# Navigation-B diff
# ---------------------------------------------------------------------------

class TestSnapshotDiffNavigation:
    def test_nav_active_marker_change_detected(self, client):
        """Changing active nav entry (Teams* → Chats*) shows up in diff."""
        rv = client.post("/api/snapshot/diff", json={
            "prev": "/teams | nav:Teams*",
            "curr": "/teams | nav:Chats*",
        })
        data = rv.get_json()
        assert data["is_empty"] is False

    def test_multiple_changes_all_reported(self, client):
        rv = client.post("/api/snapshot/diff", json={
            "prev": "/teams | list:3 | h:Teams",
            "curr": "/chats | list:5 | h:Chats",
        })
        data = rv.get_json()
        assert data["is_empty"] is False
        # Expect path + list + heading lines
        text = " ".join(data["lines"])
        assert "list" in text
        assert "h:" in text or "Chats" in text


# ---------------------------------------------------------------------------
# Error / validation tests
# ---------------------------------------------------------------------------

class TestSnapshotDiffValidation:
    def test_missing_curr_returns_400(self, client):
        rv = client.post("/api/snapshot/diff", json={"prev": "/teams"})
        assert rv.status_code == 400

    def test_missing_prev_returns_400(self, client):
        rv = client.post("/api/snapshot/diff", json={"curr": "/chats"})
        assert rv.status_code == 400

    def test_empty_body_returns_400(self, client):
        rv = client.post("/api/snapshot/diff", json={})
        assert rv.status_code == 400

    def test_error_response_has_error_key(self, client):
        rv = client.post("/api/snapshot/diff", json={"prev": "/teams"})
        data = rv.get_json()
        assert "error" in data

    def test_non_json_body_is_handled(self, client):
        """Non-JSON body must not crash the server (treated as empty)."""
        rv = client.post("/api/snapshot/diff",
                         data="not json",
                         content_type="text/plain")
        # Either 400 (missing fields) or 200 with empty diff — no 500
        assert rv.status_code in (200, 400)


# ---------------------------------------------------------------------------
# Unicode / special characters
# ---------------------------------------------------------------------------

class TestSnapshotDiffUnicode:
    def test_unicode_in_snapshot_handled(self, client):
        rv = client.post("/api/snapshot/diff", json={
            "prev": "/teams | h:Blueprints | err:⚠️ Konflikt",
            "curr": "/teams | h:Mitglieder | err:keiner",
        })
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["is_empty"] is False
