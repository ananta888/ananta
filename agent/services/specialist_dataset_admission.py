"""Admit the Factory's fixed training partitions through existing Hub services."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from typing import Any

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.ml_intern_dataset_catalog_service import MlInternDatasetCatalogService
from agent.services.ml_intern_dataset_repository_bridge_service import MlInternDatasetRepositoryBridgeService
from agent.services.specialist_dataset_split import SplitManifest
from ananta_contracts.specialist_dataset_admission import AdmittedSpecialistDataset

MAX_PARTITION_BYTES = 64 * 1024 * 1024


def encode_specialist_partition(records: Sequence[Mapping[str, Any]]) -> bytes:
    result = bytearray()
    for record in records:
        row = (json.dumps(dict(record), sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(result) + len(row) > MAX_PARTITION_BYTES:
            raise ValueError("specialist_dataset_partition_quota_exceeded")
        result.extend(row)
    return bytes(result)


class HubSpecialistDatasetAdmission:
    def __init__(
        self,
        *,
        catalog: MlInternDatasetCatalogService,
        bridge: MlInternDatasetRepositoryBridgeService,
        principal: MlInternTrainingPrincipal,
    ):
        self._catalog = catalog
        self._bridge = bridge
        self._principal = principal

    def admit(
        self, manifest: SplitManifest, train: Sequence[Mapping[str, Any]], validation: Sequence[Mapping[str, Any]]
    ) -> AdmittedSpecialistDataset:
        for name, records in (("train", train), ("validation", validation)):
            if not records or tuple(row.get("record_digest") for row in records) != manifest.partitions[name]:
                raise ValueError("specialist_dataset_partition_manifest_mismatch")
        return self._project(manifest, train, validation)

    def admit_evaluation(
        self, manifest: SplitManifest, train: Sequence[Mapping[str, Any]],
        evaluation: Sequence[Mapping[str, Any]], *, partition: str,
    ) -> AdmittedSpecialistDataset:
        if partition not in {"validation", "holdout"}:
            raise ValueError("specialist_evaluation_partition_invalid")
        for name, records in (("train", train), (partition, evaluation)):
            if not records or tuple(row.get("record_digest") for row in records) != manifest.partitions[name]:
                raise ValueError("specialist_dataset_partition_manifest_mismatch")
        return self._project(manifest, train, evaluation, evaluation_partition=partition)

    def _project(
        self, manifest: SplitManifest, train: Sequence[Mapping[str, Any]],
        validation: Sequence[Mapping[str, Any]], *, evaluation_partition: str | None = None,
    ) -> AdmittedSpecialistDataset:
        encoded = {
            name: encode_specialist_partition(records)
            for name, records in (("train", train), ("validation", validation))
        }
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in encoded.items()}
        scope = {"tenant_id": self._principal.tenant_id, "principal_id": self._principal.subject}
        identity = hashes["train"]
        split_extra = {}
        if evaluation_partition is not None:
            identity = hashlib.sha256(
                (
                    f"evaluation:{evaluation_partition}:{manifest.manifest_digest}:"
                    f"{hashes['train']}:{hashes['validation']}"
                ).encode()
            ).hexdigest()
            split_extra = {
                "execution_purpose": "specialist_evaluation",
                "evaluation_partition": evaluation_partition,
                "specialist_manifest_digest": manifest.manifest_digest,
                "holdout_digest": manifest.holdout.holdout_digest,
            }
        summary = self._catalog.create_from_records(
            **scope,
            records=[dict(row) for row in train],
            name=f"specialist-{identity[:16]}",
            idempotency_key=f"specialist-{identity}",
        )
        dataset_id = summary["dataset_id"]
        partitions = summary.get("partitions") or {}
        if any((partitions.get(name) or {}).get("sha256") != digest for name, digest in hashes.items()) or any(
            (summary.get("split") or {}).get(key) != value for key, value in split_extra.items()
        ):
            summary = self._catalog.commit_split(
                **scope,
                dataset_id=dataset_id,
                train_stream=io.BytesIO(encoded["train"]),
                validation_stream=io.BytesIO(encoded["validation"]),
                manifest={
                    **split_extra,
                    "algorithm_version": "specialist-fixed-partitions-v1",
                    "seed": manifest.seed,
                    "validation_ratio": manifest.validation_ratio,
                    "train_record_count": len(train),
                    "validation_record_count": len(validation),
                    "train_sha256": hashes["train"],
                    "validation_sha256": hashes["validation"],
                },
            )
        if not (summary.get("validation") or {}).get("trainable"):
            report = self._catalog.validate_dataset(**scope, dataset_id=dataset_id)
            if report.get("ok") is not True:
                raise ValueError("specialist_dataset_catalog_validation_failed")
        summary = self._catalog.get_dataset(**scope, dataset_id=dataset_id)
        if any(
            ((summary.get("partitions") or {}).get(name) or {}).get("sha256") != digest
            for name, digest in hashes.items()
        ):
            raise ValueError("specialist_dataset_changed_during_admission")
        report = self._catalog.get_validation_report(**scope, dataset_id=dataset_id)
        projected = self._bridge.sync(self._principal, summary, validation_report=report)
        return AdmittedSpecialistDataset(projected["id"], projected["version"])
