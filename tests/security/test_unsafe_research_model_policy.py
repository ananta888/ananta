from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.services.model_policy_service import ResearchModelPolicyService, ResearchModelRunRequest

ROOT = Path(__file__).resolve().parents[2]


def request(now: datetime) -> ResearchModelRunRequest:
    return ResearchModelRunRequest(
        trust_class="unsafe_research",
        safety_modified=True,
        environment="local_isolated_test",
        route="explicit_research_run",
        model_revision="6386362d3cc8dbcb895c485a772869bfac5352dc",
        runtime_id="llamacpp-pinned",
        authorization_expires_at=(now + timedelta(minutes=10)).isoformat(),
    )


def test_only_bounded_local_text_research_is_admitted() -> None:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    service = ResearchModelPolicyService.from_file(ROOT / "config/security/model-trust-policy.v1.json")

    decision = service.evaluate(request(now), now=now)
    assert decision.allowed is True
    event = decision.audit_event(request(now))
    assert event["event_type"] == "unsafe_research.run_admitted"
    assert event["content_persisted"] is False
    assert "6386362d" not in str(event)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"trust_class": None}, "unsafe_research_identity_missing"),
        ({"environment": "production"}, "unsafe_research_environment_forbidden"),
        ({"route": "automatic_fallback"}, "unsafe_research_route_forbidden"),
        ({"tools_requested": True}, "unsafe_research_capability_forbidden"),
        ({"network_requested": True}, "unsafe_research_capability_forbidden"),
        ({"write_requested": True}, "unsafe_research_capability_forbidden"),
        ({"secrets_present": True}, "unsafe_research_data_forbidden"),
        ({"personal_data_present": True}, "unsafe_research_data_forbidden"),
        ({"authorization_expires_at": "2026-09-03T00:00:00+00:00"}, "unsafe_research_authorization_expired"),
    ],
)
def test_policy_fails_closed(changes, reason) -> None:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    service = ResearchModelPolicyService.from_file(ROOT / "config/security/model-trust-policy.v1.json")

    decision = service.evaluate(replace(request(now), **changes), now=now)

    assert decision.allowed is False
    assert decision.reason_code == reason
