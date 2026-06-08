"""Tests for the Three-Way Flex Diff API (T01-T03)."""
from __future__ import annotations

import pytest
import json


@pytest.fixture
def client():
    """Flask test client with diff3 blueprint registered."""
    from flask import Flask
    from agent.routes.diff3 import diff3_bp, _SESSIONS
    _SESSIONS.clear()
    app = Flask(__name__)
    app.register_blueprint(diff3_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    _SESSIONS.clear()


def _post_json(client, url, data=None):
    return client.post(url, data=json.dumps(data or {}), content_type="application/json")

def _put_json(client, url, data=None):
    return client.put(url, data=json.dumps(data or {}), content_type="application/json")


# ── T01: Session model / interaction flow ────────────────────────────────────

class TestSessionFlow:
    def test_create_session_defaults(self, client):
        resp = _post_json(client, "/api/diff3/sessions")
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["schema"] == "three_way_diff_session.v1"
        assert len(data["panels"]) == 3
        assert data["layout_mode"] == "equal"
        assert data["active_panel"] == "A"

    def test_create_session_with_goal(self, client):
        resp = _post_json(client, "/api/diff3/sessions", {"goal_id": "g-123"})
        assert resp.status_code == 201
        assert resp.get_json()["goal_id"] == "g-123"

    def test_create_session_custom_layout(self, client):
        resp = _post_json(client, "/api/diff3/sessions", {"layout_mode": "left-wide"})
        assert resp.status_code == 201
        assert resp.get_json()["layout_mode"] == "left-wide"

    def test_create_session_invalid_layout_falls_back(self, client):
        resp = _post_json(client, "/api/diff3/sessions", {"layout_mode": "bogus"})
        assert resp.status_code == 201
        assert resp.get_json()["layout_mode"] == "equal"

    def test_get_session(self, client):
        create = _post_json(client, "/api/diff3/sessions").get_json()
        sid = create["session_id"]
        resp = client.get(f"/api/diff3/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.get_json()["session_id"] == sid

    def test_get_session_not_found(self, client):
        resp = client.get("/api/diff3/sessions/does-not-exist")
        assert resp.status_code == 404

    def test_delete_session(self, client):
        sid = _post_json(client, "/api/diff3/sessions").get_json()["session_id"]
        del_resp = client.delete(f"/api/diff3/sessions/{sid}")
        assert del_resp.status_code == 200
        assert del_resp.get_json()["ok"] is True
        assert client.get(f"/api/diff3/sessions/{sid}").status_code == 404

    def test_set_focus(self, client):
        sid = _post_json(client, "/api/diff3/sessions").get_json()["session_id"]
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/focus", {"panel_id": "C"})
        assert resp.status_code == 200
        assert resp.get_json()["active_panel"] == "C"

    def test_set_focus_invalid_panel(self, client):
        sid = _post_json(client, "/api/diff3/sessions").get_json()["session_id"]
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/focus", {"panel_id": "Z"})
        assert resp.status_code == 400

    def test_set_layout(self, client):
        sid = _post_json(client, "/api/diff3/sessions").get_json()["session_id"]
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/layout", {"layout_mode": "right-wide"})
        assert resp.status_code == 200
        assert resp.get_json()["layout_mode"] == "right-wide"

    def test_set_sync(self, client):
        sid = _post_json(client, "/api/diff3/sessions").get_json()["session_id"]
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/sync", {"sync": True})
        assert resp.status_code == 200
        assert resp.get_json()["extensions"]["sync_scroll"] is True


# ── T02: Panel configuration ──────────────────────────────────────────────────

class TestPanelConfiguration:
    def _sid(self, client) -> str:
        return _post_json(client, "/api/diff3/sessions").get_json()["session_id"]

    def test_set_panel_current_diff(self, client):
        sid = self._sid(client)
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/panels/A",
                         {"source_kind": "current_diff", "render_mode": "unified"})
        assert resp.status_code == 200
        data = resp.get_json()
        panel_a = next(p for p in data["panels"] if p["panel_id"] == "A")
        assert panel_a["panel_type"] == "diff"
        assert panel_a["source_left"]["source_kind"] == "git_diff"

    def test_set_panel_current_diff_summary_mode(self, client):
        sid = self._sid(client)
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/panels/B",
                         {"source_kind": "current_diff", "render_mode": "summary"})
        assert resp.status_code == 200
        panel_b = next(p for p in resp.get_json()["panels"] if p["panel_id"] == "B")
        assert panel_b["render_mode"] == "summary"

    def test_set_panel_output_artifact(self, client):
        sid = self._sid(client)
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/panels/B",
                         {"source_kind": "output_artifact", "output_artifact_id": "out-abc123"})
        assert resp.status_code == 200
        panel_b = next(p for p in resp.get_json()["panels"] if p["panel_id"] == "B")
        assert panel_b["source_left"]["source_kind"] == "goal_output_artifact"
        assert "out-abc123" in panel_b["source_left"]["source_ref_id"]

    def test_set_panel_output_artifact_missing_id(self, client):
        sid = self._sid(client)
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/panels/B",
                         {"source_kind": "output_artifact"})
        assert resp.status_code == 400

    def test_set_panel_ai_mode(self, client):
        sid = self._sid(client)
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/panels/C",
                         {"source_kind": "ai", "ai_mode": "review"})
        assert resp.status_code == 200
        data = resp.get_json()
        ai_state = data["extensions"].get("ai_panel_state")
        assert ai_state is not None
        assert ai_state["mode"] == "review"
        assert ai_state["status"] == "idle"

    def test_set_panel_ai_invalid_mode(self, client):
        sid = self._sid(client)
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/panels/C",
                         {"source_kind": "ai", "ai_mode": "teleport"})
        assert resp.status_code == 400

    def test_set_panel_empty(self, client):
        sid = self._sid(client)
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/panels/A",
                         {"source_kind": "empty"})
        assert resp.status_code == 200
        panel_a = next(p for p in resp.get_json()["panels"] if p["panel_id"] == "A")
        assert panel_a["panel_type"] == "empty"

    def test_invalid_panel_id(self, client):
        sid = self._sid(client)
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/panels/X",
                         {"source_kind": "empty"})
        assert resp.status_code == 400

    def test_all_three_panels_configurable(self, client):
        sid = self._sid(client)
        for pid, sk in [("A", "current_diff"), ("B", "current_diff"), ("C", "empty")]:
            resp = _put_json(client, f"/api/diff3/sessions/{sid}/panels/{pid}",
                             {"source_kind": sk})
            assert resp.status_code == 200


