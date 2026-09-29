from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.context_policy_lifecycle import (
    ContextPolicyLifecycleError,
    ContextPolicyPreview,
    derive_context_policy_digest,
)
from agent.services.source_control_prepare_index_access import (
    SAFE_INDEX_ACCESS_OPTION_ID,
    SAFE_INDEX_ACCESS_POLICY_ID,
    SAFE_INDEX_ACCESS_PRESET_ID,
    SourceControlPrepareIndexAccessError,
    SourceControlPrepareIndexAccessService,
)
from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
)
from ananta_contracts.source_control import (
    DestinationDescriptor,
    ProviderLocation,
)


class _Projections:
    def __init__(self) -> None:
        self.fail = False
        self.value = SimpleNamespace(
            etag="a" * 64,
            connection={"state": "active"},
            revision={
                "source_revision_id": "srev_" + "b" * 64,
                "revision_digest": "c" * 64,
                "captured_at": "2026-08-01T10:00:00Z",
            },
            admission={"state": "admitted"},
        )

    def get(self, **_kwargs):
        if self.fail:
            raise AssertionError("replay must precede projection OCC")
        return self.value


class _Bindings:
    def __init__(self, project_id="project-example") -> None:
        self.project_id = project_id

    def binding(self, **_kwargs):
        return {
            "tenant_id": "tenant-example",
            "project_id": self.project_id,
            "owner_id": "owner-example",
        }


class _Destinations:
    def __init__(self) -> None:
        self.local = _destination(ProviderLocation.LOCAL_CONTAINER, "local")
        self.external = _destination(
            ProviderLocation.EXTERNAL_REGION, "external"
        )

    def list(self, **_kwargs):
        return (self.local, self.external), None


class _Policies:
    def __init__(self) -> None:
        self.latest = None
        self.created_document = None

    def active(self, **_kwargs):
        if self.latest is None or self.latest.state != "active":
            raise ContextPolicyLifecycleError("policy_active_not_found")
        return self.latest

    def versions(self, **_kwargs):
        return ((self.latest,) if self.latest is not None else ()), None

    def create_draft(self, *, document, **_kwargs):
        self.created_document = document
        digest = derive_context_policy_digest(document)
        self.latest = SimpleNamespace(
            policy_id=SAFE_INDEX_ACCESS_POLICY_ID,
            version=1,
            state="draft",
            document=document,
            policy_digest=digest,
            etag="d" * 64,
        )
        return self.latest

    def lint(self, **_kwargs):
        return ()

    def preview(self, **_kwargs):
        return ContextPolicyPreview(
            decision="allow_redacted",
            reason_codes=(),
            matched_rule_path=("local-redacted-index-only",),
            approval_requirement=None,
            policy_digest=self.latest.policy_digest,
        )

    def activate(self, **_kwargs):
        values = {**self.latest.__dict__, "state": "active", "etag": "e" * 64}
        self.latest = SimpleNamespace(**values)
        return self.latest


class _Grants:
    def __init__(self) -> None:
        self.calls = []

    def create_grant(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            grant_id="grant_" + "f" * 64,
            state="active",
            etag="1" * 64,
            expires_at="2026-08-01T10:15:00Z",
        )


class _Idempotency:
    def __init__(self) -> None:
        self.completed = None
        self.digest = None
        self.releases = 0

    def claim(self, *, plan_digest, **_kwargs):
        if self.completed is not None:
            if plan_digest != self.digest:
                raise SourceControlPrepareIndexAccessError(
                    "idempotency_key_conflict", status_code=409
                )
            return SimpleNamespace(state="completed", result=self.completed)
        self.digest = plan_digest
        return SimpleNamespace(state="claimed", claim_token="claim-example")

    def complete(self, *, result, **_kwargs):
        self.completed = dict(result)

    def release(self, **_kwargs):
        self.releases += 1


def _destination(location: ProviderLocation, suffix: str):
    return DestinationDescriptor.create(
        worker_id=f"worker-{suffix}",
        worker_kind="worker",
        runtime_id=f"runtime-{suffix}",
        runtime_kind="ollama",
        provider_id=f"provider-{suffix}",
        model_id=f"model-{suffix}",
        model_class="embedding",
        provider_location=location,
        data_residency=f"residency-{suffix}",
    )


def _actor():
    return SourceControlPrincipal(
        subject_id="owner-example",
        tenant_id="tenant-example",
        project_id="project-example",
        roles=frozenset({"project_owner"}),
    )


