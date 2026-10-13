"""Synthetic registry lifecycle fixtures; no productive release evidence."""

import pytest

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.specialist_model_factory import SpecialistModelFactory
from agent.services.specialist_scoped_lifecycle import HubSpecialistLifecycle
from tests.test_specialist_model_factory import GATE
from tests.test_specialist_model_factory_e2e import POLICY


def _approved(registry, adapter_id, version, principal, task_kind):
    scope = {"tenant_id": principal.tenant_id, "owner_subject": principal.subject}
    registry.register_trained(
        adapter_id=adapter_id, display_name="Synthetic test adapter", version=version, base_model="base",
        method="lora", artifact_paths={"adapter_storage_ref": "synthetic-fixture/adapter"}, config_hash="a" * 64, artifact_sha256="b" * 64,
        task_kinds=[task_kind], **scope,
    )
    registry.set_eval_report(adapter_id, eval_report_ref="synthetic-evaluation", eval_score=1.0, **scope)
    registry.approve(adapter_id, approved_by="automated-test-fixture", reason="synthetic fixture only", **scope)


def test_factory_selection_and_rollback_preserve_tenant_owner_and_specialist(tmp_path):
    registry = MlInternAdapterRegistryService(tmp_path / "registry.json")
    principal = MlInternTrainingPrincipal("tenant-one", "owner-one")
    foreign = MlInternTrainingPrincipal("tenant-two", "owner-one")
    other_owner = MlInternTrainingPrincipal("tenant-one", "owner-two")
    task_kind = f"specialist:{GATE.specialist_id}"
    _approved(registry, "version-one", "v1", principal, task_kind)
    _approved(registry, "version-two", "v2", principal, task_kind)
    _approved(registry, "foreign", "v3", foreign, task_kind)
    _approved(registry, "other-owner", "v4", other_owner, task_kind)
    _approved(registry, "other-specialist", "v5", principal, "specialist:other")
    lifecycle = HubSpecialistLifecycle(principal=principal, registry=registry)
    factory = SpecialistModelFactory(
        GATE, trainer=None, partition_predictor=object(), registry=registry, policy=POLICY,
        benchmark_version="bench-v1", candidate_registration=object(), lifecycle=lifecycle,
    )
    assert factory.active_version("base") == "v2"
    assert factory.rollback("version-two") == "version-one"
    assert factory.active_version("base") == "v1"
    assert factory.rollback("version-one") is None
    assert factory.active_version("base") is None
    for adapter_id in ("foreign", "other-owner", "other-specialist"):
        with pytest.raises(ValueError, match="specialist_lifecycle_adapter_scope_invalid"):
            factory.rollback(adapter_id)
    assert registry.get("other-specialist", tenant_id="tenant-one", owner_subject="owner-one").status == "approved"


def test_real_factory_lifecycle_never_falls_back_to_unscoped_registry(tmp_path):
    factory = SpecialistModelFactory(
        GATE, trainer=None, partition_predictor=object(),
        registry=MlInternAdapterRegistryService(tmp_path / "registry.json"), policy=POLICY,
        benchmark_version="bench-v1", candidate_registration=object(),
    )
    with pytest.raises(ValueError, match="specialist_scoped_lifecycle_required"):
        factory.active_version("base")
    with pytest.raises(ValueError, match="specialist_scoped_lifecycle_required"):
        factory.rollback("some-adapter")
