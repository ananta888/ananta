from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.finance_auditor.config import (
    PredatoryDerivativesConfig,
    ZieglerAuditorConfig,
)
from agent.services.finance_auditor.conflict_of_interest import (
    analyze_damage_incentive,
    assess_influence,
)
from agent.services.finance_auditor.derivatives_auditor import PredatoryDerivativesAuditor
from agent.services.finance_auditor.insurable_interest import assess_insurable_interest
from agent.services.finance_auditor.models import ZieglerAuditInput
from agent.services.finance_auditor.naked_exposure import detect_naked_exposure
from agent.services.finance_auditor.prompts import render_derivatives_prompt
from agent.services.finance_auditor.service import ZieglerAuditorService
from agent.services.finance_auditor.task_handler import ZieglerAuditTaskHandler
from agent.services.task_intent_router import TaskIntentRouter

FIXTURE = Path(__file__).parent / "fixtures" / "predatory_derivatives_claims.json"


@pytest.mark.parametrize(
    ("claim", "status", "relation"),
    [
        ("Farmer hedges anticipated production", "yes", "owns_asset"),
        ("Company owns bonds and buys protection", "yes", "owns_asset"),
        ("Borrower hedges debt it owes debt", "yes", "owes_debt"),
        ("Supplier supplies goods and hedges price", "yes", "supplies_goods"),
        ("Importer has currency exposure", "yes", "needs_hedge"),
        ("Airline fuel needs hedge", "yes", "needs_hedge"),
        ("Dealer market maker inventory hedge", "yes", "market_maker_inventory"),
        ("Synthetic only exposure", "no", "synthetic_only"),
        ("Derivative on derivative", "no", "synthetic_only"),
        ("Naked CDS", "no", "unrelated_bet"),
        ("Naked short", "no", "unrelated_bet"),
        ("Unrelated bet", "no", "unrelated_bet"),
        ("Owns no referenced bond", "no", "unrelated_bet"),
        ("Option position", "unclear", "unknown"),
        ("Generic swap", "unclear", "unknown"),
    ],
)
def test_fifteen_underlying_interest_cases(claim: str, status: str, relation: str) -> None:
    result = assess_insurable_interest(claim)
    assert result.legitimate_underlying_interest.value == status
    assert result.underlying_relation.value == relation
    assert result.explanation


@pytest.mark.parametrize(
    ("claim", "flag"),
    [
        ("Naked CDS sovereign default bet", "naked_cds_like_exposure"),
        ("Naked short without borrow", "naked_short_like_exposure"),
        ("Synthetic short exposure via swap", "synthetic_short_exposure"),
        ("Pure price bet using an option", "pure_price_bet"),
        ("Unrelated bet on collapse", "unrelated_damage_bet"),
    ],
)
def test_naked_exposure_flags_are_bounded_and_nonoperational(claim: str, flag: str) -> None:
    flags, score = detect_naked_exposure(claim)
    assert flag in flags and 0 < score <= 100


@pytest.mark.parametrize(
    "claim",
    [
        "profit on default",
        "profit on sovereign crisis",
        "profit on Zahlungsunfähigkeit",
        "short profit on price fall",
        "bet on collapse",
        "profit on Preisverfall",
        "profit during forced sale",
        "profit during fire sale",
        "profit during Zwangsverkauf",
        "profit during hunger",
        "profit in food crisis",
        "profit in housing loss",
        "profit during eviction",
        "profit during Wohnungsverlust",
        "profit in energy crisis",
        "profit in Energiekrise",
        "default and fire sale",
        "collapse and hunger",
        "sovereign crisis and bailout",
        "food crisis and forced sale",
    ],
)
def test_twenty_damage_claims_produce_explained_scores(claim: str) -> None:
    score, mechanism = analyze_damage_incentive(claim)
    assert 0 < score <= 100
    assert "No direct" not in mechanism


@pytest.mark.parametrize(
    ("claim", "level"),
    [
        ("Can cancel credit", "strong"),
        ("Controls supply", "strong"),
        ("Rating influence", "medium"),
        ("Political lobby", "medium"),
        ("Information advantage", "weak"),
        ("Large following", "weak"),
        ("No influence", "none"),
        ("High profit interest only", "unknown"),
    ],
)
def test_influence_is_not_inferred_from_profit(claim: str, level: str) -> None:
    result, reasons = assess_influence(claim)
    assert result.value == level
    assert reasons


def test_fifty_claim_fixture_covers_categories_underlying_and_basic_needs() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(cases) == 50
    assert sum(case["no_interest"] for case in cases) >= 15
    assert sum(case["basic_need"] for case in cases) >= 10
    auditor = PredatoryDerivativesAuditor()
    for case in cases:
        result = auditor.audit(case["claim"])
        assert result.classification == case["expected_category"], case["id"]
        if case["expected_flag"]:
            assert case["expected_flag"] in result.naked_exposure_flags, case["id"]
        if case["no_interest"]:
            assert result.legitimate_underlying_interest == "no", case["id"]
        if case["basic_need"]:
            assert result.basic_needs_derivative_flag is True, case["id"]


