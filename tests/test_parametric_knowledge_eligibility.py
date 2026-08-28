from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.services.parametric_knowledge_eligibility_policy import ParametricKnowledgeEligibilityPolicy
from ananta_contracts.parametric_knowledge import ParametricKnowledgeUnit

ROOT = Path(__file__).resolve().parents[1]


def _unit(**overrides):
    payload = {
        "schema": "ananta.parametric-knowledge-unit.v1",
        "unit_id": "unit-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "repository_id": "repo-1",
        "source_id": "SRC_0001",
        "source_revision": "rev-1",
        "content_hash": "a" * 64,
        "provenance_digest": "b" * 64,
        "domain": "payments",
        "parent_id": "",
        "relations": [],
        "sensitivity": "public",
        "retention_until": "2099-01-01T00:00:00Z",
        "license_spdx": "MIT",
        "citation_ref": "citation-1",
        "citation_required": False,
        "stable": True,
        "approval_state": "approved",
        "revoked": False,
    }
    payload.update(overrides)
    return ParametricKnowledgeUnit.from_mapping(payload)


def _policy():
    raw = json.loads((ROOT / "config/policies/parametric-knowledge-eligibility.v1.json").read_text(encoding="utf-8"))
    return ParametricKnowledgeEligibilityPolicy(raw, clock=lambda: datetime(2026, 8, 27, tzinfo=UTC))


def test_policy_allows_only_stable_approved_bound_knowledge():
    decision = _policy().evaluate(_unit(), tenant_id="tenant-1", workspace_id="workspace-1", repository_id="repo-1")
    assert decision.decision == "allow"
    assert decision.reason_codes == ("eligible_stable_approved_knowledge",)


def test_policy_denies_secret_cross_tenant_citation_and_revoked_sources():
    decision = _policy().evaluate(
        _unit(sensitivity="secret", citation_required=True, revoked=True),
        tenant_id="other-tenant",
        workspace_id="workspace-1",
        repository_id="repo-1",
    )
    assert decision.decision == "deny"
    assert set(decision.reason_codes) >= {
        "scope_binding_mismatch",
        "source_revoked",
        "sensitivity_secret_denied",
        "citation_required_rag_only",
    }


def test_request_cannot_replace_authoritative_approval():
    decision = _policy().evaluate(
        _unit(approval_state="unreviewed"),
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        repository_id="repo-1",
    )
    assert decision.decision == "require_approval"


def test_policy_config_is_closed_and_cannot_change_fail_closed_modes():
    raw = json.loads((ROOT / "config/policies/parametric-knowledge-eligibility.v1.json").read_text())
    with pytest.raises(ValueError, match="policy_invalid"):
        ParametricKnowledgeEligibilityPolicy({**raw, "request_can_override": True})
    with pytest.raises(ValueError, match="policy_invalid"):
        ParametricKnowledgeEligibilityPolicy({**raw, "unknown_provenance_mode": "allow"})


def test_policy_denies_when_clock_has_no_timezone() -> None:
    raw = json.loads((ROOT / "config/policies/parametric-knowledge-eligibility.v1.json").read_text())
    policy = ParametricKnowledgeEligibilityPolicy(
        raw,
        clock=lambda: datetime(2026, 8, 27),
    )

    decision = policy.evaluate(
        _unit(),
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        repository_id="repo-1",
    )

    assert decision.decision == "deny"
    assert decision.reason_codes == ("retention_expired",)