# ── T03: AI mode behavior ─────────────────────────────────────────────────────

class TestAiMode:
    def _sid(self, client) -> str:
        return _post_json(client, "/api/diff3/sessions").get_json()["session_id"]

    def test_set_ai_mode_idle(self, client):
        sid = self._sid(client)
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/ai/mode", {"mode": "explain"})
        assert resp.status_code == 200
        ai_state = resp.get_json()["extensions"]["ai_panel_state"]
        assert ai_state["mode"] == "explain"
        assert ai_state["status"] == "idle"

    def test_set_ai_mode_invalid(self, client):
        sid = self._sid(client)
        resp = _put_json(client, f"/api/diff3/sessions/{sid}/ai/mode", {"mode": "teleport"})
        assert resp.status_code == 400

    def test_all_valid_ai_modes(self, client):
        for mode in ("review", "explain", "risk", "tests", "patch", "chat"):
            sid = self._sid(client)
            resp = _put_json(client, f"/api/diff3/sessions/{sid}/ai/mode", {"mode": mode})
            assert resp.status_code == 200, f"mode {mode!r} failed"
            assert resp.get_json()["extensions"]["ai_panel_state"]["mode"] == mode

    def test_run_ai_returns_response(self, client):
        sid = self._sid(client)
        # Set up a panel first
        _put_json(client, f"/api/diff3/sessions/{sid}/panels/A",
                  {"source_kind": "current_diff"})
        resp = _post_json(client, f"/api/diff3/sessions/{sid}/ai/run", {"mode": "review"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "session" in data
        assert "ai_result" in data
        assert data["ai_result"]["status"] in ("success", "degraded")

    def test_run_ai_invalid_mode(self, client):
        sid = self._sid(client)
        resp = _post_json(client, f"/api/diff3/sessions/{sid}/ai/run", {"mode": "hack"})
        assert resp.status_code == 400

    def test_run_ai_updates_session_state(self, client):
        sid = self._sid(client)
        resp = _post_json(client, f"/api/diff3/sessions/{sid}/ai/run", {"mode": "explain"})
        assert resp.status_code == 200
        session = resp.get_json()["session"]
        ai_state = session["extensions"].get("ai_panel_state")
        assert ai_state is not None
        assert ai_state["status"] in ("completed", "degraded")

    def test_run_ai_response_has_summary(self, client):
        sid = self._sid(client)
        resp = _post_json(client, f"/api/diff3/sessions/{sid}/ai/run", {"mode": "review"})
        assert resp.status_code == 200
        response = resp.get_json()["ai_result"]["response"]
        assert "summary" in response
        assert "findings" in response

    def test_run_ai_persists_in_session(self, client):
        sid = self._sid(client)
        _post_json(client, f"/api/diff3/sessions/{sid}/ai/run", {"mode": "risk"})
        get_resp = client.get(f"/api/diff3/sessions/{sid}")
        session = get_resp.get_json()
        assert "ai_last_response" in session["extensions"]

    def test_fallback_on_unknown_session(self, client):
        resp = _post_json(client, "/api/diff3/sessions/no-such-session/ai/run",
                          {"mode": "review"})
        assert resp.status_code == 404

    def test_three_way_full_flow(self, client):
        """T01-T03 full integration: create → configure panels → run AI."""
        # Create
        sid = _post_json(client, "/api/diff3/sessions",
                         {"layout_mode": "equal"}).get_json()["session_id"]
        # Panel A: git diff
        _put_json(client, f"/api/diff3/sessions/{sid}/panels/A",
                  {"source_kind": "current_diff", "render_mode": "unified"})
        # Panel B: summary
        _put_json(client, f"/api/diff3/sessions/{sid}/panels/B",
                  {"source_kind": "current_diff", "render_mode": "summary"})
        # Panel C: AI
        _put_json(client, f"/api/diff3/sessions/{sid}/panels/C",
                  {"source_kind": "ai", "ai_mode": "review"})
        # Set focus C
        focus = _put_json(client, f"/api/diff3/sessions/{sid}/focus",
                          {"panel_id": "C"}).get_json()
        assert focus["active_panel"] == "C"
        # Run AI
        run_resp = _post_json(client, f"/api/diff3/sessions/{sid}/ai/run",
                              {"mode": "review"})
        assert run_resp.status_code == 200
        ai_result = run_resp.get_json()["ai_result"]
        assert ai_result["response"]["schema"] == "ai_diff_response.v1"
