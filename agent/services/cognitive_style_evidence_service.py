"""Pure retrospective hypotheses and controlled style experiment evaluation."""

from __future__ import annotations

import hashlib

from ananta_contracts.cognitive_style import (
    ComplementaryStyleExperimentCommand,
    ComplementaryStyleExperimentReport,
    StyleEvolutionProposal,
    StyleMismatchEvidence,
    StyleRetrospectiveAnalysisReport,
    StyleRetrospectiveSignal,
)


_HYPOTHESES = {
    "rework": "Observed rework may correlate with insufficient rule/correctness fit for the assigned role.",
    "overthinking": "Observed overthinking may correlate with exploration exceeding the role's useful target range.",
    "rule_violation": "A rule violation may correlate with weak rule/correctness fit or an ineffective instruction overlay.",
    "missing_initiative": "Missing initiative may correlate with initiative/assertiveness below the role target.",
    "scope_expansion": "Scope expansion may correlate with initiative/assertiveness above the role's useful target range.",
}

_ALTERNATIVES = {
    "rework": ("ambiguous_requirements", "missing_capability", "insufficient_context"),
    "overthinking": ("unclear_definition_of_done", "excessive_task_scope", "missing_decision_gate"),
    "rule_violation": ("conflicting_instructions", "contract_not_visible", "tool_failure"),
    "missing_initiative": ("permission_boundary_unclear", "insufficient_context", "low_confidence"),
    "scope_expansion": ("task_boundary_ambiguous", "missing_approval_gate", "prompt_conflict"),
}


class CognitiveStyleRetrospectiveService:
    """Produces correlation hypotheses; it never mutates or reclassifies profiles."""

    def analyze(
        self, signals: tuple[StyleRetrospectiveSignal, ...]
    ) -> StyleRetrospectiveAnalysisReport:
        hypotheses = tuple(self._hypothesis(item) for item in signals)
        return StyleRetrospectiveAnalysisReport(hypotheses=hypotheses)

    @staticmethod
    def proposal_from_evidence(
        evidence: StyleMismatchEvidence,
        *,
        proposal_id: str,
        proposal_type: str = "style_target",
        experiment_id: str | None = None,
        sprint_id: str | None = None,
    ) -> StyleEvolutionProposal:
        return StyleEvolutionProposal(
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            hypothesis=evidence.hypothesis,
            expected_effect="Reduce the correlated retrospective signal without weakening hard gates.",
            experiment_id=experiment_id,
            sprint_id=sprint_id,
            payload={
                "role_id": evidence.role_id,
                "model_profile_id": evidence.model_profile_id,
                "signal": evidence.signal,
                "change_requires_review": True,
            },
            evidence_refs=(evidence.evidence_id,),
            rollback_payload={"restore_previous_style_configuration": True},
        )

    @staticmethod
    def _hypothesis(signal: StyleRetrospectiveSignal) -> StyleMismatchEvidence:
        fingerprint = hashlib.sha256(
            "|".join((
                signal.agent_id, signal.role_id, signal.model_profile_id,
                signal.signal, signal.observed_at, *signal.evidence_refs,
            )).encode("utf-8")
        ).hexdigest()[:20]
        return StyleMismatchEvidence(
            evidence_id=f"style-mismatch-{fingerprint}",
            agent_id=signal.agent_id,
            role_id=signal.role_id,
            model_profile_id=signal.model_profile_id,
            signal=signal.signal,
            observed_at=signal.observed_at,
            correlation_score=round(signal.severity, 6),
            hypothesis=_HYPOTHESES[signal.signal],
            alternative_causes=_ALTERNATIVES[signal.signal],
            evidence_refs=signal.evidence_refs,
        )


class ComplementaryStyleExperimentService:
    """Evaluates a falsifiable paired run without claiming causality."""

    def evaluate(
        self, command: ComplementaryStyleExperimentCommand
    ) -> ComplementaryStyleExperimentReport:
        candidate = command.complementary
        control = command.homogeneous_control
        quality_delta = candidate.quality_score - control.quality_score
        rework_delta = candidate.rework_count - control.rework_count
        gate_delta = self._gate_rate(candidate.gates_passed, candidate.gates_total) - self._gate_rate(
            control.gates_passed, control.gates_total
        )
        supported = (
            quality_delta >= command.minimum_quality_delta
            and rework_delta <= 0
            and gate_delta >= 0
        )
        falsified = quality_delta <= -command.minimum_quality_delta or gate_delta < 0
        outcome = "supported" if supported else "falsified" if falsified else "inconclusive"
        reasons = {
            "supported": ("complementary_run_outperformed_control",),
            "falsified": ("complementary_run_regressed_against_control",),
            "inconclusive": ("paired_run_delta_below_threshold",),
        }[outcome]
        return ComplementaryStyleExperimentReport(
            experiment_id=command.experiment_id,
            outcome=outcome,
            quality_delta=round(quality_delta, 6),
            rework_delta=rework_delta,
            cost_delta=round(candidate.cost_units - control.cost_units, 6),
            duration_delta_seconds=round(candidate.duration_seconds - control.duration_seconds, 6),
            gate_rate_delta=round(gate_delta, 6),
            reason_codes=reasons,
        )

    @staticmethod
    def _gate_rate(passed: int, total: int) -> float:
        return passed / total if total else 0.0


__all__ = [
    "CognitiveStyleRetrospectiveService", "ComplementaryStyleExperimentService",
]
