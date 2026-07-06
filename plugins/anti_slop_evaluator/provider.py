from __future__ import annotations

from agent.services.evolution.engine import EvolutionEngine
from agent.services.evolution.models import (
    EvolutionCapability,
    EvolutionContext,
    EvolutionProposal,
    EvolutionResult,
)
from agent.services.text_quality.models import ContentKind
from agent.services.text_quality.runtime_service import (
    get_text_quality_runtime_service,
)


class AntiSlopEvolutionProvider(EvolutionEngine):
    @property
    def provider_name(self) -> str:
        return "anti_slop_evaluator"

    @property
    def version(self) -> str:
        return "1.0"

    @property
    def capabilities(self):
        return [EvolutionCapability.ANALYZE, EvolutionCapability.REVIEW_HINTS]

    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        text = str(context.signals.get("text") or "")[:12000]
        if not text.strip():
            return EvolutionResult(
                provider_name=self.provider_name,
                status="degraded",
                summary="authorized_text_missing",
                provider_metadata={"reason_code": "authorized_text_missing"},
            )
        result, row = get_text_quality_runtime_service().evaluate(
            text=text,
            language=str(context.signals.get("language") or "de"),
            content_kind=ContentKind(str(context.signals.get("content_kind") or "freeform_prose")),
            evidence_refs=list(context.source_refs or []),
        )
        proposals = []
        if result.status.value == "completed" and result.reason_codes:
            proposals.append(
                EvolutionProposal(
                    title="Review text-quality prompt criteria",
                    description=", ".join(result.reason_codes[:8]),
                    proposal_type="prompt_quality",
                    risk_level="medium",
                    confidence=result.confidence,
                    requires_review=True,
                    provider_metadata={"evaluation_id": row.id},
                )
            )
        return EvolutionResult(
            provider_name=self.provider_name,
            summary=f"text quality {result.status.value}",
            proposals=proposals,
            provider_metadata={
                "evaluation_id": row.id,
                "criteria_version": result.criteria_version,
                "evaluator_version": result.evaluator_version,
                "content_kind": result.content_kind.value,
                "language": result.language,
                "scores": {
                    "slop": result.slop_score,
                    "depth": result.depth_score,
                    "style_fit": result.style_fit_score,
                },
            },
        )
