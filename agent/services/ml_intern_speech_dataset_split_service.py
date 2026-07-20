"""Leakage-safe deterministic split for multi-contributor speech manifests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

from agent.repositories.speech_evidence_lineage import SpeechLineageEdge, SpeechLineageNode
from agent.services.ml_intern_speech_dataset_build_service import MlInternSpeechDatasetBuildService
from agent.services.ml_intern_speech_lineage_service import (
    MlInternSpeechLineageService,
    get_ml_intern_speech_lineage_service,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import canonical_json


class SpeechDatasetSplitError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechDatasetSplit:
    algorithm_version: str
    seed: int
    validation_ratio: float
    manifest_digest: str
    split_digest: str
    assignments: Mapping[str, str]
    group_assignments: Mapping[str, str]
    train_count: int
    validation_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ananta.speech-dataset-split.v1",
            "algorithm_version": self.algorithm_version,
            "seed": self.seed,
            "validation_ratio": self.validation_ratio,
            "manifest_digest": self.manifest_digest,
            "split_digest": self.split_digest,
            "assignments": dict(self.assignments),
            "group_assignments": dict(self.group_assignments),
            "train_count": self.train_count,
            "validation_count": self.validation_count,
        }


class MlInternSpeechDatasetSplitService:
    ALGORITHM_VERSION = "speech-connected-leakage-groups-v1"

    def __init__(
        self,
        *,
        manifest_validator: MlInternSpeechDatasetBuildService | None = None,
        lineage: MlInternSpeechLineageService | None = None,
    ) -> None:
        # A publisher is irrelevant for validation; callers normally inject
        # their already composed builder in production.
        self._validator = manifest_validator or MlInternSpeechDatasetBuildService()
        self._lineage = lineage or get_ml_intern_speech_lineage_service()

    def split(
        self,
        principal: VoicePrincipal,
        manifest: Mapping[str, object],
        *,
        validation_ratio: float = 0.2,
        seed: int = 3407,
        authority: str = "hub",
        publish_lineage: bool = True,
    ) -> SpeechDatasetSplit:
        if authority != "hub":
            raise SpeechDatasetSplitError("speech_split_hub_authority_required")
        self._validator.validate(manifest)
        curation = manifest.get("curation_summary")
        if isinstance(curation, Mapping) and (
            curation.get("trainable") is not True
            or any(
                not isinstance(value, Mapping) or value.get("curation_status") != "resolved"
                for value in manifest.get("records", ())
            )
        ):
            raise SpeechDatasetSplitError("speech_split_dataset_not_trainable")
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**31 - 1:
            raise SpeechDatasetSplitError("speech_split_seed_invalid")
        if isinstance(validation_ratio, bool) or not isinstance(validation_ratio, (int, float)):
            raise SpeechDatasetSplitError("speech_split_ratio_invalid")
        ratio = float(validation_ratio)
        if not 0.05 <= ratio <= 0.5:
            raise SpeechDatasetSplitError("speech_split_ratio_invalid")
        records = list(manifest.get("records") or [])
        if len(records) < 2:
            raise SpeechDatasetSplitError("speech_split_dataset_too_small")
        parent: dict[int, int] = {index: index for index in range(len(records))}

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            a, b = find(left), find(right)
            if a != b:
                parent[max(a, b)] = min(a, b)

        observed: dict[tuple[str, str], int] = {}
        for index, raw in enumerate(records):
            record = dict(raw)
            groups = (
                ("session", str(record["session_group_id"])),
                ("utterance", str(record["utterance_family_id"])),
                ("source", str(record["source_digest"])),
                ("near_duplicate", str(record["near_duplicate_group_id"])),
            )
            for group in groups:
                previous = observed.setdefault(group, index)
                union(index, previous)
        components: dict[int, list[int]] = {}
        for index in range(len(records)):
            components.setdefault(find(index), []).append(index)
        if len(components) < 2:
            raise SpeechDatasetSplitError("speech_split_indivisible_leakage_group")
        ordered = sorted(
            components.values(),
            key=lambda indexes: hashlib.sha256(
                f"{self.ALGORITHM_VERSION}:{seed}:".encode()
                + ":".join(sorted(str(records[index]["record_digest"]) for index in indexes)).encode()
            ).hexdigest(),
        )
        target = max(1, min(len(records) - 1, round(len(records) * ratio)))
        validation_components: set[int] = set()
        validation_count = 0
        for indexes in ordered:
            if len(records) - validation_count - len(indexes) < 1:
                continue
            validation_components.add(find(indexes[0]))
            validation_count += len(indexes)
            if validation_count >= target:
                break
        if not validation_components or validation_count >= len(records):
            raise SpeechDatasetSplitError("speech_split_balance_impossible")
        assignments: dict[str, str] = {}
        group_assignments: dict[str, str] = {}
        for index, raw in enumerate(records):
            record = dict(raw)
            partition = "validation" if find(index) in validation_components else "train"
            assignments[str(record["record_digest"])] = partition
            for group in (
                str(record["session_group_id"]),
                str(record["utterance_family_id"]),
                str(record["source_digest"]),
                str(record["near_duplicate_group_id"]),
            ):
                previous = group_assignments.setdefault(group, partition)
                if previous != partition:
                    raise SpeechDatasetSplitError("speech_split_group_overlap")
        payload = {
            "algorithm_version": self.ALGORITHM_VERSION,
            "seed": seed,
            "validation_ratio": ratio,
            "manifest_digest": manifest["manifest_digest"],
            "assignments": assignments,
            "group_assignments": group_assignments,
        }
        split_digest = hashlib.sha256(canonical_json(payload)).hexdigest()
        result = SpeechDatasetSplit(
            algorithm_version=self.ALGORITHM_VERSION,
            seed=seed,
            validation_ratio=ratio,
            manifest_digest=str(manifest["manifest_digest"]),
            split_digest=split_digest,
            assignments=assignments,
            group_assignments=group_assignments,
            train_count=len(records) - validation_count,
            validation_count=validation_count,
        )
        if publish_lineage:
            self._lineage.publish(
                principal,
                nodes=(
                    SpeechLineageNode("manifest", result.manifest_digest),
                    SpeechLineageNode("split", split_digest),
                ),
                edges=(
                    SpeechLineageEdge(
                        "manifest",
                        result.manifest_digest,
                        "split",
                        split_digest,
                        "split_into",
                    ),
                ),
            )
        return result


__all__ = ["MlInternSpeechDatasetSplitService", "SpeechDatasetSplit", "SpeechDatasetSplitError"]
