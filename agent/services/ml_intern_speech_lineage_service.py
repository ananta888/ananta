"""Hub application service for speech lineage and impact analysis."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    SpeechLineageEdge,
    SpeechLineageNode,
    SpeechLineagePage,
    get_speech_evidence_lineage_repository,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_adaptation import SpeechAdaptationJob, SpeechAdaptationResult
from ananta_contracts.speech_evidence_governance import canonical_json


@dataclass(frozen=True)
class SpeechImpactReport:
    root_kind: str
    root_digest: str
    revocation_epoch: int
    nodes: tuple[dict[str, object], ...]
    impact_digest: str
    truncated: bool


class MlInternSpeechLineageService:
    def __init__(self, repository: SpeechEvidenceLineageRepository | None = None) -> None:
        self._repository = repository or get_speech_evidence_lineage_repository()

    def publish(
        self,
        principal: VoicePrincipal,
        *,
        nodes: Sequence[SpeechLineageNode],
        edges: Sequence[SpeechLineageEdge],
        authority: str = "hub",
    ) -> str:
        if authority != "hub":
            raise PermissionError("speech_lineage_hub_authority_required")
        return self._repository.publish(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            nodes=nodes,
            edges=edges,
        )

    def forward(
        self,
        principal: VoicePrincipal,
        *,
        root_kind: str,
        root_digest: str,
        depth_limit: int = 16,
        offset: int = 0,
        limit: int = 200,
    ) -> SpeechLineagePage:
        return self._repository.traverse(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            root_kind=root_kind,
            root_digest=root_digest,
            direction="forward",
            depth_limit=depth_limit,
            offset=offset,
            limit=limit,
        )

    def backward(self, principal: VoicePrincipal, **kwargs) -> SpeechLineagePage:
        return self._repository.traverse(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            direction="backward",
            **kwargs,
        )

    def impact(
        self,
        principal: VoicePrincipal,
        *,
        root_kind: str,
        root_digest: str,
        revocation_epoch: int,
    ) -> SpeechImpactReport:
        # Domain writers stage graph changes transactionally.  Materialize
        # this principal's pending outbox before impact analysis so a revoke
        # immediately following a domain commit cannot miss fresh descendants.
        self._repository.recover_pending(
            limit=1000,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
        )
        page = self._repository.traverse_impact(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            root_kind=root_kind,
            root_digest=root_digest,
        )
        # Impact mutation is bounded to one page; a truncated report is never
        # treated as complete by revocation.
        # Status and per-node revocation_epoch are transition state, not graph
        # identity.  Excluding them makes parallel revokers agree on the same
        # impact decision while the caller still receives the full node view.
        digest_nodes = [
            {
                "kind": node["kind"],
                "digest": node["digest"],
                "consent_id": node["consent_id"],
                "depth": node["depth"],
            }
            for node in page.nodes
        ]
        payload = {
            "root_kind": root_kind,
            "root_digest": root_digest,
            "revocation_epoch": revocation_epoch,
            "nodes": digest_nodes,
            "truncated": page.truncated,
        }
        return SpeechImpactReport(
            root_kind=root_kind,
            root_digest=root_digest,
            revocation_epoch=revocation_epoch,
            nodes=page.nodes,
            impact_digest=hashlib.sha256(canonical_json(payload)).hexdigest(),
            truncated=bool(payload["truncated"]),
        )

    def publish_training_job(
        self,
        principal: VoicePrincipal,
        job: SpeechAdaptationJob,
        *,
        authority: str = "hub",
    ) -> str:
        """Bind admitted immutable inputs to the Hub-owned training job."""

        nodes = [
            SpeechLineageNode("manifest", job.dataset.dataset_digest),
            SpeechLineageNode("split", job.dataset.split_digest),
            SpeechLineageNode("model", job.base_model.model_digest),
            SpeechLineageNode(
                "job",
                job.binding_digest,
                consent_id=job.consent.consent_id,
            ),
        ]
        edges = [
            SpeechLineageEdge(
                "manifest",
                job.dataset.dataset_digest,
                "split",
                job.dataset.split_digest,
                "split_into",
            ),
            SpeechLineageEdge("split", job.dataset.split_digest, "job", job.binding_digest, "input_to"),
            SpeechLineageEdge("model", job.base_model.model_digest, "job", job.binding_digest, "base_for"),
        ]
        if job.resume is not None:
            nodes.append(SpeechLineageNode("checkpoint", job.resume.checkpoint_digest))
            edges.append(
                SpeechLineageEdge(
                    "checkpoint",
                    job.resume.checkpoint_digest,
                    "job",
                    job.binding_digest,
                    "resumed_by",
                )
            )
        return self.publish(principal, nodes=tuple(nodes), edges=tuple(edges), authority=authority)

    def publish_training_result(
        self,
        principal: VoicePrincipal,
        job: SpeechAdaptationJob,
        result: SpeechAdaptationResult,
        *,
        authority: str = "hub",
    ) -> str:
        """Publish only a result that still matches the admitted Hub fence."""

        if (
            result.job_id != job.job_id
            or result.attempt_id != job.attempt.attempt_id
            or result.binding_digest != job.binding_digest
            or result.fencing_digest != job.fencing.fencing_digest
        ):
            raise ValueError("speech_lineage_result_binding_mismatch")
        nodes = [SpeechLineageNode("job", job.binding_digest)]
        edges: list[SpeechLineageEdge] = []
        parents: list[tuple[str, str]] = [("job", job.binding_digest)]
        if result.checkpoint_digest is not None:
            nodes.append(SpeechLineageNode("checkpoint", result.checkpoint_digest))
            edges.append(
                SpeechLineageEdge(
                    "job",
                    job.binding_digest,
                    "checkpoint",
                    result.checkpoint_digest,
                    "checkpointed_as",
                )
            )
            parents.append(("checkpoint", result.checkpoint_digest))
        if result.evaluation_report_digest is not None:
            nodes.append(SpeechLineageNode("evaluation", result.evaluation_report_digest))
            edges.append(
                SpeechLineageEdge(
                    "job",
                    job.binding_digest,
                    "evaluation",
                    result.evaluation_report_digest,
                    "evaluated_as",
                )
            )
            parents.append(("evaluation", result.evaluation_report_digest))
        if result.artifact is not None:
            nodes.append(SpeechLineageNode("adapter", result.artifact.sha256))
            edges.extend(
                SpeechLineageEdge(kind, digest, "adapter", result.artifact.sha256, "produced")
                for kind, digest in parents
            )
        return self.publish(principal, nodes=tuple(nodes), edges=tuple(edges), authority=authority)


_service = MlInternSpeechLineageService()


def get_ml_intern_speech_lineage_service() -> MlInternSpeechLineageService:
    return _service


__all__ = [
    "MlInternSpeechLineageService",
    "SpeechImpactReport",
    "get_ml_intern_speech_lineage_service",
]
