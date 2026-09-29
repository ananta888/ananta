"""Tests for CaseFlow REST API (CASECORE-006) — Flask test client."""
from __future__ import annotations

import json
import pytest
from flask import Flask


@pytest.fixture
def caseflow_client():
    from agent.routes.caseflow import caseflow_bp, reset_stores
    from agent.caseflow.timeline import clear_events
    from agent.job_module import setup as job_setup

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(caseflow_bp)
    job_setup()

    reset_stores()
    clear_events()
    yield app.test_client()
    reset_stores()
    clear_events()


def _create_case(client, case_type="generic", title="Test Case", **kwargs):
    body = {"case_type": case_type, "title": title, **kwargs}
    return client.post("/api/caseflow/cases", json=body)


class TestCaseFlowApi:
    def test_create_case_returns_201(self, caseflow_client):
        resp = _create_case(caseflow_client)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["case_type"] == "generic"
        assert data["title"] == "Test Case"
        assert data["status"] == "new"
        assert "id" in data

    def test_list_cases_returns_list(self, caseflow_client):
        _create_case(caseflow_client, title="A")
        _create_case(caseflow_client, title="B")
        resp = caseflow_client.get("/api/caseflow/cases")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 2

    def test_get_case_returns_case(self, caseflow_client):
        created = _create_case(caseflow_client).get_json()
        case_id = created["id"]
        resp = caseflow_client.get(f"/api/caseflow/cases/{case_id}")
        assert resp.status_code == 200
        assert resp.get_json()["id"] == case_id

    def test_patch_case_updates_fields(self, caseflow_client):
        created = _create_case(caseflow_client, title="Original").get_json()
        case_id = created["id"]
        resp = caseflow_client.patch(
            f"/api/caseflow/cases/{case_id}",
            json={"title": "Updated", "priority": "high"}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Updated"
        assert data["priority"] == "high"

    def test_transition_valid_returns_ok(self, caseflow_client):
        created = _create_case(caseflow_client).get_json()
        case_id = created["id"]
        resp = caseflow_client.post(
            f"/api/caseflow/cases/{case_id}/transition",
            json={"to_status": "active", "actor": "user"}
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_transition_invalid_returns_422_with_error_code(self, caseflow_client):
        # Create a job_application case
        created = _create_case(
            caseflow_client, case_type="job_application", title="Job App"
        ).get_json()
        case_id = created["id"]
        # found -> offer is not allowed
        resp = caseflow_client.post(
            f"/api/caseflow/cases/{case_id}/transition",
            json={"to_status": "offer", "actor": "user"}
        )
        assert resp.status_code == 422
        data = resp.get_json()
        assert data["ok"] is False
        assert "error_code" in data

    def test_timeline_returns_events_chronologically(self, caseflow_client):
        created = _create_case(caseflow_client, title="Timeline Test").get_json()
        case_id = created["id"]
        # Transition adds another event
        caseflow_client.post(
            f"/api/caseflow/cases/{case_id}/transition",
            json={"to_status": "active", "actor": "user"}
        )
        resp = caseflow_client.get(f"/api/caseflow/cases/{case_id}/timeline")
        assert resp.status_code == 200
        events = resp.get_json()
        assert len(events) >= 2
        # First event is case_created
        assert events[0]["event_type"] == "case_created"

    def test_open_actions_endpoint(self, caseflow_client):
        created = _create_case(caseflow_client, title="T").get_json()
        case_id = created["id"]
        caseflow_client.post(
            f"/api/caseflow/cases/{case_id}/actions",
            json={"action_type": "review", "title": "Review document"}
        )
        resp = caseflow_client.get("/api/caseflow/actions/open")
        assert resp.status_code == 200
        actions = resp.get_json()
        assert any(a["title"] == "Review document" for a in actions)
