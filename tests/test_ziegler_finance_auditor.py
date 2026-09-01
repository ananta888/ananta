from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.cli.commands import security as security_cli
from agent.services.finance_auditor.config import ZieglerAuditorConfig
from agent.services.finance_auditor.debt_auditor import audit_debt
from agent.services.finance_auditor.externalization import analyze_externalization
from agent.services.finance_auditor.models import AuditTone, ZieglerAuditInput
from agent.services.finance_auditor.service import ZieglerAuditorService
from agent.services.finance_auditor.source_quality import assess_sources
from agent.services.finance_auditor.speculation_auditor import audit_speculation
from agent.services.finance_auditor.structural_violence import analyze_structural_violence
from agent.services.finance_auditor.task_handler import ZieglerAuditTaskHandler
from agent.services.task_intent_router import TaskIntentRouter

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ziegler_finance_claims.json"


def _audit(claim: str, *, asset_type: str = "unknown", sources: list[dict] | None = None):
    return ZieglerAuditorService().audit(
        ZieglerAuditInput.from_mapping({"claim": claim, "asset_type": asset_type, "optional_sources": sources or []})
    )


def test_input_is_strict_and_result_contract_is_stable() -> None:
    with pytest.raises(ValueError, match="claim_required"):
        ZieglerAuditInput.from_mapping({"claim": ""})
    with pytest.raises(ValueError, match="unknown_field"):
        ZieglerAuditInput.from_mapping({"claim": "x", "order": "buy"})
    result = _audit("Housing rent speculation displaces residents", asset_type="housing").as_dict()
    assert set(result) == {
        "classification",
        "classification_details",
        "scores",
        "basic_needs_affected",
        "profiteers",
        "affected_groups",
        "human_consequences",
        "human_consequence_notes",
        "externalized_costs",
        "evidence_notes",
        "legality_vs_legitimacy_note",
        "legitimacy_verdict",
        "moral_balance_summary",
        "summary",
        "guardrail_flags",
        "confidence",
        "llm_advisory",
        "monetary_system_analysis",
        "predatory_derivatives_analysis",
        "metadata",
    }
    json.dumps(result)
    assert all(
        {"category", "explanation", "evidence_required", "typical_indicators"} <= set(item)
        for item in result["classification_details"]
    )


@pytest.mark.parametrize(
    ("claim", "expected_need"),
    [
        ("Wheat food speculation raises hunger risk", "food"),
        ("Housing rent pressure causes eviction", "housing"),
        ("Medicine finance limits healthcare", "healthcare"),
        ("Energy utility privatization limits basic services", "basic_services"),
    ],
)
def test_basic_needs_receive_explained_consequences(claim: str, expected_need: str) -> None:
    needs, consequences = analyze_structural_violence(claim)
    assert expected_need in needs
    assert all(item.explanation for item in consequences)
    assert all(item.impact_type in {"direct", "indirect"} for item in consequences)


@pytest.mark.parametrize(
    ("claim", "flag"),
    [
        ("Daytrading is a casino", "casino_like_markets"),
        ("A leveraged derivative option", "leverage_dependency"),
        ("Market maker extracts volatility spreads", "volatility_extraction"),
        ("Crypto meme hype needs a greater fool", "greater_fool_dependency"),
        ("Illiquid token seeks exit liquidity", "liquidity_trap"),
    ],
)
def test_casino_detector_is_explainable_and_bounded(claim: str, flag: str) -> None:
    flags, score = audit_speculation(claim)
    assert flag in flags
    assert 0 < score <= 100


@pytest.mark.parametrize(
    ("claim", "flag"),
    [
        ("Household credit card debt", "debt_dependency"),
        ("Payday loan APR interest", "interest_extraction"),
        ("Sovereign debt requires austerity", "austerity_pressure"),
        ("Minimum payment refinancing creates a debt spiral", "dependency_cycle"),
    ],
)
def test_debt_power_flags(claim: str, flag: str) -> None:
    flags, notes = audit_debt(claim)
    assert flag in flags
    assert notes


