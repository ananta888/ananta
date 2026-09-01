from __future__ import annotations

import json
from pathlib import Path

from agent.cli.commands import security as security_cli
from agent.composite_risk_review_contract import COMPOSITE_RISK_REVIEW_WARNING
from agent.config import Settings
from agent.services.composite_risk_review_service import (
    CompositeRiskReviewService,
    RiskIndicator,
)


def test_feature_defaults_off_and_warning_cannot_be_weakened() -> None:
    configured = Settings(_env_file=None)
    assert configured.composite_risk_review_enabled is False
    assert configured.composite_risk_review_explicit_only is True
    assert configured.composite_risk_review_warning_text == COMPOSITE_RISK_REVIEW_WARNING


def test_warning_contract_is_visible_in_docs_and_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    assert COMPOSITE_RISK_REVIEW_WARNING in (
        root / "docs/security/composite-risk-review.md"
    ).read_text(encoding="utf-8").replace("\n> ", " ").replace("> ", "")
    assert COMPOSITE_RISK_REVIEW_WARNING in (
        root / "frontend-angular/src/app/services/composite-risk-review-api.service.ts"
    ).read_text(encoding="utf-8")


def test_empty_review_is_insufficient_context_not_a_safety_decision() -> None:
    result = CompositeRiskReviewService().review()
    assert result["risk_level"] == "insufficient_context"
    assert result["warning_text"] == COMPOSITE_RISK_REVIEW_WARNING
    assert result["review_only"] is True
    assert "allowed" not in result
    assert "denied" not in result


def test_explainable_rules_return_bounded_evidence() -> None:
    result = CompositeRiskReviewService().review(
        goal="Prepare auth API payload and network deployment",
        tasks=[
            {"id": "task-1", "scope": "auth", "title": "credential auth"},
            {"id": "task-2", "scope": "auth", "title": "network API payload"},
            {"id": "task-3", "scope": "release", "title": "assemble deployment bundle"},
        ],
        artifacts_metadata=[
            {"id": "a1", "path": "security/auth_policy.py"},
            {"id": "a2", "path": "network/oauth_token.py"},
            {"id": "a3", "path": "deploy/credential_secret.yaml"},
        ],
    )
    indicators = {item["id"]: item for item in result["indicators"]}
    assert result["risk_level"] == "high"
    assert "many_security_relevant_files" in indicators
    assert "auth_network_payload_deploy_chain" in indicators
    assert "sudden_scope_shift" in indicators
    assert "final_assembly_after_many_artifacts" in indicators
    assert all(item["matched_evidence"] for item in indicators.values())
    assert result["recommended_action"] == "automated_policy_escalation"
    assert result["warning_text"] == COMPOSITE_RISK_REVIEW_WARNING


def test_no_indicator_means_low_hint_not_safe() -> None:
    result = CompositeRiskReviewService().review(goal="Update spelling in documentation")
    assert result["risk_level"] == "low"
    assert result["indicators"] == []
    assert "keine Sicherheitsfreigabe" in result["explanation"]


def test_rules_are_injectable_without_changing_review_orchestration() -> None:
    def project_rule(_context):
        return RiskIndicator(
            id="project_specific_indicator",
            description="Injected project rule",
            severity="medium",
            matched_evidence=({"task_ref": "task-1"},),
        )

    result = CompositeRiskReviewService(rules=[project_rule]).review(goal="review")
    assert [item["id"] for item in result["indicators"]] == ["project_specific_indicator"]


def test_api_is_disabled_by_default_and_always_returns_warning(
    client,
    admin_auth_header,
    app,
) -> None:
    app.config["COMPOSITE_RISK_REVIEW_ENABLED"] = False
    response = client.post(
        "/api/security/composite-risk-review",
        json={"explicit_request": True, "goal": "review"},
        headers=admin_auth_header,
    )
    assert response.status_code == 409
    payload = response.get_json()
    assert payload["data"]["warning_text"] == COMPOSITE_RISK_REVIEW_WARNING


def test_api_runs_explicitly_and_headlessly(client, admin_auth_header, app) -> None:
    app.config["COMPOSITE_RISK_REVIEW_ENABLED"] = True
    app.config["COMPOSITE_RISK_REVIEW_EXPLICIT_ONLY"] = True
    missing_marker = client.post(
        "/api/security/composite-risk-review",
        json={"goal": "review"},
        headers=admin_auth_header,
    )
    assert missing_marker.status_code == 422
    assert missing_marker.get_json()["data"]["warning_text"] == COMPOSITE_RISK_REVIEW_WARNING

    invalid = client.post(
        "/api/security/composite-risk-review",
        json={"explicit_request": True, "unexpected": "value"},
        headers=admin_auth_header,
    )
    assert invalid.status_code == 422
    assert invalid.get_json()["data"]["warning_text"] == COMPOSITE_RISK_REVIEW_WARNING

    response = client.post(
        "/api/security/composite-risk-review",
        json={"explicit_request": True, "goal": "review", "tasks": []},
        headers=admin_auth_header,
    )
    assert response.status_code == 200
    result = response.get_json()["data"]
    assert result["review_only"] is True
    assert result["warning_text"] == COMPOSITE_RISK_REVIEW_WARNING


def test_cli_posts_explicit_payload_without_prompting(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    input_path = tmp_path / "review.json"
    input_path.write_text(json.dumps({"goal": "review"}), encoding="utf-8")
    calls = []

    class FakeClient:
        def post(self, path, *, json):
            calls.append((path, json))
            return {
                "status": "ok",
                "data": {
                    "risk_level": "low",
                    "warning_text": COMPOSITE_RISK_REVIEW_WARNING,
                },
            }

    monkeypatch.setattr(security_cli, "AnantaApiClient", FakeClient)
    assert security_cli.dispatch(["composite-risk-review", "--input", str(input_path)]) == 0
    assert calls == [
        (
            "/api/security/composite-risk-review",
            {"goal": "review", "explicit_request": True},
        )
    ]
    output = capsys.readouterr().out
    assert COMPOSITE_RISK_REVIEW_WARNING in output
    assert "keine Sicherheitsfreigabe" in output
