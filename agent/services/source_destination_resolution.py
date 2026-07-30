"""Hub-side resolution and binding of real execution destinations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from ananta_contracts.source_control import (
    DestinationDescriptor,
    ProviderLocation,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")


class DestinationResolutionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class DestinationSelection:
    worker_id: str
    runtime_id: str
    provider_id: str
    model_id: str

    def __post_init__(self) -> None:
        for name in ("worker_id", "runtime_id", "provider_id", "model_id"):
            if not _IDENTIFIER.fullmatch(str(getattr(self, name) or "")):
                raise DestinationResolutionError(f"{name}_invalid")


@dataclass(frozen=True)
class DestinationCatalogRecord:
    worker_id: str
    worker_kind: str
    runtime_id: str
    runtime_kind: str
    provider_id: str
    model_id: str
    model_class: str
    provider_location: ProviderLocation
    data_residency: str
    enabled: bool
    authorization_status: str


class DestinationCatalogPort(Protocol):
    def resolve(
        self,
        *,
        worker_id: str,
        runtime_id: str,
        provider_id: str,
        model_id: str,
    ) -> DestinationCatalogRecord | None: ...


@dataclass(frozen=True)
class ResolvedDestination:
    descriptor: DestinationDescriptor
    destination_digest: str


class SourceDestinationResolutionService:
    """Resolve client selections exclusively through the Hub catalog."""

    def __init__(self, catalog: DestinationCatalogPort) -> None:
        self._catalog = catalog

    def resolve(self, selection: DestinationSelection) -> ResolvedDestination:
        record = self._catalog.resolve(
            worker_id=selection.worker_id,
            runtime_id=selection.runtime_id,
            provider_id=selection.provider_id,
            model_id=selection.model_id,
        )
        if record is None:
            raise DestinationResolutionError("destination_not_found")
        if not record.enabled:
            raise DestinationResolutionError("destination_disabled")
        if record.authorization_status != "authorized":
            raise DestinationResolutionError("destination_authorization_required")
        if (
            record.worker_id != selection.worker_id
            or record.runtime_id != selection.runtime_id
            or record.provider_id != selection.provider_id
            or record.model_id != selection.model_id
        ):
            raise DestinationResolutionError("destination_catalog_mismatch")
        descriptor = DestinationDescriptor.create(
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
        wire = descriptor.to_wire()
        digest = hashlib.sha256(
            json.dumps(
                wire,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        return ResolvedDestination(
            descriptor=descriptor,
            destination_digest=digest,
        )

    def verify_dispatch_binding(
        self,
        *,
        preview_destination_digest: str,
        dispatch_selection: DestinationSelection,
    ) -> ResolvedDestination:
        resolved = self.resolve(dispatch_selection)
        if resolved.destination_digest != preview_destination_digest:
            raise DestinationResolutionError("destination_changed_after_preview")
        return resolved