def _service(*, bindings=None):
    projections = _Projections()
    destinations = _Destinations()
    policies = _Policies()
    grants = _Grants()
    idempotency = _Idempotency()
    service = SourceControlPrepareIndexAccessService(
        projections=projections,
        bindings=bindings or _Bindings(),
        destinations=destinations,
        policies=policies,
        grants=grants,
        idempotency=idempotency,
    )
    return service, projections, destinations, policies, grants, idempotency


def _payload(destination_id: str):
    return {
        "source_revision_id": "srev_" + "b" * 64,
        "destination_id": destination_id,
        "option_id": SAFE_INDEX_ACCESS_OPTION_ID,
        "duration_seconds": 900,
        "confirmed": True,
    }


def test_options_expose_only_local_redacted_one_time_normal_form() -> None:
    service, _, destinations, _, _, _ = _service()

    result = service.options(
        actor=_actor(), connection_id="connection-example"
    )

    assert result["readiness"] == {"ready": True, "reason_codes": []}
    assert [item["destination_id"] for item in result["destinations"]] == [
        destinations.local.destination_id
    ]
    assert result["options"][0]["effect"] == {
        "provider_location": "local",
        "transformation": "redacted",
        "one_time": True,
    }
    assert result["options"][0]["duration_seconds"] == {
        "minimum": 60,
        "maximum": 900,
        "default": 900,
    }
    assert result["etag"] == result["etag"].strip('"')


def test_prepare_owns_policy_and_grant_and_replays_before_occ() -> None:
    service, projections, destinations, policies, grants, idempotency = _service()
    options = service.options(
        actor=_actor(), connection_id="connection-example"
    )
    payload = _payload(destinations.local.destination_id)

    created = service.prepare(
        actor=_actor(),
        connection_id="connection-example",
        payload=payload,
        if_match=f'"{options["etag"]}"',
        idempotency_key="prepare-example",
    )
    projections.fail = True
    replay = service.prepare(
        actor=_actor(),
        connection_id="connection-example",
        payload=payload,
        if_match=f'"{options["etag"]}"',
        idempotency_key="prepare-example",
    )

    assert replay == created
    assert created["access_ready"] is True
    assert created["policy"]["state"] == "active"
    assert created["grant"]["state"] == "active"
    assert created["next_actions"] == ["start_index_run"]
    assert policies.created_document["defaults"]["send_allowed"] is False
    request = grants.calls[0]["request"]
    assert request.source_revision_id == payload["source_revision_id"]
    assert request.preset_id == SAFE_INDEX_ACCESS_PRESET_ID
    assert grants.calls[0]["if_match"] == "e" * 64
    serialized = str(created).casefold()
    assert not any(
        forbidden in serialized
        for forbidden in ("credential", "capability", "source_content")
    )
    assert idempotency.releases == 0


def test_prepare_fails_closed_for_scope_confirmation_occ_and_external() -> None:
    foreign, _, _, _, _, _ = _service(
        bindings=_Bindings(project_id="project-other")
    )
    with pytest.raises(
        SourceControlPrepareIndexAccessError,
        match="index_access_resource_not_found",
    ):
        foreign.options(actor=_actor(), connection_id="connection-example")

    service, _, destinations, _, grants, idempotency = _service()
    options = service.options(
        actor=_actor(), connection_id="connection-example"
    )
    with pytest.raises(
        SourceControlPrepareIndexAccessError,
        match="index_access_confirmation_required",
    ):
        service.prepare(
            actor=_actor(),
            connection_id="connection-example",
            payload={
                **_payload(destinations.local.destination_id),
                "confirmed": False,
            },
            if_match=options["etag"],
            idempotency_key="confirmation-example",
        )
    with pytest.raises(
        SourceControlPrepareIndexAccessError,
        match="index_access_version_conflict",
    ):
        service.prepare(
            actor=_actor(),
            connection_id="connection-example",
            payload=_payload(destinations.local.destination_id),
            if_match="0" * 64,
            idempotency_key="stale-example",
        )
    with pytest.raises(
        SourceControlPrepareIndexAccessError,
        match="index_access_destination_not_safe",
    ):
        service.prepare(
            actor=_actor(),
            connection_id="connection-example",
            payload=_payload(destinations.external.destination_id),
            if_match=options["etag"],
            idempotency_key="external-example",
        )
    assert grants.calls == []
    assert idempotency.releases == 2