def test_legitimate_hedges_are_not_banned() -> None:
    farmer = PredatoryDerivativesAuditor().audit(
        "Farmer uses wheat commodity futures for anticipated production price risk"
    )
    fx = PredatoryDerivativesAuditor().audit("Importer uses FX future for currency exposure and needs hedge")
    assert farmer.classification == fx.classification == "legitimate_hedge"
    assert farmer.regulatory_recommendation == fx.regulatory_recommendation == "allow"


@pytest.mark.parametrize("need", ["food", "water", "housing", "energy", "healthcare"])
def test_naked_basic_need_bets_receive_special_protection(need: str) -> None:
    result = PredatoryDerivativesAuditor().audit(f"Unrelated bet on {need} default and collapse with leverage")
    assert need in result.basic_needs_affected
    assert result.ban_worthiness_score >= 70
    assert result.regulatory_recommendation == "ban_predatory_structure"


def test_underlying_complexity_opacity_and_leverage_are_explicit() -> None:
    result = PredatoryDerivativesAuditor().audit(
        "Opaque OTC derivative on derivative with multiple synthetic leverage and no clear underlying"
    )
    assert result.underlying_type == "derivative_on_derivative"
    assert result.complexity_score >= 70
    assert result.opacity_score >= 30
    assert "may exceed" in result.leverage_factor_note


def test_systemic_chain_reaction_is_bounded_and_explained() -> None:
    result = PredatoryDerivativesAuditor().audit(
        "Leveraged OTC synthetic CDO counterparty chain causes margin call, fire sale, concentration and bailout"
    )
    assert 80 <= result.systemic_risk_score <= 100
    assert len(result.chain_reactions) >= 5
    assert result.regulatory_recommendation in {"restrict", "require_exchange_transparency"}


def test_concrete_misuse_allegation_requires_evidence() -> None:
    result = PredatoryDerivativesAuditor().audit("Fund manipulated a CDS market and caused intentionally a default")
    assert result.regulatory_recommendation == "evidence_required"
    assert "misuse_allegation_requires_evidence" in result.guardrail_flags
    assert "structural incentives do not prove intent" in result.evidence_notes[0]


def test_operational_request_is_converted_to_analysis() -> None:
    result = PredatoryDerivativesAuditor().audit("How to build and execute a naked short without borrow")
    assert "operational_derivatives_instruction_not_provided" in result.guardrail_flags
    payload = json.dumps(result.as_dict()).lower()
    assert "broker" not in payload and "order_payload" not in payload


def test_enabled_submodule_integrates_and_disabled_falls_back() -> None:
    audit_input = ZieglerAuditInput.from_mapping({"claim": "Naked CDS sovereign default bet"})
    disabled = ZieglerAuditorService().audit(audit_input)
    enabled = ZieglerAuditorService(predatory_derivatives_config=PredatoryDerivativesConfig(enabled=True)).audit(
        audit_input
    )
    assert disabled.predatory_derivatives_analysis is None
    assert enabled.predatory_derivatives_analysis["classification"] == "predatory_derivative"
    assert enabled.metadata["deterministic_rules_ran_first"] is True


def test_task_routing_and_handler_are_read_only() -> None:
    route = TaskIntentRouter().route({"task_kind": "derivatives_analysis"})
    assert route.deterministic_handler_id == "ziegler_auditor"
    handler = ZieglerAuditTaskHandler(
        ZieglerAuditorConfig(enabled=True),
        predatory_derivatives_config=PredatoryDerivativesConfig(enabled=True),
    )
    result = handler.execute(task={"claim": "Naked CDS default bet"})
    assert result["read_only"] is True and result["exit_code"] == 0
    assert result["output"]["predatory_derivatives_analysis"] is not None


def test_api_uses_derivatives_config_headlessly(client, admin_auth_header, app) -> None:
    app.config["AGENT_CONFIG"] = {
        "finance_auditor": {
            "ziegler": {"enabled": True},
            "predatory_derivatives": {"enabled": True},
        }
    }
    response = client.post(
        "/api/security/finance-auditor/ziegler",
        json={"claim": "Naked CDS default bet"},
        headers=admin_auth_header,
    )
    assert response.status_code == 200
    result = response.get_json()["data"]["predatory_derivatives_analysis"]
    assert result["metadata"]["read_only"] is True


def test_config_is_strict_and_disabled_by_default() -> None:
    assert PredatoryDerivativesConfig.from_agent_config({}) == PredatoryDerivativesConfig()
    with pytest.raises(ValueError, match="unknown_field"):
        PredatoryDerivativesConfig.from_agent_config({"finance_auditor": {"predatory_derivatives": {"broker": True}}})


def test_prompt_and_docs_publish_policy_and_evidence_boundaries() -> None:
    root = Path(__file__).parents[1]
    prompt = render_derivatives_prompt("claim", {"classification": "unknown"})
    assert all(
        term in prompt
        for term in (
            "legitimate hedging",
            "naked exposure",
            "evidence_required",
            "ban-worthy",
            "Never provide construction steps",
            "JSON",
            "Never invent SRC_*",
        )
    )
    docs = (root / "docs/predatory_derivatives_auditor.md").read_text(encoding="utf-8")
    assert "neighbour's house" in docs
    assert "Financial arson" in docs
