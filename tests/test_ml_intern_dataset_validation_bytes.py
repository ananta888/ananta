"""Catalogue validation cannot bless changed physical dataset bytes."""

from pathlib import Path

import pytest

from agent.services.ml_intern_dataset_catalog_service import DatasetCatalogError
from tests.test_ml_intern_dataset_catalog_service import _catalog, _records


def setup_catalog(tmp_path):
    catalog = _catalog(tmp_path)
    summary = catalog.create_from_records(
        tenant_id="tenant", principal_id="owner", records=_records(), name="Synthetic immutable validation"
    )
    with catalog.open_partition(
        tenant_id="tenant", principal_id="owner", dataset_id=summary["dataset_id"], partition="train"
    ) as handle:
        path = Path(handle.name)
    return catalog, summary["dataset_id"], path


def test_bytes_changed_before_validation_do_not_receive_catalog_approval(tmp_path):
    catalog, dataset_id, path = setup_catalog(tmp_path)
    path.write_bytes(path.read_bytes().replace(b"Prompt", b"Changed", 1))
    with pytest.raises(DatasetCatalogError) as error:
        catalog.validate_dataset(tenant_id="tenant", principal_id="owner", dataset_id=dataset_id)
    assert error.value.reason_code == "dataset_partition_hash_mismatch"


def test_bytes_changed_during_validation_are_rejected_before_report_commit(tmp_path, monkeypatch):
    catalog, dataset_id, path = setup_catalog(tmp_path)
    original = catalog._validator.validate

    def validate(*args, **kwargs):
        report = original(*args, **kwargs)
        path.write_bytes(path.read_bytes().replace(b"Prompt", b"Changed", 1))
        return report

    monkeypatch.setattr(catalog._validator, "validate", validate)
    with pytest.raises(DatasetCatalogError) as error:
        catalog.validate_dataset(tenant_id="tenant", principal_id="owner", dataset_id=dataset_id)
    assert error.value.reason_code == "dataset_changed_during_validation"
