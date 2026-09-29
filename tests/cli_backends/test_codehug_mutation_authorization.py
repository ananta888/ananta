from __future__ import annotations

import pytest

from agent.services.codehug_mutation_authorization import (
    CodeHugToolAuthorization,
    CodeHugMutationAuthorizationError,
    CodeHugMutationAuthorizationService,
    CodeHugMutationCommand,
)
from agent.services.effective_source_access_service import (
    EffectiveSourceAccessDecision,
)
from agent.services.source_access_enforcement import (
    AuthorizedSourceDispatch,
    DelegatedSourceEnforcementManifest,
    SourceAccessDecision,
)
from ananta_contracts.source_control import GrantOperation, GrantTransformation


class _Tools:
    def resolve(self, *, tool_id):
        return CodeHugToolAuthorization(
            tool_id=tool_id,
            operation=GrantOperation.TOOL_CONTEXT,
            mutating=True,
            enabled=True,
        )


class _Access:
    def verify_dispatch(self, **kwargs):
        return EffectiveSourceAccessDecision(
            schema="ananta.source-control.access-decision.v1",
            source_revision_id=kwargs["source_revision_id"],
            revision_digest=kwargs["expected_revision_digest"],
            destination_id=kwargs["destination_id"],
            operation=kwargs["operation"].value,
            transformation=kwargs["transformation"].value,
            purpose=kwargs["purpose"],
            decision="allow",
            reason_codes=("explicit_allow",),
            matched_rule_path=("rule-example",),
            default_applied=False,
            approval_requirement=None,
            policy_digest=kwargs["expected_policy_digest"],
        )


class _Grants:
    def __init__(self, *, allowed=True) -> None:
        self.allowed = allowed

    def authorize(self, request):
        if not self.allowed:
            error = ValueError("source_access_grant_missing")
            error.reason_code = "source_access_grant_missing"
            raise error
        manifest = DelegatedSourceEnforcementManifest(
            schema="ananta.source-control.enforcement-manifest.v1",
            authority="hub",
            source_revision_id=request.source_revision_id,
            destination_id=request.destination_id,
            destination_digest=request.destination_digest,
            source_access_grant_id="grant-example",
            operation=request.operation.value,
            transformation=request.transformation.value,
            purpose=request.purpose,
            policy_version=request.policy_version,
            content_manifest_id=request.manifest_id,
            content_manifest_digest=request.manifest_digest,
            assignment_id=request.assignment_id,
            lease_id=request.lease_id,
            binding_digest="e" * 64,
            signature="signature-example",
        )
        return AuthorizedSourceDispatch(
            decision=SourceAccessDecision(
                allowed=True,
                reason_code="grant_match",
                binding_digest="e" * 64,
                grant_id="grant-example",
            ),
            manifest=manifest,
        )


class _Executor:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "completed", "operation_id": "operation-example"}


class _Audit:
    def __init__(self) -> None:
        self.events = []

    def record(self, **event):
        self.events.append(event)


def _command() -> CodeHugMutationCommand:
    return CodeHugMutationCommand(
        tenant_id="tenant-example",
        project_id="project-example",
        actor_id="actor-example",
        source_revision_id="revision-example",
        destination_id="destination-example",
        destination_digest="a" * 64,
        expected_revision_digest="b" * 64,
        expected_policy_digest="c" * 64,
        tool_id="tool-example",
        transformation=GrantTransformation.REDACTED,
        purpose="project-repair",
        content_manifest_id="manifest-example",
        content_manifest_digest="d" * 64,
        assignment_id="assignment-example",
        lease_id="lease-example",
        payload_reference_id="payload-example",
        source_access_grant_id="grant-example",
        source_access_grant_digest="e" * 64,
    )


def test_server_policy_and_grant_precede_mutation_execution() -> None:
    executor = _Executor()
    audit = _Audit()
    service = CodeHugMutationAuthorizationService(
        tools=_Tools(),
        effective_access=_Access(),
        grants=_Grants(),
        executor=executor,
        audit=audit,
    )

    result = service.execute(_command())

    assert result["status"] == "completed"
    assert executor.calls[0]["enforcement_manifest"].authority == "hub"
    assert audit.events[-1]["decision"] == "allow"


def test_direct_call_without_grant_is_denied_despite_no_ui_state() -> None:
    executor = _Executor()
    audit = _Audit()
    service = CodeHugMutationAuthorizationService(
        tools=_Tools(),
        effective_access=_Access(),
        grants=_Grants(allowed=False),
        executor=executor,
        audit=audit,
    )

    with pytest.raises(
        CodeHugMutationAuthorizationError,
        match="grant_missing",
    ):
        service.execute(_command())

    assert executor.calls == []
    assert audit.events[-1]["decision"] == "deny"
    assert "write_mode" not in CodeHugMutationCommand.__dataclass_fields__
