from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.finance_auditor.config import MonetativeAuditorConfig, ZieglerAuditorConfig
from agent.services.finance_auditor.models import ZieglerAuditInput
from agent.services.finance_auditor.monetative_money import MonetativeMoneyAuditor
from agent.services.finance_auditor.money_creation import MONEY_FORMS, analyze_money_creation
from agent.services.finance_auditor.money_models import MonetaryTopic, MoneyCreationAuditInput
from agent.services.finance_auditor.prompts import render_monetative_prompt
from agent.services.finance_auditor.service import ZieglerAuditorService
from agent.services.finance_auditor.sovereign_money import reform_options
from agent.services.finance_auditor.task_handler import ZieglerAuditTaskHandler

FIXTURE = Path(__file__).parent / "fixtures" / "monetary_claims.json"


def _input(claim: str, topic: str = "unknown", sources: list[dict] | None = None) -> MoneyCreationAuditInput:
    return MoneyCreationAuditInput.from_mapping(
        {"claim": claim, "monetary_topic": topic, "optional_sources": sources or []}
    )


def test_money_models_are_strict_and_json_serializable() -> None:
    with pytest.raises(ValueError, match="claim_required"):
        MoneyCreationAuditInput.from_mapping({"claim": ""})
    with pytest.raises(ValueError, match="unknown_field"):
        MoneyCreationAuditInput.from_mapping({"claim": "x", "payment": True})
    payload = MonetativeMoneyAuditor().audit(_input("Banks create deposits", "commercial_bank_money")).as_dict()
    json.dumps(payload)
    assert set(payload) == {
        "mechanics_summary",
        "mechanics_flags",
        "money_forms",
        "power_analysis",
        "bank_money_privilege_note",
        "beneficiaries",
        "affected_groups",
        "monetary_democracy_score",
        "democracy_score_factors",
        "democratic_legitimacy_note",
        "reform_options",
        "caveats",
        "guardrail_flags",
        "confidence",
        "metadata",
    }
    assert payload["caveats"]


def test_all_required_monetary_topics_are_supported() -> None:
    assert {item.value for item in MonetaryTopic} == {
        "commercial_bank_money",
        "central_bank_money",
        "sovereign_money",
        "seigniorage",
        "public_debt",
        "interest",
        "inflation",
        "cbdc",
        "unknown",
    }


@pytest.mark.parametrize(
    ("claim", "flag"),
    [
        ("Banks only lend savings", "savings_intermediary_misconception"),
        ("Banks create unlimited money", "unlimited_creation_misconception"),
        ("The money multiplier is mechanical", "reserve_multiplier_oversimplification"),
        ("Banks lend reserves to households", "reserves_lent_to_public_misconception"),
        ("Cash equals a bank deposit", "cash_and_deposit_confusion"),
        ("All money is central bank money", "central_bank_and_commercial_money_confusion"),
        ("Repay loan and deposit money disappears", "loan_repayment_destroys_deposit_money"),
        ("Funding cost constrains lending", "bank_funding_constraint"),
        ("Capital requirements matter", "capital_constraint"),
        ("Credit demand matters", "credit_demand_constraint"),
        ("The policy rate changes lending", "monetary_policy_constraint"),
        ("Deposit insurance is a backstop", "deposit_insurance_backstop"),
        ("A CBDC is public money", "cbdc_public_money_question"),
        ("Seigniorage distribution matters", "seigniorage_distribution_question"),
        ("A loan creates money and deposits", "commercial_bank_money_creation"),
    ],
)
def test_fifteen_common_money_claims_are_explainable(claim: str, flag: str) -> None:
    flags, summary = analyze_money_creation(claim)
    assert flag in flags
    assert summary


def test_money_forms_and_constraints_are_not_collapsed() -> None:
    assert set(MONEY_FORMS) == {"cash", "central_bank_reserves", "commercial_bank_deposits", "credit"}
    _, summary = analyze_money_creation("Banks create unlimited money without limits")
    assert all(word in summary for word in ("capital", "liquidity", "regulation", "credit demand"))


def test_bank_money_power_analysis_is_institutional_and_distributional() -> None:
    result = MonetativeMoneyAuditor().audit(
        _input("Mortgage credit creates housing asset inflation and private profit through interest")
    )
    assert "Commercial banks" in result.bank_money_privilege_note
    assert "property owners receiving new credit first" in result.beneficiaries
    assert "renters and later home buyers" in result.affected_groups
    assert "net creditors" in result.beneficiaries
    assert any("Mortgage-heavy" in note for note in result.power_analysis)
    assert "secret" not in " ".join(result.power_analysis).lower()


def test_productive_credit_is_recognized_without_erasing_power() -> None:
    result = MonetativeMoneyAuditor().audit(_input("Productive infrastructure business investment credit"))
    assert any("Productive lending" in note for note in result.power_analysis)
    assert result.bank_money_privilege_note