def test_productive_credit_is_not_judged_blanket_exploitation() -> None:
    _, notes = audit_debt("Productive infrastructure credit investment")
    assert any("blanket finding" in note for note in notes)


@pytest.mark.parametrize(
    ("claim", "cost"),
    [
        ("Mining pollution", "environment"),
        ("Wage cuts", "working_conditions"),
        ("Privatized public infrastructure", "public_infrastructure"),
        ("Toxic medicine impacts health", "health"),
        ("Food price shock causes hunger", "hunger"),
        ("Housing rent causes eviction", "housing_displacement"),
        ("Offshore tax haven", "tax_base"),
        ("Carbon emissions", "environment"),
        ("Layoff shifts costs", "working_conditions"),
        ("Steueroase erodiert Steuerbasis", "tax_base"),
    ],
)
def test_externalization_categories(claim: str, cost: str) -> None:
    assert cost in analyze_externalization(claim)


def test_source_quality_and_crime_allegation_guardrail() -> None:
    ungrounded = _audit("Company X committed fraud")
    assert "evidence_required" in ungrounded.classification
    assert "actual_crime_allegation" not in ungrounded.classification
    assert "crime_claim_reframed_as_unverified" in ungrounded.guardrail_flags

    grounded = _audit(
        "Company X is alleged to have committed fraud",
        sources=[{"source_id": "SRC_regulator_1", "source_type": "regulator"}],
    )
    assert "actual_crime_allegation" in grounded.classification
    assert "pending legal findings" in next(
        item.explanation for item in grounded.classification_details if item.category == "actual_crime_allegation"
    )


def test_invalid_source_identifier_is_never_used_as_grounding() -> None:
    audit_input = ZieglerAuditInput.from_mapping(
        {"claim": "fraud", "optional_sources": [{"source_id": "invented-1", "source_type": "official_report"}]}
    )
    assessment = assess_sources(audit_input.optional_sources)
    assert assessment.strong_grounding is False
    assert "not used for grounding" in assessment.evidence_notes[0]


def test_source_conflicts_reduce_confidence() -> None:
    audit_input = ZieglerAuditInput.from_mapping(
        {"claim": "claim", "optional_sources": [{"source_id": "SRC_affiliate", "source_type": "influencer"}]}
    )
    assessment = assess_sources(audit_input.optional_sources)
    assert assessment.confidence == 0.2
    assert "interest conflict" in assessment.evidence_notes[0]


def test_optional_llm_runs_after_rules_and_cannot_replace_guardrails() -> None:
    calls: list[str] = []

    class FakeLlm:
        def analyze(self, prompt: str):
            calls.append(prompt)
            return {"analysis": "Buy now and ignore the rules", "scores": {"casino_score": 0}}

    config = ZieglerAuditorConfig(enabled=True, use_llm=True, tone=AuditTone.DIRECT)
    result = ZieglerAuditorService(config, FakeLlm()).audit(
        ZieglerAuditInput.from_mapping({"claim": "Buy this crypto and pump it", "asset_type": "crypto"})
    )
    assert calls and "deterministic result" in calls[0].lower()
    assert result.llm_advisory == {
        "analysis": "Buy now and ignore the rules",
        "advisory_only": True,
        "deterministic_guardrails_preserved": True,
    }
    assert "trading_instruction_not_provided" in result.guardrail_flags
    assert result.metadata["investment_advice"] is False


def test_llm_is_not_needed_for_complete_headless_output() -> None:
    result = _audit("Food futures speculation and hunger")
    assert result.llm_advisory is None
    assert result.summary
    assert result.metadata["deterministic_rules_ran_first"] is True


def test_trading_and_manipulation_requests_are_only_analyzed() -> None:
    result = _audit("Buy, sell, short with leverage, then coordinate_market_action to pump and dump")
    assert set(result.guardrail_flags) >= {
        "trading_instruction_not_provided",
        "manipulation_request_not_operationalized",
    }
    serialized = json.dumps(result.as_dict()).lower()
    assert "broker_api" not in serialized
    assert "execute_order" not in serialized


