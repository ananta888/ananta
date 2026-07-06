from __future__ import annotations

import hashlib
import json

from agent.db_models import TextQualityEvaluationDB
from agent.services.repository_registry import get_repository_registry

from .criteria_service import get_criteria_service
from .evaluator_service import TextQualityEvaluatorService
from .models import ContentKind, TextQualityEvaluationRequest


class TextQualityRuntimeService:
    def __init__(self, evaluator: TextQualityEvaluatorService | None = None) -> None:
        self.evaluator = evaluator or TextQualityEvaluatorService()

    def evaluate(
        self,
        *,
        text: str,
        language: str,
        content_kind: ContentKind,
        evidence_refs: list[dict] | None = None,
        planning_run_id: str | None = None,
        planning_evaluation_id: str | None = None,
        prompt_version_id: str | None = None,
    ):
        criteria = get_criteria_service().active(language, content_kind)
        result = self.evaluator.evaluate(
            TextQualityEvaluationRequest(
                text=text,
                language=language,
                content_kind=content_kind,
                criteria=criteria,
                evidence_refs=evidence_refs or [],
            )
        )
        identity = hashlib.sha256(
            json.dumps(
                {
                    "run": planning_run_id,
                    "text": hashlib.sha256(text.encode()).hexdigest(),
                    "criteria": result.criteria_version,
                    "evaluator": result.evaluator_version,
                    "kind": content_kind.value,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        row = get_repository_registry().text_quality_evaluation_repo.save(
            TextQualityEvaluationDB(
                id=result.evaluation_id,
                planning_run_id=planning_run_id,
                planning_evaluation_id=planning_evaluation_id,
                criteria_set_id=criteria.id,
                prompt_version_id=prompt_version_id,
                evaluator_version=result.evaluator_version,
                criteria_version=result.criteria_version,
                language=result.language,
                content_kind=result.content_kind.value,
                status=result.status.value,
                slop_score=result.slop_score,
                depth_score=result.depth_score,
                style_fit_score=result.style_fit_score,
                confidence=result.confidence,
                reason_codes=result.reason_codes,
                result_payload=result.model_dump(
                    mode="json",
                    exclude={"source_breakdown", "findings"},
                ),
                identity_checksum=identity,
            )
        )
        return result, row


_SERVICE = TextQualityRuntimeService()


def get_text_quality_runtime_service() -> TextQualityRuntimeService:
    return _SERVICE
