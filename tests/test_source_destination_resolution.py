from __future__ import annotations

import pytest

from agent.services.source_destination_resolution import (
    DestinationCatalogRecord,
    DestinationResolutionError,
    DestinationSelection,
    SourceDestinationResolutionService,
)
from ananta_contracts.source_control import ProviderLocation


class _Catalog:
    def __init__(self, records: list[DestinationCatalogRecord]) -> None:
        self.records = records

    def resolve(self, **coordinates) -> DestinationCatalogRecord | None:
        for record in self.records:
            if all(getattr(record, key) == value for key, value in coordinates.items()):
                return record
        return None


def _record(
    *,
    model_id: str = "claude-model-a",
    enabled: bool = True,
    authorization_status: str = "authorized",
) -> DestinationCatalogRecord:
    return DestinationCatalogRecord(
        worker_id="worker-cloud-example",
        worker_kind="llm",
        runtime_id="runtime-cloud-example",
        runtime_kind="remote_api",
        provider_id="anthropic",
        model_id=model_id,
        model_class="anthropic_claude",
        provider_location=ProviderLocation.EXTERNAL_REGION,
        data_residency="region-example",
        enabled=enabled,
        authorization_status=authorization_status,
    )


def _selection(model_id: str = "claude-model-a") -> DestinationSelection:
    return DestinationSelection(
        worker_id="worker-cloud-example",
        runtime_id="runtime-cloud-example",
        provider_id="anthropic",
        model_id=model_id,
    )


def test_model_change_produces_a_different_destination_identity() -> None:
    service = SourceDestinationResolutionService(
        _Catalog([_record(), _record(model_id="claude-model-b")])
    )

    first = service.resolve(_selection())
    second = service.resolve(_selection("claude-model-b"))

    assert first.descriptor.destination_id != second.descriptor.destination_id
    assert first.destination_digest != second.destination_digest
    assert first.descriptor.model_class == "anthropic_claude"


def test_destination_change_between_preview_and_dispatch_is_blocked() -> None:
    service = SourceDestinationResolutionService(
        _Catalog([_record(), _record(model_id="claude-model-b")])
    )
    preview = service.resolve(_selection())

    with pytest.raises(
        DestinationResolutionError,
        match="destination_changed_after_preview",
    ):
        service.verify_dispatch_binding(
            preview_destination_digest=preview.destination_digest,
            dispatch_selection=_selection("claude-model-b"),
        )


@pytest.mark.parametrize(
    "record",
    (
        _record(enabled=False),
        _record(authorization_status="revoked"),
    ),
)
def test_disabled_or_revoked_destination_is_fail_closed(
    record: DestinationCatalogRecord,
) -> None:
    service = SourceDestinationResolutionService(_Catalog([record]))

    with pytest.raises(DestinationResolutionError):
        service.resolve(_selection())


def test_client_cannot_supply_authoritative_destination_metadata() -> None:
    fields = set(DestinationSelection.__dataclass_fields__)

    assert fields == {"worker_id", "runtime_id", "provider_id", "model_id"}
    assert "provider_location" not in fields
    assert "model_class" not in fields
    assert "data_residency" not in fields
