"""Institutional money-power analysis with anti-conspiracy guardrails."""

from __future__ import annotations

from agent.services.finance_auditor.money_creation import MONEY_FORMS, analyze_money_creation
from agent.services.finance_auditor.money_models import MoneyCreationAuditInput, MoneyCreationAuditResult
from agent.services.finance_auditor.source_quality import assess_sources
from agent.services.finance_auditor.sovereign_money import reform_options

_CONSPIRACY_TERMS = (
    "secret cabal",
    "single hidden group",
    "secretly controls all",
    "geheime elite kontrolliert alles",
    "weltverschwörung",
    "world conspiracy",
)


class MonetativeMoneyAuditor:
    def audit(self, audit_input: MoneyCreationAuditInput) -> MoneyCreationAuditResult:
        if not isinstance(audit_input, MoneyCreationAuditInput):
            raise TypeError("money_audit_input_invalid")
        text = " ".join(part for part in (audit_input.claim, audit_input.context) if part)
        lower = text.lower()
        mechanics_flags, mechanics_summary = analyze_money_creation(text)
        sources = assess_sources(audit_input.optional_sources)
        conspiracy = any(term in lower for term in _CONSPIRACY_TERMS)
        factors = self._democracy_factors(lower)
        score = max(0, min(100, 50 + sum(factors.values())))
        beneficiaries, affected = self._actors(lower)
        caveats = list(sources.evidence_notes)
        if conspiracy:
            caveats.append(
                "The conspiracy framing is unsupported and was replaced by analysis of "
                "observable laws, incentives and institutions."
            )
        caveats.append(
            "Reform effects depend on institutional design, transition rules and "
            "empirical conditions; no option is an automatic cure."
        )
        return MoneyCreationAuditResult(
            mechanics_summary=mechanics_summary,
            mechanics_flags=mechanics_flags,
            money_forms=dict(MONEY_FORMS),
            power_analysis=self._power_analysis(lower),
            bank_money_privilege_note=(
                "Commercial banks can create widely accepted deposit money through lending "
                "within a public legal, regulatory and backstop framework. That institutional "
                "privilege raises transparent questions about credit allocation, private "
                "returns and public crisis exposure."
            ),
            beneficiaries=beneficiaries,
            affected_groups=affected,
            monetary_democracy_score=score,
            democracy_score_factors=factors,
            democratic_legitimacy_note=(
                "Technical functionality does not establish democratic legitimacy; assess "
                "transparency, mandate, accountability, distribution and who bears crises."
            ),
            reform_options=reform_options(text),
            caveats=tuple(caveats),
            guardrail_flags=("conspiracy_claim_reframed_institutionally",) if conspiracy else (),
            confidence=sources.confidence,
            metadata={
                "read_only": True,
                "monetary_topic": audit_input.monetary_topic.value,
                "investment_advice": False,
            },
        )

    @staticmethod
    def _democracy_factors(lower: str) -> dict[str, int]:
        return {
            "transparency": 10 if any(x in lower for x in ("transparent", "public report", "offenleg")) else -5,
            "public_control": 10
            if any(x in lower for x in ("public mandate", "parliament", "democratic", "öffentlich"))
            else -5,
            "private_profit_extraction": -10
            if any(x in lower for x in ("private profit", "interest extraction", "zinsabschöpf"))
            else 0,
            "crisis_liability": -10
            if any(x in lower for x in ("bailout", "public loss", "rettung", "sozialisierte verluste"))
            else 0,
            "distribution": -10
            if any(x in lower for x in ("asset inflation", "housing bubble", "inequality", "immobilienblase"))
            else 0,
        }

    @staticmethod
    def _actors(lower: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        beneficiaries = ["commercial banks and credit recipients"]
        affected = ["deposit users and the risk-bearing public"]
        if any(x in lower for x in ("housing", "mortgage", "immobil")):
            beneficiaries.append("property owners receiving new credit first")
            affected.append("renters and later home buyers")
        if "interest" in lower or "zins" in lower:
            beneficiaries.append("net creditors")
            affected.append("net debtors")
        return tuple(beneficiaries), tuple(affected)

    @staticmethod
    def _power_analysis(lower: str) -> tuple[str, ...]:
        notes = [
            "Credit allocation influences which activities and asset markets receive newly created purchasing power."
        ]
        if any(x in lower for x in ("housing", "mortgage", "asset inflation", "immobil")):
            notes.append("Mortgage-heavy credit expansion can amplify property prices and distributional inequality.")
        if any(x in lower for x in ("productive", "business investment", "infrastructure", "produktiv")):
            notes.append(
                "Productive lending can finance capacity and public value; purpose and "
                "outcomes matter alongside the money mechanism."
            )
        return tuple(notes)