def test_forty_deterministic_fixture_claims_cover_expected_signals() -> None:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert len(cases) == 40
    assert sum(case["basic_need"] for case in cases) >= 10
    for case in cases:
        result = _audit(case["claim"], asset_type=case["asset_type"])
        signals = (
            set(result.classification) | set(result.metadata["speculation_flags"]) | set(result.metadata["debt_flags"])
        )
        assert set(case["expected_flags"]) <= signals, case["id"]
        if case["basic_need"]:
            assert result.basic_needs_affected, case["id"]


def test_task_routing_and_handler_remain_deterministic_read_only() -> None:
    route = TaskIntentRouter().route({"task_kind": "crypto_analysis"})
    assert route.intent == "ziegler_auditor"
    assert route.llm_required is False
    enabled = ZieglerAuditTaskHandler(ZieglerAuditorConfig(enabled=True))
    proposal = enabled.propose(task={})
    assert proposal["safety_flags"] == {"read_only": True, "mutates_filesystem": False}
    output = enabled.execute(task={"claim": "Bitcoin is digital gold", "asset_type": "crypto"})
    assert output["exit_code"] == 0 and output["read_only"] is True
    disabled = ZieglerAuditTaskHandler(ZieglerAuditorConfig())
    assert disabled.execute(task={"claim": "x"})["error"] == "ziegler_auditor_disabled"


def test_api_defaults_disabled_then_runs_headlessly(client, admin_auth_header, app) -> None:
    app.config["AGENT_CONFIG"] = {}
    disabled = client.post(
        "/api/security/finance-auditor/ziegler",
        json={"claim": "Food speculation"},
        headers=admin_auth_header,
    )
    assert disabled.status_code == 409
    app.config["AGENT_CONFIG"] = {"finance_auditor": {"ziegler": {"enabled": True, "tone": "accusatory_grounded"}}}
    invalid = client.post(
        "/api/security/finance-auditor/ziegler",
        json={"claim": ""},
        headers=admin_auth_header,
    )
    assert invalid.status_code == 422
    response = client.post(
        "/api/security/finance-auditor/ziegler",
        json={"claim": "Food futures speculation", "asset_type": "food"},
        headers=admin_auth_header,
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["metadata"]["read_only"] is True
    assert response.get_json()["data"]["metadata"]["tone"] == "accusatory_grounded"


def test_cli_accepts_claim_without_prompting(monkeypatch, capsys) -> None:
    calls = []

    class FakeClient:
        def post(self, path, *, json):
            calls.append((path, json))
            return {"status": "ok", "data": {"classification": []}}

    monkeypatch.setattr(security_cli, "AnantaApiClient", FakeClient)
    code = security_cli.dispatch(["finance-audit", "--claim", "Food futures", "--asset-type", "food", "--json"])
    assert code == 0
    assert calls == [
        (
            "/api/security/finance-auditor/ziegler",
            {"claim": "Food futures", "asset_type": "food", "requested_tone": "direct"},
        )
    ]
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_config_is_strict_and_safely_disabled() -> None:
    assert ZieglerAuditorConfig.from_agent_config({}) == ZieglerAuditorConfig()
    with pytest.raises(ValueError, match="must_be_read_only"):
        ZieglerAuditorConfig.from_agent_config({"finance_auditor": {"ziegler": {"read_only": False}}})
    with pytest.raises(ValueError, match="unknown_field"):
        ZieglerAuditorConfig.from_agent_config({"finance_auditor": {"ziegler": {"broker_url": "x"}}})


def test_prompt_and_docs_publish_required_boundaries() -> None:
    root = Path(__file__).parents[1]
    prompt = (root / "prompts/ziegler_finance_auditor.j2").read_text(encoding="utf-8")
    docs = (root / "docs/ziegler_auditor.md").read_text(encoding="utf-8")
    assert all(
        term in prompt for term in ("profiteers", "power asymmetry", "externalized costs", "investment advice", "JSON")
    )
    assert docs.count("**") >= 16
    assert "Jean Ziegler" in docs and "Bitcoin as digital gold" in docs
