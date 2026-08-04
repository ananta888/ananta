from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from agent.services.source_access_enforcement import (
    ResolvedSourceGrant,
    SourceAccessEnforcementError,
    SourceAccessEnforcementService,
    SourceAccessRequest,
    source_access_grant_digest,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantState,
    GrantTransformation,
    SourceAccessGrant,
    derive_destination_id,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
DESTINATION_ID = derive_destination_id(
    worker_id="worker-example",
    worker_kind="llm",
    runtime_id="runtime-example",
    runtime_kind="local",
    provider_id="provider-example",
    model_id="model-example",
    model_class="local_model",
    provider_location="local_container",
    data_residency="local",
)


def _request(**overrides) -> SourceAccessRequest:
    values = {
        "tenant_id": "tenant-example",
        "project_id": "project-example",
        "source_revision_id": "srev_" + "a" * 64,
        "destination_id": DESTINATION_ID,
        "operation": GrantOperation.INDEX,
        "transformation": GrantTransformation.REDACTED,
        "purpose": "project-index",
        "policy_version": "policy-v1",
        "manifest_id": "manifest-example",
        "manifest_digest": "b" * 64,
        "assignment_id": "assignment-example",
        "lease_id": "lease-example",
        "destination_digest": "c" * 64,
    }
    values.update(overrides)
    return SourceAccessRequest(**values)


def _grant(**overrides) -> SourceAccessGrant:
    values = {
        "version": 1,
        "tenant_id": "tenant-example",
        "project_id": "project-example",
        "source_revision_id": "srev_" + "a" * 64,
        "destination_id": DESTINATION_ID,
        "operation": GrantOperation.INDEX,
        "transformation": GrantTransformation.REDACTED,
        "purpose": "project-index",
        "policy_version": "policy-v1",
        "state": GrantState.ACTIVE,
        "issued_at": NOW - timedelta(minutes=1),
        "expires_at": NOW + timedelta(minutes=5),
    }
    values.update(overrides)
    return SourceAccessGrant.create(**values)


class _Grants:
    def __init__(self, resolved: ResolvedSourceGrant | None) -> None:
        self.resolved = resolved

    def resolve_active(self, request):
        return self.resolved


class _Consumptions:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls: list[dict] = []

    def consume_once(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return self.result


class _Signer:
    def sign(self, *, manifest_digest: str) -> str:
        return f"signature:{manifest_digest}"


class _Verifier:
    @staticmethod
    def verify_manifest(manifest) -> bool:
        return manifest.get("signature") == (
            "signature:" + str(manifest.get("binding_digest") or "")
        )


def _service(
    grant: SourceAccessGrant | None,
    *,
    mode: str = "reusable",
    consumption_result: bool = True,
):
    resolved = (
        ResolvedSourceGrant(
            grant=grant,
            consumption_mode=mode,
            concurrency_version=1,
        )
        if grant is not None
        else None
    )
    consumptions = _Consumptions(consumption_result)
    return (
        SourceAccessEnforcementService(
            grants=_Grants(resolved),
            consumptions=consumptions,
            signer=_Signer(),
        ),
        consumptions,
    )


def test_exact_grant_produces_assignment_bound_worker_manifest() -> None:
    service, consumptions = _service(_grant(), mode="one_time")

    authorized = service.authorize(_request(), now=NOW)

    assert authorized.decision.allowed is True
    assert authorized.manifest.authority == "hub"
    assert authorized.manifest.assignment_id == "assignment-example"
    assert authorized.manifest.lease_id == "lease-example"
    assert authorized.manifest.source_revision_id == "srev_" + "a" * 64
    assert consumptions.calls[0]["consumption_digest"] == (
        authorized.manifest.binding_digest
    )


@pytest.mark.parametrize(
    ("access_request", "reason_code"),
    (
        (
            _request(source_revision_id="srev_" + "d" * 64),
            "grant_revision_mismatch",
        ),
        (
            _request(destination_id="dst_" + "d" * 64),
            "grant_destination_mismatch",
        ),
        (
            _request(operation=GrantOperation.EXPORT),
            "grant_operation_mismatch",
        ),
        (
            _request(transformation=GrantTransformation.RAW),
            "grant_transformation_mismatch",
        ),
    ),
)
def test_grant_cannot_be_reused_across_a_binding(
    access_request: SourceAccessRequest,
    reason_code: str,
) -> None:
    service, _ = _service(_grant())

    with pytest.raises(SourceAccessEnforcementError, match=reason_code):
        service.authorize(access_request, now=NOW)


def test_missing_expired_revoked_or_consumed_grant_is_denied() -> None:
    missing, _ = _service(None)
    expired, _ = _service(_grant(expires_at=NOW))
    revoked, _ = _service(_grant(state=GrantState.REVOKED))
    consumed, _ = _service(
        _grant(),
        mode="one_time",
        consumption_result=False,
    )

    with pytest.raises(SourceAccessEnforcementError, match="grant_missing"):
        missing.authorize(_request(), now=NOW)
    with pytest.raises(SourceAccessEnforcementError, match="grant_expired"):
        expired.authorize(_request(), now=NOW)
    with pytest.raises(SourceAccessEnforcementError, match="grant_not_active"):
        revoked.authorize(_request(), now=NOW)
    with pytest.raises(SourceAccessEnforcementError, match="already_consumed"):
        consumed.authorize(_request(), now=NOW)


def test_nearly_expired_grant_is_denied_before_consumption() -> None:
    service, consumptions = _service(
        _grant(expires_at=NOW + timedelta(seconds=5)),
        mode="one_time",
    )

    with pytest.raises(SourceAccessEnforcementError, match="grant_expiring"):
        service.authorize(_request(), now=NOW)

    assert consumptions.calls == []


def test_runtime_and_transfer_window_is_required_before_one_time_consumption(
) -> None:
    grant = _grant(expires_at=NOW + timedelta(seconds=121))
    consumptions = _Consumptions()
    service = SourceAccessEnforcementService(
        grants=_Grants(
            ResolvedSourceGrant(
                grant=grant,
                consumption_mode="one_time",
                concurrency_version=1,
            )
        ),
        consumptions=consumptions,
        signer=_Signer(),
        manifest_verifier=_Verifier(),
    )

    authorized = service.authorize(
        _request(),
        now=NOW,
        minimum_remaining_ms=120_000,
    )
    assert len(consumptions.calls) == 1

    with pytest.raises(
        SourceAccessEnforcementError,
        match="delegated_manifest_expiring",
    ):
        service.validate_delegated_manifest(
            asdict(authorized.manifest),
            _request(),
            now=NOW + timedelta(seconds=2),
            minimum_remaining_ms=120_000,
        )


def test_persisted_manifest_requires_safe_lifetime_and_ascii_digest() -> None:
    grant = _grant(expires_at=NOW + timedelta(seconds=7))
    request = _request(
        source_access_grant_id=grant.grant_id,
        source_access_grant_digest=source_access_grant_digest(grant),
    )
    resolved = ResolvedSourceGrant(
        grant=grant,
        consumption_mode="one_time",
        concurrency_version=1,
    )
    service = SourceAccessEnforcementService(
        grants=_Grants(resolved),
        consumptions=_Consumptions(),
        signer=_Signer(),
        manifest_verifier=_Verifier(),
    )
    authorized = service.authorize(request, now=NOW)
    manifest = asdict(authorized.manifest)

    with pytest.raises(
        SourceAccessEnforcementError,
        match="delegated_manifest_expiring",
    ):
        service.validate_delegated_manifest(
            manifest,
            request,
            now=NOW + timedelta(seconds=2),
        )

    malformed = {**manifest, "binding_digest": "é" * 64}
    with pytest.raises(
        SourceAccessEnforcementError,
        match="delegated_manifest_digest_invalid",
    ):
        service.validate_delegated_manifest(
            malformed,
            request,
            now=NOW,
        )


def test_signing_failure_does_not_consume_one_time_grant() -> None:
    consumptions = _Consumptions()

    class _FailingSigner:
        def sign(self, *, manifest_digest: str) -> str:
            return ""

    service = SourceAccessEnforcementService(
        grants=_Grants(
            ResolvedSourceGrant(
                grant=_grant(),
                consumption_mode="one_time",
                concurrency_version=1,
            )
        ),
        consumptions=consumptions,
        signer=_FailingSigner(),
    )

    with pytest.raises(
        SourceAccessEnforcementError,
        match="manifest_signing_failed",
    ):
        service.authorize(_request(), now=NOW)
    assert consumptions.calls == []


def test_exact_consumption_receipt_recovery_is_explicitly_opt_in() -> None:
    consumptions = _Consumptions(result=False)
    receipt_calls = []

    class _Receipts:
        @staticmethod
        def verify_exact_consumption_receipt(**values):
            receipt_calls.append(values)
            return True

    service = SourceAccessEnforcementService(
        grants=_Grants(
            ResolvedSourceGrant(
                grant=_grant(),
                consumption_mode="one_time",
                concurrency_version=2,
            )
        ),
        consumptions=consumptions,
        signer=_Signer(),
        consumption_receipts=_Receipts(),
    )

    with pytest.raises(
        SourceAccessEnforcementError,
        match="grant_already_consumed",
    ):
        service.authorize(_request(), now=NOW)
    assert receipt_calls == []

    authorized = service.authorize(
        _request(),
        now=NOW,
        allow_exact_consumption_recovery=True,
    )

    assert authorized.decision.allowed is True
    assert receipt_calls == [
        {
            "grant_id": authorized.decision.grant_id,
            "expected_policy_version": 2,
            "consumption_digest": authorized.decision.binding_digest,
        }
    ]


@pytest.mark.parametrize(
    ("request_override", "reason_code"),
    [
        ({"purpose": "different-purpose"}, "grant_purpose_mismatch"),
        ({"policy_version": "policy-v2"}, "grant_policy_version_mismatch"),
        (
            {"source_access_grant_id": "grant-" + "f" * 64},
            "grant_id_mismatch",
        ),
        (
            {"source_access_grant_digest": "f" * 64},
            "grant_digest_mismatch",
        ),
    ],
)
def test_grant_purpose_policy_and_identity_are_fully_bound(
    request_override,
    reason_code,
) -> None:
    grant = _grant()
    binding = {
        "source_access_grant_id": grant.grant_id,
        "source_access_grant_digest": source_access_grant_digest(
            grant
        ),
        **request_override,
    }
    request = _request(**binding)
    service, _ = _service(grant)

    with pytest.raises(
        SourceAccessEnforcementError,
        match=reason_code,
    ):
        service.authorize(request, now=NOW)
