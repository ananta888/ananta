"""Repeated catalogue projection must preserve admitted dataset identity."""

from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.services.ml_intern_dataset_repository_bridge_service import MlInternDatasetRepositoryBridgeService
from tests.test_ml_intern_dataset_catalog_service import _catalog, _records
from tests.test_ml_intern_training_control_service import _principal


def test_unchanged_catalog_sync_preserves_sql_version_and_partition_hashes(app, tmp_path):
    del app
    principal = _principal()
    catalog = _catalog(tmp_path)
    summary = catalog.create_from_records(
        tenant_id=principal.tenant_id,
        principal_id=principal.subject,
        records=_records(),
        name="Synthetic bridge replay",
    )
    repository = MlInternTrainingRepository()
    bridge = MlInternDatasetRepositoryBridgeService(
        execution_root=tmp_path / "execution", catalog=catalog, repository=repository, max_dataset_bytes=1024 * 1024
    )
    first = bridge.sync(principal, summary)
    initial = repository.get_dataset(principal, first["id"])
    again = bridge.sync(principal, summary)
    after = repository.get_dataset(principal, first["id"])
    assert again["id"] == first["id"]
    assert after.version == initial.version
    assert after.dataset_metadata == initial.dataset_metadata
    assert after.train_storage_ref == initial.train_storage_ref


def test_changed_metadata_still_updates_sql_projection(app, tmp_path):
    del app
    principal = _principal()
    catalog = _catalog(tmp_path)
    summary = catalog.create_from_records(
        tenant_id=principal.tenant_id,
        principal_id=principal.subject,
        records=_records(),
        name="Synthetic bridge metadata",
    )
    repository = MlInternTrainingRepository()
    bridge = MlInternDatasetRepositoryBridgeService(
        execution_root=tmp_path / "execution", catalog=catalog, repository=repository, max_dataset_bytes=1024 * 1024
    )
    first = bridge.sync(principal, summary)
    before = repository.get_dataset(principal, first["id"])
    changed = bridge.sync(principal, summary, metadata={"purpose": "specialist-decision"})
    after = repository.get_dataset(principal, changed["id"])
    assert after.version > before.version
    assert after.dataset_metadata["purpose"] == "specialist-decision"
