from __future__ import annotations

import re
import unicodedata

from .models import (
    ContentKind,
    DetectorSignal,
    EvaluationStatus,
    Finding,
    TextQualityEvaluationRequest,
)


class DeterministicTextQualityScanner:
    version = "core-scanner-v1"

    def analyze(self, request: TextQualityEvaluationRequest) -> DetectorSignal:
        text = unicodedata.normalize("NFC", request.text or "")
        words = re.findall(r"\w+", text, flags=re.UNICODE)
        if len(text) > request.max_input_chars or len(words) > request.max_input_words:
            return DetectorSignal(
                provider_name="core_scanner",
                provider_version=self.version,
                reason_codes=["text_too_long"],
                status=EvaluationStatus.UNSCORABLE,
                confidence=1,
            )
        if len(words) < 10:
            return DetectorSignal(
                provider_name="core_scanner",
                provider_version=self.version,
                reason_codes=["text_too_short"],
                status=EvaluationStatus.UNSCORABLE,
                confidence=1,
            )
        findings: list[Finding] = []
        lowered = unicodedata.normalize("NFC", text.casefold())
        for phrase in request.criteria.blocked_phrases:
            needle = unicodedata.normalize("NFC", str(phrase).casefold().strip())
            if not needle:
                continue
            start = lowered.find(needle)
            if start >= 0:
                findings.append(
                    Finding(
                        reason_code="generic_phrase",
                        start=start,
                        end=start + len(needle),
                        excerpt=text[start : start + min(len(needle), 160)],
                    )
                )
        prose_kind = request.content_kind in {
            ContentKind.FREEFORM_PROSE,
            ContentKind.PLANNING_TASK_DESCRIPTION,
            ContentKind.COURSE_MATERIAL,
        }
        if prose_kind:
            for match in re.finditer(
                r"\b(darueber hinaus|zudem|moreover|furthermore)\b",
                lowered,
            ):
                findings.append(
                    Finding(
                        reason_code="overused_transition",
                        start=match.start(),
                        end=match.end(),
                        excerpt=text[match.start() : match.end()],
                    )
                )
        score = min(1.0, sum(0.18 for _ in findings))
        return DetectorSignal(
            provider_name="core_scanner",
            provider_version=self.version,
            raw_signal_score=score,
            normalized_signal_score=score,
            reason_codes=[finding.reason_code for finding in findings],
            findings=findings[:20],
            confidence=1.0,
        )
