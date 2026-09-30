"""Scoped destination catalog projected from validated workers and models.

``ScopedWorkerModelDestinationCatalog`` joins online, registration-validated
worker runtime targets with the Hub model catalog and exposes only enabled,
authorized destinations within a tenant/project scope.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Mapping, Sequence

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.db_models import AgentInfoDB
from agent.services.source_control_adapter_common import (
    SourceControlProductionAdapterError,
)
from agent.services.source_destination_resolution import (
    DestinationCatalogRecord,
)
from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelHealth,
)
from ananta_contracts.source_control import (
    DestinationDescriptor,
    ProviderLocation,
)


class ScopedWorkerModelDestinationCatalog:
    """Project destinations from validated workers and the Hub model catalog."""

    def __init__(
        self,
        *,
        engine: Engine,
        model_supplier: Callable[[], Sequence[object]],
        clock=time.time,
        cache_ttl_seconds: int = 30,
    ) -> None:
        self._engine = engine
        self._models = model_supplier
        self._clock = clock
        self._ttl = max(1, min(cache_ttl_seconds, 300))
        self._cached_at = 0.0
        self._model_index: dict[tuple[str, str], object] = {}

    def resolve(
        self,
        *,
        worker_id: str,
        runtime_id: str,
        provider_id: str,
        model_id: str,
    ) -> DestinationCatalogRecord | None:
        for record, target in self._records():
            if (
                record.worker_id == worker_id
                and record.runtime_id == runtime_id
                and record.provider_id == provider_id
                and record.model_id == model_id
                and bool(target.get("global_source_access"))
            ):
                return record
        return None

    def get(
        self,
        *,
        tenant_id: str,
        project_id: str,
        destination_id: str,
    ) -> DestinationDescriptor | None:
        for descriptor in self._scoped_descriptors(
            tenant_id, project_id
        ):
            if descriptor.destination_id == destination_id:
                return descriptor
        return None

    def list(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, object],
    ) -> tuple[Sequence[DestinationDescriptor], str | None]:
        if limit < 1 or limit > 200:
            raise SourceControlProductionAdapterError(
                "destination_limit_invalid"
            )
        after = self._decode_destination_cursor(cursor)
        values = sorted(
            self._scoped_descriptors(tenant_id, project_id),
            key=lambda item: item.destination_id,
        )
        selected = [
            item
            for item in values
            if (after is None or item.destination_id > after)
            and (
                not filters.get("provider_id")
                or item.provider_id == str(filters["provider_id"])
            )
            and (
                not filters.get("model_class")
                or item.model_class == str(filters["model_class"])
            )
        ][: limit + 1]
        visible = selected[:limit]
        return (
            tuple(visible),
            (
                base64.urlsafe_b64encode(
                    visible[-1].destination_id.encode("ascii")
                )
                .decode("ascii")
                .rstrip("=")
                if len(selected) > limit and visible
                else None
            ),
        )

    def _scoped_descriptors(
        self, tenant_id: str, project_id: str
    ) -> list[DestinationDescriptor]:
        descriptors: list[DestinationDescriptor] = []
        for record, target in self._records():
            if not self._scope_allowed(
                target, tenant_id=tenant_id, project_id=project_id
            ):
                continue
            descriptors.append(
                DestinationDescriptor.create(
                    worker_id=record.worker_id,
                    worker_kind=record.worker_kind,
                    runtime_id=record.runtime_id,
                    runtime_kind=record.runtime_kind,
                    provider_id=record.provider_id,
                    model_id=record.model_id,
                    model_class=record.model_class,
                    provider_location=record.provider_location,
                    data_residency=record.data_residency,
                )
            )
        return descriptors

    def _records(
        self,
    ) -> list[tuple[DestinationCatalogRecord, Mapping[str, object]]]:
        model_index = self._model_catalog()
        with Session(self._engine) as db:
            workers = list(db.exec(select(AgentInfoDB)).all())
        records: list[
            tuple[DestinationCatalogRecord, Mapping[str, object]]
        ] = []
        for worker in workers:
            if (
                worker.role != "worker"
                or worker.status != "online"
                or not worker.registration_validated
            ):
                continue
            worker_id = str(worker.name or worker.url)
            for raw in list(worker.runtime_targets or []):
                if not isinstance(raw, Mapping):
                    continue
                target = dict(raw)
                runtime_id = str(target.get("runtime_id") or "")
                runtime_kind = str(
                    target.get("runtime_kind")
                    or target.get("kind")
                    or ""
                )
                provider_id = str(target.get("provider_id") or "")
                model_provider_id = str(
                    target.get("model_provider_id") or provider_id
                )
                model_ids = target.get("model_ids")
                if not isinstance(model_ids, list):
                    model_ids = [target.get("model_id")]
                for raw_model_id in model_ids:
                    model_id = str(raw_model_id or "")
                    model = model_index.get(
                        (model_provider_id, model_id)
                    )
                    if (
                        not runtime_id
                        or not runtime_kind
                        or model is None
                    ):
                        continue
                    try:
                        location = ProviderLocation(
                            str(target["provider_location"])
                        )
                        data_residency = str(
                            target["data_residency"]
                        )
                        model_class = str(
                            target.get("model_class")
                            or self._model_class(model)
                        )
                    except (KeyError, ValueError):
                        continue
                    if not data_residency or not model_class:
                        continue
                    records.append(
                        (
                            DestinationCatalogRecord(
                                worker_id=worker_id,
                                worker_kind=str(
                                    target.get("worker_kind")
                                    or worker.role
                                ),
                                runtime_id=runtime_id,
                                runtime_kind=runtime_kind,
                                provider_id=provider_id,
                                model_id=model_id,
                                model_class=model_class,
                                provider_location=location,
                                data_residency=data_residency,
                                enabled=bool(
                                    target.get("enabled", True)
                                ),
                                authorization_status=(
                                    "authorized"
                                    if self._model_available(model)
                                    and bool(
                                        target.get(
                                            "source_access_authorized"
                                        )
                                    )
                                    else "denied"
                                ),
                            ),
                            target,
                        )
                    )
        return [
            (record, target)
            for record, target in records
            if record.enabled
            and record.authorization_status == "authorized"
        ]

    def _model_catalog(self) -> dict[tuple[str, str], object]:
        now = float(self._clock())
        if now - self._cached_at >= self._ttl:
            self._model_index = {
                (
                    str(
                        getattr(item, "provider_id", None)
                        or (
                            item.get("provider_id")
                            if isinstance(item, Mapping)
                            else ""
                        )
                    ),
                    str(
                        getattr(item, "model_id", None)
                        or (
                            item.get("model_id")
                            if isinstance(item, Mapping)
                            else ""
                        )
                    ),
                ): item
                for item in self._models()
            }
            self._cached_at = now
        return self._model_index

    @staticmethod
    def _model_available(model: object) -> bool:
        availability = getattr(model, "availability", None)
        health = getattr(model, "health", None)
        return (
            str(getattr(availability, "value", availability))
            == ModelAvailability.AVAILABLE.value
            and str(getattr(health, "value", health))
            != ModelHealth.UNAVAILABLE.value
        )

    @staticmethod
    def _model_class(model: object) -> str:
        capabilities = getattr(model, "capabilities", ())
        return next(
            (
                str(item).removeprefix("class:")
                for item in capabilities
                if str(item).startswith("class:")
            ),
            "general",
        )

    @staticmethod
    def _scope_allowed(
        target: Mapping[str, object],
        *,
        tenant_id: str,
        project_id: str,
    ) -> bool:
        if target.get("global_source_access") is True:
            return True
        if (
            target.get("tenant_id") == tenant_id
            and target.get("project_id") == project_id
        ):
            return True
        scopes = target.get("source_access_scopes")
        return isinstance(scopes, list) and any(
            isinstance(scope, Mapping)
            and scope.get("tenant_id") == tenant_id
            and scope.get("project_id") == project_id
            for scope in scopes
        )

    @staticmethod
    def _decode_destination_cursor(cursor: str | None) -> str | None:
        if cursor is None:
            return None
        try:
            value = cursor + "=" * (-len(cursor) % 4)
            return base64.urlsafe_b64decode(value).decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceControlProductionAdapterError(
                "destination_cursor_invalid"
            ) from exc


__all__ = ["ScopedWorkerModelDestinationCatalog"]
