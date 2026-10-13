"""Tenant-/owner-bound runtime selection and rollback for Factory specialists."""

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService


class HubSpecialistLifecycle:
    def __init__(self, *, principal: MlInternTrainingPrincipal, registry: MlInternAdapterRegistryService):
        self._principal = principal
        self._registry = registry

    def active_version(self, base_model: str, *, task_kind: str) -> str | None:
        record = self._registry.resolve_active_adapter(
            base_model=base_model, task_kind=task_kind,
            tenant_id=self._principal.tenant_id, owner_subject=self._principal.subject,
        )
        if record is None:
            return None
        if task_kind not in record.task_kinds:
            raise ValueError("specialist_lifecycle_task_kind_mismatch")
        return record.version

    def rollback(self, adapter_id: str, *, task_kind: str) -> str | None:
        record = self._registry.get(
            adapter_id, tenant_id=self._principal.tenant_id, owner_subject=self._principal.subject
        )
        if record is None or record.task_kinds != [task_kind]:
            raise ValueError("specialist_lifecycle_adapter_scope_invalid")
        _deprecated, target = self._registry.rollback(
            adapter_id, tenant_id=self._principal.tenant_id, owner_subject=self._principal.subject,
            expected_version=record.registry_version,
            task_kind=task_kind,
        )
        return target.adapter_id if target is not None else None
