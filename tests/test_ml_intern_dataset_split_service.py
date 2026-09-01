from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from agent.services.ml_intern_dataset_catalog_service import MlInternDatasetCatalogService
from agent.services.ml_intern_dataset_split_service import (
    DatasetSplitError,
    MlInternDatasetSplitService,
)


def _catalog(tmp_path: Path) -> MlInternDatasetCatalogService:
    ids = (f"ds-{index:032x}" for index in itertools.count(1))
    return MlInternDatasetCatalogService(
        storage_root=tmp_path / "catalog",
        id_factory=lambda: next(ids),
    )


def _records(count: int, *, prefix: str = "R") -> list[dict]:
    return [
        {"instruction": f"{prefix} instruction {index}", "output": f"{prefix} output {index}"}
        for index in range(count)
    ]


def _semantic_pairs(catalog, dataset_id: str, partition: str) -> list[tuple[str, str]]:
    rows = []
    with catalog.open_partition(
        tenant_id="t", principal_id="p", dataset_id=dataset_id, partition=partition
    ) as handle:
        for line in handle:
            record = json.loads(line)
            rows.append((record.get("instruction"), record.get("output")))
    return rows


def test_split_is_reproducible_seeded_non_overlapping_and_invalidates_validation(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    created = catalog.create_from_records(
        tenant_id="t", principal_id="p", records=_records(30), name="Split"
    )
    catalog.validate_dataset(tenant_id="t", principal_id="p", dataset_id=created["dataset_id"])
    service = MlInternDatasetSplitService(catalog)

    first = service.split(
        tenant_id="t",
        principal_id="p",
        dataset_id=created["dataset_id"],
        validation_ratio=0.2,
        seed=42,
    )
    train_first = set(_semantic_pairs(catalog, created["dataset_id"], "train"))
    validation_first = set(_semantic_pairs(catalog, created["dataset_id"], "validation"))
    assert train_first
    assert validation_first
    assert train_first.isdisjoint(validation_first)
    assert first["manifest"]["train_record_count"] + first["manifest"]["validation_record_count"] == 30
    assert first["dataset"]["validation"]["status"] == "pending"
    assert first["dataset"]["validation"]["trainable"] is False

    second = service.split(
        tenant_id="t",
        principal_id="p",
        dataset_id=created["dataset_id"],
        validation_ratio=0.2,
        seed=42,
    )
    assert second["manifest"] == first["manifest"]
    assert set(_semantic_pairs(catalog, created["dataset_id"], "train")) == train_first
    assert set(_semantic_pairs(catalog, created["dataset_id"], "validation")) == validation_first

    different = service.split(
        tenant_id="t",
        principal_id="p",
        dataset_id=created["dataset_id"],
        validation_ratio=0.2,
        seed=7,
    )
    assert different["manifest"]["validation_sha256"] != first["manifest"]["validation_sha256"]


def test_normalized_duplicate_groups_stay_together(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    records = _records(10)
    records.extend(
        [
            {
                "instruction": "Duplicate prompt",
                "output": "Duplicate output",
                "source_ref": f"source-{index}",
            }
            for index in range(4)
        ]
    )
    created = catalog.create_from_records(
        tenant_id="t", principal_id="p", records=records, name="Grouped"
    )
    result = MlInternDatasetSplitService(catalog).split(
        tenant_id="t", principal_id="p", dataset_id=created["dataset_id"], seed=11
    )
    train = _semantic_pairs(catalog, created["dataset_id"], "train")
    validation = _semantic_pairs(catalog, created["dataset_id"], "validation")
    duplicate = ("Duplicate prompt", "Duplicate output")
    assert (train.count(duplicate), validation.count(duplicate)) in {(4, 0), (0, 4)}
    assert result["manifest"]["duplicate_record_count"] >= 3


def test_explicit_lineage_roots_stay_in_one_partition(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    records = _records(8)
    records.extend(
        [
            {
                "instruction": f"Workbook revision {index}",
                "output": f"Action revision {index}",
                "lineage_root_id": "workbook-one",
            }
            for index in range(4)
        ]
    )
    created = catalog.create_from_records(
        tenant_id="t",
        principal_id="p",
        records=records,
        name="Lineage grouped",
    )
    MlInternDatasetSplitService(catalog).split(
        tenant_id="t",
        principal_id="p",
        dataset_id=created["dataset_id"],
        seed=17,
    )
    locations = []
    for partition in ("train", "validation"):
        with catalog.open_partition(
            tenant_id="t",
            principal_id="p",
            dataset_id=created["dataset_id"],
            partition=partition,
        ) as handle:
            locations.extend(
                partition
                for line in handle
                if json.loads(line).get("lineage_root_id") == "workbook-one"
            )
    assert len(locations) == 4
    assert len(set(locations)) == 1


def test_small_or_single_group_dataset_has_clear_failure(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    small = catalog.create_from_records(
        tenant_id="t", principal_id="p", records=_records(3), name="Small"
    )
    with pytest.raises(DatasetSplitError) as exc:
        MlInternDatasetSplitService(catalog).split(
            tenant_id="t", principal_id="p", dataset_id=small["dataset_id"]
        )
    assert exc.value.reason_code == "dataset_too_small_for_split"


def test_external_validation_rejects_identical_and_semantic_overlap(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    train = catalog.create_from_records(
        tenant_id="t", principal_id="p", records=_records(5, prefix="Train"), name="Train"
    )
    validation = catalog.create_from_records(
        tenant_id="t",
        principal_id="p",
        records=_records(4, prefix="Validation") + [_records(1, prefix="Train")[0]],
        name="Validation",
    )
    service = MlInternDatasetSplitService(catalog)
    identical = service.validate_external_validation(
        tenant_id="t",
        principal_id="p",
        train_dataset_id=train["dataset_id"],
        validation_dataset_id=train["dataset_id"],
    )
    assert identical["ok"] is False
    assert identical["semantic_overlap_count"] == 5

    overlapping = service.validate_external_validation(
        tenant_id="t",
        principal_id="p",
        train_dataset_id=train["dataset_id"],
        validation_dataset_id=validation["dataset_id"],
    )
    assert overlapping["ok"] is False
    assert overlapping["semantic_overlap_count"] == 1
    assert "dataset_path" not in overlapping["train"]
    assert str(tmp_path) not in str(overlapping)


def test_external_validation_dataset_is_atomically_attached_and_pair_validated(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    train = catalog.create_from_records(
        tenant_id="t",
        principal_id="p",
        records=_records(6, prefix="Train"),
        name="Train",
    )
    validation = catalog.create_from_records(
        tenant_id="t",
        principal_id="p",
        records=_records(4, prefix="Validation"),
        name="Validation",
    )
    service = MlInternDatasetSplitService(catalog)

    result = service.attach_external_validation(
        tenant_id="t",
        principal_id="p",
        train_dataset_id=train["dataset_id"],
        validation_dataset_id=validation["dataset_id"],
    )

    assert result["manifest"]["algorithm_version"] == "external-validation-dataset-v1"
    assert result["manifest"]["train_record_count"] == 6
    assert result["manifest"]["validation_record_count"] == 4
    assert result["pair"]["ok"] is True
    assert set(_semantic_pairs(catalog, train["dataset_id"], "train")).isdisjoint(
        _semantic_pairs(catalog, train["dataset_id"], "validation")
    )
    report = catalog.validate_dataset(
        tenant_id="t",
        principal_id="p",
        dataset_id=train["dataset_id"],
    )
    assert report["ok"] is True


def test_external_validation_attach_rejects_same_or_overlapping_dataset(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    train_records = _records(5, prefix="Train")
    train = catalog.create_from_records(
        tenant_id="t", principal_id="p", records=train_records, name="Train"
    )
    overlap = catalog.create_from_records(
        tenant_id="t",
        principal_id="p",
        records=_records(3, prefix="Validation") + [train_records[0]],
        name="Overlap",
    )
    service = MlInternDatasetSplitService(catalog)

    with pytest.raises(DatasetSplitError) as same:
        service.attach_external_validation(
            tenant_id="t",
            principal_id="p",
            train_dataset_id=train["dataset_id"],
            validation_dataset_id=train["dataset_id"],
        )
    assert same.value.reason_code == "validation_dataset_same_as_train"

    with pytest.raises(DatasetSplitError) as invalid:
        service.attach_external_validation(
            tenant_id="t",
            principal_id="p",
            train_dataset_id=train["dataset_id"],
            validation_dataset_id=overlap["dataset_id"],
        )
    assert invalid.value.reason_code == "external_validation_pair_invalid"


def test_external_validation_preserves_early_catalog_error(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    service = MlInternDatasetSplitService(catalog)

    with pytest.raises(DatasetSplitError) as missing:
        service.attach_external_validation(
            tenant_id="t",
            principal_id="p",
            train_dataset_id="missing-train",
            validation_dataset_id="missing-validation",
        )

    assert missing.value.reason_code == "dataset_not_found"
