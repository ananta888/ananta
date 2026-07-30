"""Infrastructure adapter from worker result publication to ArtifactStore."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

from agent.services.model_intelligence_artifact_store import (
    ModelIntelligenceArtifactRef,
    ModelIntelligenceArtifactStorePort,
)
from ananta_contracts.model_intelligence import AnalysisJob, ArtifactRef


class ModelAnalysisArtifactPublisherError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ModelAnalysisArtifactPublisher:
    """Publish deterministic JSON and bridge opaque contract/store references."""

    def __init__(self, store: ModelIntelligenceArtifactStorePort) -> None:
        self._store = store

    def publish_json(
        self,
        *,
        job: AnalysisJob,
        artifact_kind: str,
        payload: Mapping[str, object],
    ) -> ArtifactRef:
        try:
            content = (
                json.dumps(
                    payload,
                    allow_nan=False,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("ascii")
                + b"\n"
            )
        except (TypeError, ValueError) as exc:
            raise ModelAnalysisArtifactPublisherError(
                "analysis_artifact_json_invalid",
                "analysis output is not canonical JSON",
            ) from exc
        if len(content) > job.max_output_bytes:
            raise ModelAnalysisArtifactPublisherError(
                "analysis_artifact_output_limit_exceeded",
                "analysis output exceeds the admitted job limit",
            )
        stored = self._store.put_bytes(
            job.tenant_id,
            content,
            media_type="application/json",
            artifact_kind=artifact_kind,
        )
        return self.adopt_reference(job_id=job.job_id, reference=stored)

    def adopt_reference(
        self,
        *,
        job_id: str,
        reference: ModelIntelligenceArtifactRef,
    ) -> ArtifactRef:
        contract_kind = {
            "model-intelligence-report-json": "report.json",
            "model-intelligence-report-html": "report.html",
        }.get(reference.artifact_kind, reference.artifact_kind)
        contract_media_type = reference.media_type.split(";", 1)[0].strip()
        artifact_digest = hashlib.sha256(
            (
                job_id
                + "\0"
                + contract_kind
                + "\0"
                + reference.digest
            ).encode("utf-8")
        ).hexdigest()
        return ArtifactRef(
            artifact_id=f"artifact-{artifact_digest[:32]}",
            job_id=job_id,
            kind=contract_kind,
            sha256=reference.digest.removeprefix("sha256:"),
            size_bytes=reference.size_bytes,
            media_type=contract_media_type,
        )

    def store_reference(
        self,
        *,
        tenant_id: str,
        reference: ArtifactRef,
    ) -> ModelIntelligenceArtifactRef:
        return ModelIntelligenceArtifactRef(
            digest=f"sha256:{reference.sha256}",
            media_type=reference.media_type,
            size_bytes=reference.size_bytes,
            tenant_scope=hashlib.sha256(tenant_id.encode("utf-8")).hexdigest(),
            artifact_kind=reference.kind,
        )

    def load_bytes(self, *, tenant_id: str, reference: ArtifactRef) -> bytes:
        return self._store.get_bytes(
            tenant_id,
            self.store_reference(tenant_id=tenant_id, reference=reference),
        )

    def load_json(
        self,
        *,
        tenant_id: str,
        reference: ArtifactRef,
    ) -> dict[str, object]:
        try:
            payload = json.loads(
                self.load_bytes(tenant_id=tenant_id, reference=reference)
            )
        except (UnicodeError, ValueError) as exc:
            raise ModelAnalysisArtifactPublisherError(
                "analysis_artifact_json_invalid",
                "stored analysis artifact is not valid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise ModelAnalysisArtifactPublisherError(
                "analysis_artifact_json_invalid",
                "stored analysis artifact must be a JSON object",
            )
        return payload


__all__ = [
    "ModelAnalysisArtifactPublisher",
    "ModelAnalysisArtifactPublisherError",
]
