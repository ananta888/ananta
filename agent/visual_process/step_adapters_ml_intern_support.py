"""Shared ports and Hub context resolution for ML-Intern VP step adapters.

The dataset and training adapters both resolve trusted Hub configuration and
a tenant-scoped principal from the execution context.  Keeping these seams in
one small module lets both adapters depend on narrow Protocols instead of on
each other's concrete services.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol


class _MlInternTrainingControlPort(Protocol):
    def create_job(
        self,
        principal: Any,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]: ...


class _LegacyDatasetImportPort(Protocol):
    def import_relative_path(self, principal: Any, relative_path: str) -> str: ...


class _DatasetCatalogBuildPort(Protocol):
    def create_from_records(
        self,
        principal: Any,
        records: list[dict[str, Any]],
        *,
        name: str,
        dataset_format: str,
        validation_ratio: float,
        split_seed: int,
        idempotency_key: str,
        metadata: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def get_dataset(self, principal: Any, dataset_id: str) -> Mapping[str, Any]: ...


ControlFactory = Callable[[Mapping[str, Any]], _MlInternTrainingControlPort]
LegacyDatasetImportFactory = Callable[[Mapping[str, Any]], _LegacyDatasetImportPort]
DatasetCatalogBuildFactory = Callable[[Mapping[str, Any]], _DatasetCatalogBuildPort]


class MlInternVisualProcessAdapterError(ValueError):
    """Stable, content-free VP adapter rejection."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _ml_intern_training_config(context: Mapping[str, Any]) -> dict[str, Any]:
    """Merge trusted Hub configuration with an explicit execution context."""

    config: dict[str, Any] = {}
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            agent_config = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
            config.update(dict(agent_config.get("ml_intern_training") or {}))
            if "lora_runtime" in agent_config:
                config["lora_runtime"] = dict(agent_config.get("lora_runtime") or {})
    except RuntimeError:
        pass
    context_config = context.get("ml_intern_training")
    if isinstance(context_config, Mapping):
        config.update(dict(context_config))
    return config


def _ml_intern_training_principal(context: Mapping[str, Any], principal_type: type[Any]) -> Any:
    """Resolve the tenant-scoped principal shared by VP training adapters."""

    identity: Any = context.get("ml_intern_training_principal") or context.get("principal") or {}
    if isinstance(identity, Mapping):
        subject = str(
            identity.get("subject")
            or identity.get("sub")
            or identity.get("username")
            or context.get("subject")
            or "hub-admin"
        ).strip()
        tenant = str(
            identity.get("tenant_id") or identity.get("tenant") or context.get("tenant_id") or subject
        ).strip()
    else:
        subject = str(getattr(identity, "subject", None) or context.get("subject") or "hub-admin").strip()
        tenant = str(getattr(identity, "tenant_id", None) or context.get("tenant_id") or subject).strip()
    return principal_type(tenant_id=tenant, subject=subject)
