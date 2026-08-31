from __future__ import annotations

from collections.abc import Mapping

from flask import Flask

from agent.routes.business_controlling import business_controlling_bp
from agent.services.business_controlling_runtime_control import (
    BusinessControllingRuntimeControlService,
    JsonBusinessControllingRuntimeControlRepository,
)
from agent.services.business_controlling_workbench_service import (
    BusinessControllingWorkbenchService,
    JsonBusinessControllingFindingStore,
)


class _Imports:
    def profile_import(self, **kwargs: object) -> Mapping[str, object]:
        payload = kwargs["request_payload"]
        assert isinstance(payload, Mapping)
        return {
            "profile_digest": "a" * 64,
            "source_revision_id": payload["source_revision_id"],
            "row_count": 2,
            "columns": [{"header": "amount", "inferred_type": "decimal"}],
        }

    def confirm_mapping(self, **kwargs: object) -> Mapping[str, object]:
        payload = kwargs["request_payload"]
        assert isinstance(payload, Mapping)
        return {
            "profile_digest": payload["profile_digest"],
            "confirmation_digest": "b" * 64,
            "column_mapping": payload["column_mapping"],
        }


class _Analysis:
    def execute(self, **kwargs: object) -> Mapping[str, object]:
        assert kwargs["statistics_enabled"] is False
        return {
            "run_id": "rules-run-a",
            "status": "completed",
            "finding_count": 1,
            "findings": [
                {
                    "finding_id": "finding-a",
                    "kind": "deterministic_violation",
                    "severity": "high",
                    "dataset_version": "dataset-version-a",
                    "rule_version": "rules-v1",
                    "confidence": None,
                    "evidence_digest": "c" * 64,
                    "disposition": "open",
                    "revision": 0,
                }
            ],
        }


def test_automated_rules_only_api_flow_uses_durable_hub_service(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("agent.auth.resolve_configured_agent_token", lambda: None)
    runtime = JsonBusinessControllingRuntimeControlRepository(tmp_path / "runtime.json")
    BusinessControllingRuntimeControlService(runtime).replace(
        expected_revision=0,
        global_enabled=True,
        statistical_enabled=False,
        explanations_enabled=True,
        disabled_catalog_entry_ids=(),
        actor_id="e2e-automation",
        reason="automated-rules-only",
    )
    service = BusinessControllingWorkbenchService(
        runtime_control=runtime,
        imports=_Imports(),
        analysis=_Analysis(),
        findings=JsonBusinessControllingFindingStore(tmp_path / "findings.json"),
    )
    app = Flask(__name__)
    app.register_blueprint(business_controlling_bp)
    app.extensions["business_controlling_workbench"] = service
    client = app.test_client()
    scope = {"tenant_id": "tenant-a", "project_id": "project-a"}

    assert client.post(
        "/api/v1/controlling/imports/profile",
        json={
            **scope,
            "source_revision_id": "source-revision-a",
            "revision_digest": "1" * 64,
            "source_format": "csv",
        },
    ).status_code == 201
    assert client.post(
        "/api/v1/controlling/mappings/confirm",
        json={
            **scope,
            "profile_digest": "a" * 64,
            "column_mapping": {"amount": "amount"},
        },
    ).status_code == 201
    assert client.post(
        "/api/v1/controlling/runs",
        json={
            **scope,
            "mapping_confirmation_digest": "b" * 64,
            "statistics_enabled": False,
            "explanations_enabled": True,
            "idempotency_key": "rules-run-a",
        },
    ).status_code == 202
    findings = client.get(
        "/api/v1/controlling/findings",
        query_string=scope,
    ).get_json()["findings"]

    assert findings[0]["kind"] == "deterministic_violation"
    assert findings[0]["disposition"] == "open"
