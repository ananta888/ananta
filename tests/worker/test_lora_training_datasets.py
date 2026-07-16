from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from worker.training.contracts import DatasetManifest, TrainingContractError
from worker.training.datasets import DatasetValidator


def _write(path: Path, rows: list[dict[str, object]]) -> tuple[str, int]:
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest(), len(rows)


def _manifest(train: tuple[str, int], validation: tuple[str, int]) -> DatasetManifest:
    return DatasetManifest.from_mapping(
        {
            "dataset_id": "dataset-1",
            "dataset_version": "v1",
            "train": {"relative_path": "train.jsonl", "sha256": train[0], "record_count": train[1]},
            "validation": {
                "relative_path": "validation.jsonl",
                "sha256": validation[0],
                "record_count": validation[1],
            },
        }
    )


def test_validator_verifies_hash_count_schema_and_split_separation(tmp_path: Path) -> None:
    train = _write(tmp_path / "train.jsonl", [{"instruction": "one", "output": "answer"}])
    validation = _write(tmp_path / "validation.jsonl", [{"messages": [{"role": "user", "content": "two"}]}])

    verified = DatasetValidator(tmp_path).validate(_manifest(train, validation))

    assert verified.train_records == 1
    assert verified.validation_records == 1
    assert len(verified.dataset_hash) == 64


def test_validator_rejects_cross_split_leakage(tmp_path: Path) -> None:
    row = {"instruction": "same", "output": "same"}
    train = _write(tmp_path / "train.jsonl", [row])
    validation = _write(tmp_path / "validation.jsonl", [row])

    with pytest.raises(TrainingContractError, match="identical") as error:
        DatasetValidator(tmp_path).validate(_manifest(train, validation))

    assert error.value.code == "cross_split_leakage"


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("hash", "dataset_hash_mismatch"),
        ("count", "record_count_mismatch"),
        ("schema", "invalid_dataset_record"),
    ],
)
def test_validator_rejects_manifest_and_schema_mismatches(tmp_path: Path, mutation: str, reason_code: str) -> None:
    train_rows: list[dict[str, object]] = [{"text": "train"}]
    if mutation == "schema":
        train_rows = [{"unknown": "value"}]
    train = _write(tmp_path / "train.jsonl", train_rows)
    validation = _write(tmp_path / "validation.jsonl", [{"text": "validation"}])
    manifest_data = {
        "dataset_id": "dataset-1",
        "dataset_version": "v1",
        "train": {
            "relative_path": "train.jsonl",
            "sha256": "0" * 64 if mutation == "hash" else train[0],
            "record_count": 2 if mutation == "count" else train[1],
        },
        "validation": {
            "relative_path": "validation.jsonl",
            "sha256": validation[0],
            "record_count": validation[1],
        },
    }

    with pytest.raises(TrainingContractError) as error:
        DatasetValidator(tmp_path).validate(DatasetManifest.from_mapping(manifest_data))

    assert error.value.code == reason_code


def test_manifest_rejects_empty_validation_before_training(tmp_path: Path) -> None:
    train = _write(tmp_path / "train.jsonl", [{"text": "train"}])
    validation = _write(tmp_path / "validation.jsonl", [])

    with pytest.raises(TrainingContractError) as error:
        _manifest(train, validation)

    assert error.value.code == "invalid_contract"


def test_validator_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.jsonl"
    outside.write_text('{"text":"outside"}\n', encoding="utf-8")
    (tmp_path / "train.jsonl").symlink_to(outside)
    validation = _write(tmp_path / "validation.jsonl", [{"text": "validation"}])
    train = (hashlib.sha256(outside.read_bytes()).hexdigest(), 1)

    with pytest.raises(TrainingContractError) as error:
        DatasetValidator(tmp_path).validate(_manifest(train, validation))

    assert error.value.code == "invalid_path"
