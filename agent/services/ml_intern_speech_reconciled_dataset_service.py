"""Hub adapter that materializes terminal reconciliation into immutable datasets."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from agent.services.ml_intern_speech_dataset_build_service import MlInternSpeechDatasetBuildService
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import canonical_json


@dataclass(frozen=True)
class ReconciledDatasetCandidate:
    status: str
    record: Mapping[str, object]
    resolution_id: str | None = None
    unresolved_region_ids: tuple[str, ...] = ()
    disposition_reason: str | None = None


@dataclass(frozen=True)
class ReconciledDatasetMaterialization:
    manifest: Mapping[str, object]
    created: bool
    resolved_count: int
    unresolved_count: int
    rejected_count: int
    quarantined_count: int
    trainable: bool
    curation_summary: Mapping[str, object] | None = None


class MlInternSpeechReconciledDatasetService:
    def __init__(self, builder: MlInternSpeechDatasetBuildService) -> None:
        self._builder = builder

    def materialize(
        self,
        principal: VoicePrincipal,
        *,
        dataset_id: str,
        candidates: Sequence[ReconciledDatasetCandidate],
        reconciliation_digest: str,
        parent_digest: str | None,
        terminal: bool,
        observed_unresolved_count: int | None = None,
        authority: str = "hub",
    ) -> ReconciledDatasetMaterialization:
        if authority != "hub":
            raise PermissionError("speech_reconciliation_hub_dataset_authority_required")
        allowed = {"resolved", "unresolved", "rejected", "quarantined"}
        if not candidates or any(candidate.status not in allowed for candidate in candidates):
            raise ValueError("speech_reconciliation_dataset_candidates_invalid")
        source_digests = tuple(_record_digest(candidate.record) for candidate in candidates)
        if len(source_digests) != len(set(source_digests)):
            raise ValueError("speech_reconciliation_dataset_candidate_duplicate")
        materialized = tuple(
            _materialize_candidate(candidate, reconciliation_digest=reconciliation_digest) for candidate in candidates
        )
        counts = {status: sum(candidate.status == status for candidate in candidates) for status in allowed}
        if observed_unresolved_count is not None:
            if type(observed_unresolved_count) is not int or not 0 <= observed_unresolved_count <= 1_000_000:
                raise ValueError("speech_reconciliation_dataset_counts_invalid")
            if observed_unresolved_count != counts["unresolved"]:
                raise ValueError("speech_reconciliation_dataset_counts_invalid")
        trainable = (
            terminal
            and counts["resolved"] > 0
            and counts["unresolved"] == 0
            and counts["rejected"] == 0
            and counts["quarantined"] == 0
        )
        outcomes = sorted(
            (
                dict(item["reconciliation_outcome"]) | {"output_record_digest": item["record_digest"]}
                for item in materialized
            ),
            key=lambda item: str(item["source_record_digest"]),
        )
        digest_lists = {
            status: sorted(
                str(record["record_digest"])
                for candidate, record in zip(candidates, materialized, strict=True)
                if candidate.status == status
            )
            for status in allowed
        }
        curation_summary: dict[str, object] = {
            "schema": "ananta.speech-reconciliation-curation.v1",
            "reconciliation_digest": _digest(reconciliation_digest),
            "input_count": len(candidates),
            "resolved_count": counts["resolved"],
            "unresolved_count": counts["unresolved"],
            "rejected_count": counts["rejected"],
            "quarantined_count": counts["quarantined"],
            "resolved_record_digests": digest_lists["resolved"],
            "unresolved_record_digests": digest_lists["unresolved"],
            "rejected_record_digests": digest_lists["rejected"],
            "quarantined_record_digests": digest_lists["quarantined"],
            "resolution_outcomes_sha256": hashlib.sha256(canonical_json(outcomes)).hexdigest(),
            "outcomes": outcomes,
            "terminal": bool(terminal),
            "trainable": trainable,
        }
        curation_digest = hashlib.sha256(canonical_json(curation_summary)).hexdigest()
        manifest, created = self._builder.build(
            principal,
            dataset_id=dataset_id,
            records=materialized,
            curation_report_digest=curation_digest,
            parent_digest=parent_digest,
            curation_summary=curation_summary,
            authority=authority,
        )
        return ReconciledDatasetMaterialization(
            manifest=manifest,
            created=created,
            resolved_count=counts["resolved"],
            unresolved_count=counts["unresolved"],
            rejected_count=counts["rejected"],
            quarantined_count=counts["quarantined"],
            trainable=trainable,
            curation_summary=curation_summary,
        )


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REASON = re.compile(r"^speech_reconciliation_[a-z0-9_]{1,104}$")


def _digest(value: object) -> str:
    text = str(value or "")
    if _DIGEST.fullmatch(text) is None:
        raise ValueError("speech_reconciliation_dataset_digest_invalid")
    return text


def _record_digest(record: Mapping[str, object]) -> str:
    return _digest(record.get("record_digest"))


def _materialize_candidate(
    candidate: ReconciledDatasetCandidate,
    *,
    reconciliation_digest: str,
) -> dict[str, object]:
    source_record_digest = _record_digest(candidate.record)
    region_ids = tuple(_digest(value) for value in candidate.unresolved_region_ids)
    if region_ids != tuple(sorted(set(region_ids))):
        raise ValueError("speech_reconciliation_dataset_regions_invalid")
    resolution_id = candidate.resolution_id
    if candidate.status == "resolved":
        resolution_id = _digest(resolution_id or reconciliation_digest)
        if region_ids:
            raise ValueError("speech_reconciliation_dataset_outcome_invalid")
    else:
        if resolution_id is not None:
            raise ValueError("speech_reconciliation_dataset_outcome_invalid")
        if candidate.status == "unresolved" and not region_ids:
            raise ValueError("speech_reconciliation_dataset_outcome_invalid")
    reason = candidate.disposition_reason or f"speech_reconciliation_{candidate.status}"
    if _REASON.fullmatch(reason) is None:
        raise ValueError("speech_reconciliation_dataset_reason_invalid")
    outcome: dict[str, object] = {
        "source_record_digest": source_record_digest,
        "reconciliation_digest": _digest(reconciliation_digest),
        "status": candidate.status,
        "resolution_id": resolution_id,
        "unresolved_region_ids": list(region_ids),
        "disposition_reason": reason,
    }
    output_record_digest = hashlib.sha256(canonical_json(outcome)).hexdigest()
    record = dict(candidate.record)
    record.update(
        {
            "record_digest": output_record_digest,
            "lineage_kind": "reconciliation",
            "curation_status": candidate.status,
            "reconciliation_outcome": outcome,
        }
    )
    return record


__all__ = [
    "MlInternSpeechReconciledDatasetService",
    "ReconciledDatasetCandidate",
    "ReconciledDatasetMaterialization",
]