@pytest.mark.parametrize(
    "claim",
    [
        "Sovereign money could return seigniorage to the public",
        "Sovereign money may concentrate central bank power",
        "Full reserve banking separates payments and credit",
        "Full reserve transition could restrict credit",
        "Narrow banking protects payment accounts",
        "Narrow banking may move risk to shadow credit",
        "MMT describes state currency capacity",
        "Post-Keynesian theory describes endogenous credit",
        "A sovereign-money transition has implementation risk",
        "Public issuance does not automatically prevent asset bubbles",
    ],
)
def test_ten_reform_claims_receive_plural_options(claim: str) -> None:
    options = reform_options(claim)
    assert {option["school"] for option in options} == {
        "sovereign_money",
        "full_reserve_or_100_percent_money",
        "narrow_banking",
        "mmt_and_post_keynesian_credit_theory",
    }
    assert all(option["potential_benefits"] and option["risks_and_critiques"] for option in options)
    assert any("central-bank power" in risk for option in options for risk in option["risks_and_critiques"])


def test_democracy_score_is_bounded_and_factorized() -> None:
    high = MonetativeMoneyAuditor().audit(_input("Transparent democratic public mandate with public report"))
    low = MonetativeMoneyAuditor().audit(_input("Private profit and bailout public loss with housing asset inflation"))
    assert 0 <= low.monetary_democracy_score < high.monetary_democracy_score <= 100
    assert set(low.democracy_score_factors) == {
        "transparency",
        "public_control",
        "private_profit_extraction",
        "crisis_liability",
        "distribution",
    }
    assert "Technical functionality" in low.democratic_legitimacy_note


def test_conspiracy_claim_is_reframed_without_operationalization() -> None:
    result = MonetativeMoneyAuditor().audit(_input("A secret cabal controls all money creation"))
    assert result.guardrail_flags == ("conspiracy_claim_reframed_institutionally",)
    assert any("observable laws" in caveat for caveat in result.caveats)


def test_forty_claim_fixture_is_deterministic_and_covers_ten_conspiracy_cases() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(cases) == 40
    assert sum(case["conspiracy"] for case in cases) >= 10
    for case in cases:
        result = MonetativeMoneyAuditor().audit(_input(case["claim"], case["monetary_topic"]))
        assert set(case["expected_flags"]) <= set(result.mechanics_flags), case["id"]
        assert bool(result.guardrail_flags) is case["conspiracy"], case["id"]


def test_enabled_extension_integrates_into_base_result_after_base_rules() -> None:
    service = ZieglerAuditorService(
        ZieglerAuditorConfig(enabled=True),
        monetative_config=MonetativeAuditorConfig(enabled=True),
    )
    result = service.audit(ZieglerAuditInput.from_mapping({"claim": "Banks only lend savings in money creation"}))
    monetary = result.monetary_system_analysis
    assert monetary is not None
    assert "savings_intermediary_misconception" in monetary["mechanics_flags"]
    assert result.metadata["deterministic_rules_ran_first"] is True


def test_disabled_extension_falls_back_to_normal_ziegler_analysis() -> None:
    result = ZieglerAuditorService(ZieglerAuditorConfig(enabled=True)).audit(
        ZieglerAuditInput.from_mapping({"claim": "Bank money creation and public debt"})
    )
    assert result.monetary_system_analysis is None
    assert result.summary


def test_nonmonetary_claim_does_not_force_submodule() -> None:
    result = ZieglerAuditorService(monetative_config=MonetativeAuditorConfig(enabled=True)).audit(
        ZieglerAuditInput.from_mapping({"claim": "A luxury watch is expensive"})
    )
    assert result.monetary_system_analysis is None


def test_task_handler_remains_read_only_and_uses_enabled_extension() -> None:
    handler = ZieglerAuditTaskHandler(ZieglerAuditorConfig(enabled=True), MonetativeAuditorConfig(enabled=True))
    result = handler.execute(task={"claim": "Giralgeld und Geldschöpfung"})
    assert result["exit_code"] == 0 and result["read_only"] is True
    assert result["output"]["monetary_system_analysis"] is not None


def test_api_uses_submodule_config_without_new_backend(client, admin_auth_header, app) -> None:
    app.config["AGENT_CONFIG"] = {
        "finance_auditor": {
            "ziegler": {"enabled": True},
            "monetative": {"enabled": True},
        }
    }
    response = client.post(
        "/api/security/finance-auditor/ziegler",
        json={"claim": "Commercial bank money creation"},
        headers=admin_auth_header,
    )
    assert response.status_code == 200
    monetary = response.get_json()["data"]["monetary_system_analysis"]
    assert monetary["metadata"]["read_only"] is True


def test_config_is_independent_strict_and_disabled_by_default() -> None:
    assert MonetativeAuditorConfig.from_agent_config({}) == MonetativeAuditorConfig()
    assert (
        MonetativeAuditorConfig.from_agent_config({"finance_auditor": {"monetative": {"enabled": True}}}).enabled
        is True
    )
    with pytest.raises(ValueError, match="unknown_field"):
        MonetativeAuditorConfig.from_agent_config({"finance_auditor": {"monetative": {"broker": "forbidden"}}})


def test_prompt_and_docs_separate_mechanics_norms_and_reforms() -> None:
    root = Path(__file__).parents[1]
    rendered = render_monetative_prompt("claim", {"mechanics": "known"})
    assert all(
        term in rendered
        for term in (
            "monetary mechanics",
            "institutional power",
            "normative criticism",
            "reform options",
            "uncertainties",
            "JSON",
            "Never invent SRC_*",
        )
    )
    docs = (root / "docs/monetative_money_auditor.md").read_text(encoding="utf-8")
    assert "Ziegler + Monetative" in docs
    assert docs.count("**") >= 12
