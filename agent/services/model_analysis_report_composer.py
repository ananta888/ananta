"""Hub-side deterministic composition of completed leaf-analysis artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from agent.services.model_analysis_artifact_publisher import (
    ModelAnalysisArtifactPublisher,
)
from agent.services.model_intelligence_artifact_store import (
    ModelIntelligenceArtifactStorePort,
)
from agent.services.model_intelligence_report_service import (
    ModelIntelligenceReportSection,
    ModelIntelligenceReportService,
)
from ananta_contracts.model_intelligence import ArtifactRef


@dataclass(frozen=True)
class ModelAnalysisReportArtifacts:
    content_digest: str
    json_ref: ArtifactRef
    html_ref: ArtifactRef


class ModelAnalysisReportComposer:
    """Compose a report after the Hub has completed its analysis DAG."""

    _SECTION_NAMES = {
        "tensor.statistics": "static",
        "tokenizer.analysis": "tokenizer",
        "quantization.analysis": "quantization",
    }

    def __init__(
        self,
        *,
        artifact_store: ModelIntelligenceArtifactStorePort,
        publisher: ModelAnalysisArtifactPublisher,
    ) -> None:
        self._publisher = publisher
        self._reports = ModelIntelligenceReportService(
            artifact_store=artifact_store
        )

    def compose(
        self,
        *,
        tenant_id: str,
        report_job_id: str,
        model_identity: Mapping[str, object],
        artifacts: Sequence[ArtifactRef],
    ) -> ModelAnalysisReportArtifacts:
        sections: list[ModelIntelligenceReportSection] = []
        seen: set[str] = set()
        for artifact in sorted(artifacts, key=lambda item: item.kind):
            section_name = self._SECTION_NAMES.get(artifact.kind)
            if section_name is None or section_name in seen:
                continue
            seen.add(section_name)
            payload = self._publisher.load_json(
                tenant_id=tenant_id,
                reference=artifact,
            )
            raw_status = str(payload.get("status") or "failed")
            status = {
                "available": "available",
                "not_available": "not_run",
                "failed": "failed",
            }.get(raw_status, "unsupported")
            reason = payload.get("reason_code")
            sections.append(
                ModelIntelligenceReportSection(
                    name=section_name,
                    status=status,
                    data=payload,
                    reason_code=str(reason) if reason else None,
                    artifact_refs=(
                        self._publisher.store_reference(
                            tenant_id=tenant_id,
                            reference=artifact,
                        ),
                    ),
                )
            )
        rendered = self._reports.render(
            model_identity=model_identity,
            tool_versions={"ananta-static-analysis": "v1"},
            sections=sections,
        )
        stored = self._reports.persist(tenant_id, rendered)
        return ModelAnalysisReportArtifacts(
            content_digest=stored.content_digest,
            json_ref=self._publisher.adopt_reference(
                job_id=report_job_id,
                reference=stored.json_ref,
            ),
            html_ref=self._publisher.adopt_reference(
                job_id=report_job_id,
                reference=stored.html_ref,
            ),
        )

    def load(
        self,
        *,
        tenant_id: str,
        reference: ArtifactRef,
    ) -> dict[str, object]:
        return self._reports.load(
            tenant_id,
            self._publisher.store_reference(
                tenant_id=tenant_id,
                reference=reference,
            ),
        )


__all__ = [
    "ModelAnalysisReportArtifacts",
    "ModelAnalysisReportComposer",
]
