"""Composed real Hub services; dry-run observations never claim a trained model."""

import pytest

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_dataset_repository_bridge_service import MlInternDatasetRepositoryBridgeService
from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from agent.services.specialist_factory_composition import build_hub_specialist_factory
from tests.test_ml_intern_dataset_catalog_service import _catalog
from tests.test_ml_intern_training_control_service import FakeTaskQueue, InlineExecutor
from tests.test_specialist_model_factory import GATE
from tests.test_specialist_model_factory_e2e import POLICY, TINY, gate_examples, plan_for
from tests.test_specialist_scoped_lifecycle import _approved


def test_composed_factory_real_hub_dry_run_cannot_manufacture_model_or_promotion(app, tmp_path, monkeypatch):
    del app
    principal = MlInternTrainingPrincipal("composition-tenant", "composition-owner")
    repo = MlInternTrainingRepository()
    catalog = _catalog(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    execution = tmp_path / "execution"
    bridge = MlInternDatasetRepositoryBridgeService(
        execution_root=execution, catalog=catalog, repository=repo, max_dataset_bytes=1024 * 1024,
    )
    registry = MlInternAdapterRegistryService(tmp_path / "registry.json")
    queue = FakeTaskQueue()
    monkeypatch.setattr("agent.services.ml_intern_training_control_service.get_task_queue_service", lambda: queue)
    control = MlInternTrainingControlService(
        {"enabled": True, "artifact_root": str(artifacts)}, repository=repo, executor=InlineExecutor()
    )
    factory = build_hub_specialist_factory(
        principal=principal, contract=GATE, control=control, repository=repo, catalog=catalog,
        bridge=bridge, registry=registry, artifact_root=artifacts, dataset_root=execution,
        model_catalog={TINY: {"snapshot_hash": "b" * 64}}, policy=POLICY,
        benchmark_version="code-risk-bench-v1", backend="mock", mode="dry_run",
    )
    examples = gate_examples(45)
    with pytest.raises(ValueError, match="specialist_training_registry_binding_invalid"):
        factory.run_cycle(examples, version="v1", plan_candidates=plan_for(examples, 11), seed=11)
    assert len(queue.tasks) == 1
    assert len(repo.list_datasets(principal, limit=10, offset=0)) == 1
    assert registry.list_adapters(tenant_id=principal.tenant_id, owner_subject=principal.subject) == []
    foreign = MlInternTrainingPrincipal("foreign", principal.subject)
    _approved(registry, "foreign-active", "v9", foreign, factory.task_kind)
    assert factory.active_version("base") is None
    with pytest.raises(ValueError, match="specialist_lifecycle_adapter_scope_invalid"):
        factory.rollback("foreign-active")
