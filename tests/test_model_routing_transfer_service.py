from __future__ import annotations

import pytest

from agent.services.model_routing_transfer_service import (
    ModelRoutingConfirmationError,
    ModelRoutingTransferService,
)
from agent.services.model_selection_service import (
    InMemoryModelRoutingConfigurationRepository,
    ModelConsumerRegistry,
    ModelRoutingAssignmentService,
)
from ananta_contracts.model_selection import (
    ModelAssignment,
    ModelRoutingConfiguration,
    ModelRoutingImportCommand,
)


def _service(initial: ModelRoutingConfiguration | None = None):
    assignments = ModelRoutingAssignmentService(
        repository=InMemoryModelRoutingConfigurationRepository(initial),
        consumers=ModelConsumerRegistry.defaults(),
        known_profile_ids=("local", "cloud"),
    )
    return ModelRoutingTransferService(assignments)


def _command(*, expected_revision: int = 0, digest: str | None = None):
    return ModelRoutingImportCommand(
        expected_revision=expected_revision,
        confirmation_digest=digest,
        configuration=ModelRoutingConfiguration(
            revision=91,
            assignments=(ModelAssignment(
                consumer_id="task.coding",
                scope="global",
                mode="profile",
                profile_id="local",
            ),),
        ),
    )


def test_import_requires_exact_preview_digest_and_applies_atomically():
    service = _service()
    preview = service.preview(_command())
    assert preview.applicable is True
    assert preview.source_revision == 91
    assert preview.diff.added_assignment_keys == ("task.coding@global:global",)

    with pytest.raises(ModelRoutingConfirmationError):
        service.apply(_command())

    updated = service.apply(_command(digest=preview.confirmation_digest))
    assert updated.revision == 1
    assert updated.assignments[0].profile_id == "local"


def test_import_preview_reports_revision_conflict_without_mutation():
    current = ModelRoutingConfiguration(revision=4)
    service = _service(current)
    preview = service.preview(_command(expected_revision=3))
    assert preview.applicable is False
    assert preview.current_revision == 4
    assert preview.issues[0].reason_code == "model_routing_revision_conflict"
    assert service.export().configuration == current


def test_export_contract_contains_only_closed_routing_configuration():
    bundle = _service().export()
    wire = bundle.model_dump(mode="json", by_alias=True)
    assert set(wire) == {"schema", "configuration"}
    assert "url" not in str(wire).lower()
    assert "token" not in str(wire).lower()
