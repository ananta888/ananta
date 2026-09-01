"""Composition service for deterministic, read-only finance audits."""

from __future__ import annotations

from typing import Any

from agent.services.finance_auditor.config import ZieglerAuditorConfig
from agent.services.finance_auditor.debt_auditor import audit_debt
from agent.services.finance_auditor.externalization import analyze_externalization, moral_balance_summary
from agent.services.finance_auditor.models import ClassificationDetail, ZieglerAuditInput, ZieglerAuditResult
from agent.services.finance_auditor.prompts import FinanceAuditLlmPort, render_prompt
from agent.services.finance_auditor.rules import CRIME_TERMS, classification_details, identify_actors
from agent.services.finance_auditor.scoring import calculate_scores
from agent.services.finance_auditor.source_quality import assess_sources
from agent.services.finance_auditor.speculation_auditor import audit_speculation
from agent.services.finance_auditor.structural_violence import analyze_structural_violence

_TRADING_TERMS = (
    "buy",
    "sell",
    "short",
    "leverage",
    "execute_order",
    "pump",
    "dump",
    "coordinate_market_action",
    "kaufen",
    "verkaufen",
)
_MANIPULATION_TERMS = ("pump", "dump", "coordinate_market_action", "market manipulation", "marktmanipulation")


class ZieglerAuditorService:
    """Runs policy-bound rules before optional advisory model analysis."""

    def __init__(self, config: ZieglerAuditorConfig | None = None, llm: FinanceAuditLlmPort | None = None) -> None:
        self._config = config or ZieglerAuditorConfig()
        self._llm = llm

    def audit(self, audit_input: ZieglerAuditInput) -> ZieglerAuditResult:
        if not isinstance(audit_input, ZieglerAuditInput):
            raise TypeError("ziegler_audit_input_invalid")
        text = " ".join(part for part in (audit_input.claim, audit_input.context) if part)
        lower = text.lower()
        details = list(classification_details(text))
        source_assessment = assess_sources(audit_input.optional_sources)

        crime_claim = any(term in lower for term in CRIME_TERMS)
        if crime_claim and source_assessment.strong_grounding:
            details.append(
                ClassificationDetail(
                    "actual_crime_allegation",
                    "The submitted allegation has a strong cited source class, but remains "
                    "an allegation pending legal findings.",
                    True,
                    CRIME_TERMS,
                )
            )
        elif crime_claim:
            details.append(
                ClassificationDetail(
                    "evidence_required",
                    "A concrete crime allegation cannot be repeated as fact without strong, identified evidence.",
                    True,
                    CRIME_TERMS,
                )
            )

        basic_needs, consequences = analyze_structural_violence(text)
        speculation_flags, casino_score = audit_speculation(text)
        debt_flags, debt_notes = audit_debt(text)
        costs = analyze_externalization(text)
        profiteers, affected = identify_actors(text)
        classifications = tuple(dict.fromkeys(item.category for item in details))
        scores = calculate_scores(
            classifications=classifications,
            basic_needs=basic_needs,
            casino_score=casino_score,
            debt_flags=debt_flags,
            externalized_costs=costs,
        )
        guardrails = tuple(
            flag
            for flag, active in (
                ("trading_instruction_not_provided", any(term in lower for term in _TRADING_TERMS)),
                ("manipulation_request_not_operationalized", any(term in lower for term in _MANIPULATION_TERMS)),
                ("crime_claim_reframed_as_unverified", crime_claim and not source_assessment.strong_grounding),
            )
            if active
        )
        consequence_notes = tuple(item.explanation for item in consequences) + debt_notes
        legitimacy = self._legitimacy_verdict(scores, classifications)
        summary = self._summary(audit_input, scores, basic_needs, guardrails)
        deterministic: dict[str, Any] = {
            "classification": classifications,
            "scores": scores,
            "basic_needs_affected": basic_needs,
            "profiteers": profiteers,
            "affected_groups": affected,
            "externalized_costs": costs,
            "guardrail_flags": guardrails,
        }
        llm_advisory = None
        if self._config.use_llm and self._llm is not None:
            candidate = self._llm.analyze(render_prompt(audit_input, deterministic))
            if isinstance(candidate, dict):
                llm_advisory = {
                    "analysis": str(candidate.get("analysis") or "")[:10_000],
                    "advisory_only": True,
                    "deterministic_guardrails_preserved": True,
                }
        return ZieglerAuditResult(
            classification=classifications,
            classification_details=tuple(details),
            scores=scores,
            basic_needs_affected=basic_needs,
            profiteers=profiteers,
            affected_groups=affected,
            human_consequences=consequences,
            human_consequence_notes=consequence_notes,
            externalized_costs=costs,
            evidence_notes=source_assessment.evidence_notes,
            legality_vs_legitimacy_note=(
                "Legality is distinct from legitimacy: a lawful mechanism may still "
                "impose avoidable harm or unequal power."
            ),
            legitimacy_verdict=legitimacy,
            moral_balance_summary=moral_balance_summary(costs, profiteers, affected),
            summary=summary,
            guardrail_flags=guardrails,
            confidence=source_assessment.confidence,
            llm_advisory=llm_advisory,
            metadata={
                "read_only": True,
                "investment_advice": False,
                "deterministic_rules_ran_first": True,
                "speculation_flags": speculation_flags,
                "debt_flags": debt_flags,
                "tone": audit_input.requested_tone.value,
            },
        )

    @staticmethod
    def _legitimacy_verdict(scores: dict[str, int], classifications: tuple[str, ...]) -> str:
        if scores["structural_harm_score"] >= 60:
            return "high_legitimacy_concern"
        if classifications == ("distributional_risk",):
            return "insufficient_evidence_for_specific_verdict"
        return "material_legitimacy_concern"

    @staticmethod
    def _summary(
        audit_input: ZieglerAuditInput, scores: dict[str, int], needs: tuple[str, ...], guardrails: tuple[str, ...]
    ) -> str:
        subject = audit_input.asset_type.value
        impact = f" Basic needs implicated: {', '.join(needs)}." if needs else ""
        boundary = (
            " Any operational trading or manipulation request was converted into risk analysis." if guardrails else ""
        )
        return (
            f"Read-only {subject} audit: structural harm "
            f"{scores['structural_harm_score']}/100 and casino risk "
            f"{scores['casino_score']}/100.{impact}{boundary}"
        )
