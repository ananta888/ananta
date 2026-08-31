from __future__ import annotations

from collections.abc import Mapping

from flask import Flask

from agent.routes.business_controlling import business_controlling_bp


class _Workbench:
    def __init__(self) -> None:
        self.findings: list[dict[str, object]] = []

    def status(self, **_: object) -> Mapping[str, object]:
        return {
            "schema": "ananta.business-controlling-status.v1",
            "enabled": True,
            "read_only": True,
            "statistics_enabled": False,
            "explanations_enabled": True,
        }

    def profile_import(self, **kwargs: object) -> Mapping[str, object]:
        payload = dict(kwargs["request_payload"])  # type: ignore[arg-type]
        assert "rows" not in payload
        return {
            "profile_digest": "a" * 64,
            "source_revision_id": payload["source_revision_id"],
            "row_count": 3,
            "columns": [{"header": "amount", "inferred_type": "decimal"}],
        }

    def confirm_mapping(self, **kwargs: object) -> Mapping[str, object]:
        payload = dict(kwargs["request_payload"])  # type: ignore[arg-type]
        return {
            "profile_digest": payload["profile_digest"],
            "confirmation_digest": "b" * 64,
            "column_mapping": payload["column_mapping"],
        }

    def start_run(self, **kwargs: object) -> Mapping[str, object]:
        payload = dict(kwargs["request_payload"])  # type: ignore[arg-type]
        assert payload["statistics_enabled"] is False
        self.findings = [
            {
                "finding_id": "finding-a",
                "kind": "deterministic_violation",
                "severity": "high",
                "dataset_version": "dataset-version-a",
                "rule_version": "v1",
                "confidence": None,
                "evidence_digest": "c" * 64,
                "disposition": "open",
                "revision": 0,
            }
        ]
        return {"run_id": "run-a", "status": "completed", "finding_count": 1}

    def list_findings(self, **_: object) -> tuple[Mapping[str, object], ...]:
        return tuple(self.findings)

    def set_disposition(self, **kwargs: object) -> Mapping[str, object]:
        assert kwargs["expected_revision"] == 0
        self.findings[0] = {
            **self.findings[0],
            "disposition": kwargs["disposition"],
            "revision": 1,
        }
        return self.findings[0]

    def export_findings(self, **_: object) -> Mapping[str, object]:
        return {
            "schema": "ananta.business-controlling-export.v1",
            "finding_count": len(self.findings),
            "content_redacted": True,
            "report_digest": "d" * 64,
        }


def _client(monkeypatch):
    monkeypatch.setattr("agent.auth.resolve_configured_agent_token", lambda: None)
    app = Flask(__name__)
    app.register_blueprint(business_controlling_bp)
    app.extensions["business_controlling_workbench"] = _Workbench()
    return app.test_client()


def test_real_api_contract_runs_import_to_disposition_without_human_step(monkeypatch) -> None:
    client = _client(monkeypatch)
    scope = {"tenant_id": "tenant-a", "project_id": "project-a"}

    status = client.get("/api/v1/controlling/status", query_string=scope)
    profile = client.post(
        "/api/v1/controlling/imports/profile",
        json={
            **scope,
            "source_revision_id": "source-revision-a",
            "revision_digest": "1" * 64,
            "source_format": "csv",
        },
    )
    mapping = client.post(
        "/api/v1/controlling/mappings/confirm",
        json={
            **scope,
            "profile_digest": "a" * 64,
            "column_mapping": {"amount": "amount"},
        },
    )
    run = client.post(
        "/api/v1/controlling/runs",
        json={
            **scope,
            "mapping_confirmation_digest": "b" * 64,
            "statistics_enabled": False,
            "explanations_enabled": True,
            "idempotency_key": "run-a",
        },
    )
    findings = client.get("/api/v1/controlling/findings", query_string=scope)
    disposition = client.post(
        "/api/v1/controlling/findings/finding-a/disposition",
        json={
            **scope,
            "disposition": "confirmed",
            "expected_revision": 0,
        },
    )
    export = client.post("/api/v1/controlling/exports", json=scope)

    assert status.status_code == 200
    assert profile.status_code == 201
    assert mapping.status_code == 201
    assert run.status_code == 202
    assert findings.get_json()["findings"][0]["kind"] == "deterministic_violation"
    assert disposition.get_json()["finding"]["disposition"] == "confirmed"
    assert export.get_json()["report"]["content_redacted"] is True


def test_api_fails_closed_without_server_side_workbench(monkeypatch) -> None:
    monkeypatch.setattr("agent.auth.resolve_configured_agent_token", lambda: None)
    app = Flask(__name__)
    app.register_blueprint(business_controlling_bp)

    response = app.test_client().get(
        "/api/v1/controlling/status",
        query_string={"tenant_id": "tenant-a", "project_id": "project-a"},
    )

    assert response.status_code == 503
    assert response.get_json()["reason_code"] == "controlling_workbench_unavailable"
