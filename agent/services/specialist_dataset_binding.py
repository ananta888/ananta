"""Hub-owned immutable dataset projection for specialist training and evaluation."""

from __future__ import annotations

import re
from typing import Any, Mapping

from agent.services.ml_intern_training_contract import MlInternTrainingContractError

BINDING_KEY = "_specialist_dataset_binding"
SCHEMA = "ananta.specialist-dataset-binding.v1"


def require_specialist_dataset_execution_purpose(job_type: str, dataset: Any) -> None:
    purpose = (dataset.split_manifest or {}).get("execution_purpose")
    if purpose == "specialist_evaluation" and job_type != "evaluate_lora":
        raise MlInternTrainingContractError(
            "specialist_evaluation_dataset_training_forbidden",
            "specialist evaluation datasets may only be used for evaluation", status_code=409,
        )


def specialist_dataset_binding(dataset: Any) -> dict[str, Any]:
    """Pin catalogue identity, version and both execution partitions without paths."""
    hashes = dict((dataset.dataset_metadata or {}).get("partition_hashes") or {})
    binding = {
        "schema": SCHEMA,
        "dataset_id": dataset.id,
        "version": dataset.version,
        "dataset_sha256": dataset.content_sha256,
        "train_sha256": hashes.get("train"),
        "validation_sha256": hashes.get("validation"),
        "train_records": dataset.train_record_count,
        "validation_records": dataset.validation_record_count,
    }
    for name in ("dataset_sha256", "train_sha256", "validation_sha256"):
        if not isinstance(binding[name], str) or re.fullmatch(r"[0-9a-f]{64}", binding[name]) is None:
            _invalid()
    for name in ("version", "train_records", "validation_records"):
        if type(binding[name]) is not int or binding[name] < 1:
            _invalid()
    return binding


def require_specialist_dataset_binding(spec: Mapping[str, Any], dataset: Any) -> None:
    if spec.get("task_family") == "specialist_decision":
        expected = specialist_dataset_binding(dataset)
        supplied = spec.get(BINDING_KEY)
        if (
            not isinstance(supplied, Mapping)
            or set(supplied) != set(expected)
            or any(type(supplied[key]) is not type(value) for key, value in expected.items())
            or supplied != expected
        ):
            raise MlInternTrainingContractError(
                "specialist_dataset_binding_changed", "specialist dataset changed after job admission", status_code=409
            )


def require_specialist_partition(spec: Mapping[str, Any], manifest: Mapping[str, Any], partition: str) -> None:
    if spec.get("task_family") != "specialist_decision":
        return
    binding = spec.get(BINDING_KEY)
    if (
        not isinstance(binding, Mapping)
        or binding.get("schema") != SCHEMA
        or binding.get("dataset_id") != spec.get("dataset_id")
        or type(binding.get(f"{partition}_records")) is not int
        or manifest.get("sha256") != binding.get(f"{partition}_sha256")
        or manifest.get("record_count") != binding.get(f"{partition}_records")
    ):
        raise MlInternTrainingContractError(
            "specialist_dataset_partition_changed", "specialist partition differs from admitted bytes", status_code=409
        )


def _invalid() -> None:
    raise MlInternTrainingContractError(
        "specialist_dataset_binding_unverified",
        "specialist dataset requires versioned partition hashes and counts",
        status_code=409,
    )
