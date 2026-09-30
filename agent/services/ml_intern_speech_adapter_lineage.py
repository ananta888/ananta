"""Content-free lineage graphs of speech adapter registration and export."""

from __future__ import annotations

import hashlib
from dataclasses import asdict

from agent.repositories.speech_evidence_lineage import SpeechLineageEdge, SpeechLineageNode
from agent.services.ml_intern_speech_adapter_records import SpeechAdapterExportReceipt, SpeechAdapterRecord


def registration_lineage(
    record: SpeechAdapterRecord,
) -> tuple[tuple[SpeechLineageNode, ...], tuple[SpeechLineageEdge, ...]]:
    """Return the canonical content-free registration graph.

    The same immutable graph is staged with the registry write and later
    materialized through the lineage port.  Keeping one builder prevents the
    transactional outbox and graph projection from drifting apart.
    """

    return (
        (
            SpeechLineageNode("manifest", record.dataset_digest),
            SpeechLineageNode("split", record.split_digest),
            SpeechLineageNode("model", record.base_model_digest),
            SpeechLineageNode("evaluation", record.evaluation_report_digest),
            SpeechLineageNode("adapter", record.artifact_sha256),
        ),
        (
            SpeechLineageEdge(
                "manifest", record.dataset_digest, "split", record.split_digest, "split_into"
            ),
            SpeechLineageEdge(
                "split", record.split_digest, "adapter", record.artifact_sha256, "trained_into"
            ),
            SpeechLineageEdge(
                "model", record.base_model_digest, "adapter", record.artifact_sha256, "base_for"
            ),
            SpeechLineageEdge(
                "evaluation",
                record.evaluation_report_digest,
                "adapter",
                record.artifact_sha256,
                "evaluated_adapter",
            ),
        ),
    )


class HubSpeechAdapterExportLineage:
    """Hub-side adapter that records content-free adapter lifecycle nodes."""

    def publish_registration(self, record: "SpeechAdapterRecord") -> None:
        from agent.services.ml_intern_speech_lineage_service import get_ml_intern_speech_lineage_service
        from agent.services.voice_governance_domain import VoicePrincipal

        nodes, edges = registration_lineage(record)
        get_ml_intern_speech_lineage_service().publish(
            VoicePrincipal(record.tenant_id, record.owner_subject),
            nodes=nodes,
            edges=edges,
        )

    def publish_export(
        self,
        record: "SpeechAdapterRecord",
        receipt: SpeechAdapterExportReceipt,
        *,
        export_consent_digest: str,
    ) -> None:
        from agent.repositories.speech_evidence_lineage import SpeechLineageEdge, SpeechLineageNode
        from agent.services.ml_intern_speech_lineage_service import get_ml_intern_speech_lineage_service
        from agent.services.voice_governance_domain import VoicePrincipal
        from ananta_contracts.speech_evidence_governance import canonical_json

        receipt_digest = hashlib.sha256(
            canonical_json(
                {
                    **asdict(receipt),
                    "export_consent_digest": export_consent_digest,
                }
            )
        ).hexdigest()
        get_ml_intern_speech_lineage_service().publish(
            VoicePrincipal(record.tenant_id, record.owner_subject),
            nodes=(
                SpeechLineageNode("adapter", record.artifact_sha256),
                SpeechLineageNode("export", receipt.ciphertext_sha256),
                SpeechLineageNode("receipt", receipt_digest),
            ),
            edges=(
                SpeechLineageEdge(
                    "adapter",
                    record.artifact_sha256,
                    "export",
                    receipt.ciphertext_sha256,
                    "exported_as",
                ),
                SpeechLineageEdge(
                    "export",
                    receipt.ciphertext_sha256,
                    "receipt",
                    receipt_digest,
                    "acknowledged_by",
                ),
            ),
        )


__all__ = ["HubSpeechAdapterExportLineage", "registration_lineage"]
