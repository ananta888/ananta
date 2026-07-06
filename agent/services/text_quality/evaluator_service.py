from __future__ import annotations

import re

from .deterministic_scanner import DeterministicTextQualityScanner
from .models import (
    EvaluationStatus,
    TextQualityEvaluationRequest,
    TextQualityEvaluationResult,
)
from .quality_signal_provider import QualitySignalProvider
from .score_fusion_policy import ScoreFusionPolicy


class TextQualityEvaluatorService:
    version = "text-quality-v1"

    def __init__(
        self,
        providers: list[QualitySignalProvider] | None = None,
        scanner: DeterministicTextQualityScanner | None = None,
        fusion: ScoreFusionPolicy | None = None,
    ) -> None:
        self.scanner = scanner or DeterministicTextQualityScanner()
        self.providers = list(providers or [])
        self.fusion = fusion or ScoreFusionPolicy()

    def evaluate(self, request: TextQualityEvaluationRequest) -> TextQualityEvaluationResult:
        scanner_signal = self.scanner.analyze(request)
        signals = [scanner_signal]
        providers = [] if scanner_signal.status == EvaluationStatus.UNSCORABLE else self.providers
        for provider in providers:
            try:
                signals.append(provider.analyze(request))
            except Exception:
                from .models import DetectorSignal

                signals.append(
                    DetectorSignal(
                        provider_name=type(provider).__name__,
                        provider_version="unknown",
                        reason_codes=["provider_unavailable"],
                        status=EvaluationStatus.DEGRADED,
                        degraded_reason="provider_exception",
                    )
                )
        usable = [s for s in signals if s.status == EvaluationStatus.COMPLETED]
        unscorable = signals and all(s.status == EvaluationStatus.UNSCORABLE for s in signals)
        slop_score, confidence, breakdown = self.fusion.fuse(signals)
        reasons = list(dict.fromkeys(code for signal in signals for code in signal.reason_codes))
        findings = [finding for signal in signals for finding in signal.findings][:30]
        evidence_ids = {str(ref.get("source_id") or ref.get("id") or "") for ref in request.evidence_refs}
        mentioned_ids = set(re.findall(r"\b(?:SRC|RUN)_[A-Za-z0-9_-]+\b", request.text))
        unknown_ids = mentioned_ids - evidence_ids
        grounding_status = "verified" if evidence_ids and not unknown_ids else "not_required"
        if unknown_ids:
            grounding_status = "unverified"
            reasons.extend(["source_unverified", "unsupported_specific_claim"])
            slop_score = min(1.0, slop_score + 0.15)
        concrete = bool(re.search(r"\b\d+(?:[.,]\d+)?%?\b", request.text)) or bool(evidence_ids)
        depth_score = max(
            0.0,
            min(
                1.0,
                0.65 + (0.2 if concrete else -0.2) - (0.25 if unknown_ids else 0.0),
            ),
        )
        if not concrete and not unscorable:
            reasons.append("missing_concrete_example")
        status = (
            EvaluationStatus.UNSCORABLE
            if unscorable
            else EvaluationStatus.COMPLETED
            if usable
            else EvaluationStatus.DEGRADED
        )
        return TextQualityEvaluationResult(
            slop_score=slop_score,
            depth_score=depth_score,
            style_fit_score=max(0.0, 1.0 - slop_score),
            reason_codes=list(dict.fromkeys(reasons)),
            findings=findings,
            source_breakdown=breakdown,
            grounding_status=grounding_status,
            evaluator_version=self.version,
            criteria_version=request.criteria.version,
            language=request.language,
            content_kind=request.content_kind,
            confidence=confidence,
            status=status,
        )
