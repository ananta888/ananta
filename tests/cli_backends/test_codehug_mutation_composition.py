from __future__ import annotations

import pytest

from agent.services.codehug_mutation_composition import (
    CodeHugDestinationBinding,
    CodeHugMutationCompositionError,
    CodeHugMutationCompositionService,
    CodeHugRevisionBinding,
    RegisteredCodeHugMutationIntent,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
)


class _Catalog:
    def __init__(self, value) -> None:
        self.value = value

    def resolve(self, **kwargs):
        return self.value


class _Approvals:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    def consume(self, **kwargs):
        return self.allowed


class _Authorization:
    def __init__(self) -> None:
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        return {
            "schema": "ananta.codehug.mutation-result.v1",
            "status": "accepted",
            "operation_id": "operation-example",
            "binding_digest": "e" * 64,
        }


def _service(*, approval: bool = True):
    intent = RegisteredCodeHugMutationIntent(
        intent_id="intent-example",
        tenant_id="tenant-example",
        project_id="project-example",
        actor_id="actor-example",
        job_id="job-example",
        tool_id="tool-example",
        operation=GrantOperation.TOOL_CONTEXT,
        source_revision_id="revision-example",
        destination_id="destination-example",
        transformation=GrantTransformation.REDACTED,
        purpose="project-repair",
        approval_id="approval-example",
        assignment_id="assignment-example",
        lease_id="lease-example",
        payload_reference_id="payload-example",
        source_access_grant_id="grant-example",
        source_access_grant_digest="f" * 64,
    )
    revision = CodeHugRevisionBinding(
        source_revision_id="revision-example",
        revision_digest="a" * 64,
        policy_digest="b" * 64,
        content_manifest_id="manifest-example",
        content_manifest_digest="c" * 64,
        source_access_grant_id="grant-example",
        source_access_grant_digest="f" * 64,
    )
    destination = CodeHugDestinationBinding(
        destination_id="destination-example",
        destination_digest="d" * 64,
    )
    authorization = _Authorization()
    return (
        CodeHugMutationCompositionService(
            intents=_Catalog(intent),
            revisions=_Catalog(revision),
            destinations=_Catalog(destination),
            approvals=_Approvals(approval),
            authorization=authorization,
        ),
        authorization,
    )


def test_server_resolves_all_mutation_bindings_before_authorization() -> None:
    service, authorization = _service()
    result = service.execute(
        tenant_id="tenant-example",
        project_id="project-example",
        actor_id="actor-example",
        mutation_intent_id="intent-example",
    )

    assert result["status"] == "accepted"
    command = authorization.commands[0]
    assert command.expected_revision_digest == "a" * 64
    assert command.expected_policy_digest == "b" * 64
    assert command.destination_digest == "d" * 64
    assert not hasattr(command, "write_armed")


def test_denied_approval_never_reaches_authorization_or_executor() -> None:
    service, authorization = _service(approval=False)

    with pytest.raises(
        CodeHugMutationCompositionError,
        match="approval_required",
    ):
        service.execute(
            tenant_id="tenant-example",
            project_id="project-example",
            actor_id="actor-example",
            mutation_intent_id="intent-example",
        )

    assert authorization.commands == []
