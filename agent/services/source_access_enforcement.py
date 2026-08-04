"""Hub-owned source grant evaluation and delegated enforcement manifests."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from ananta_contracts.source_control import (
    GrantOperation,
    GrantState,
    GrantTransformation,
    SourceAccessGrant,
)

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIN_DELEGATED_MANIFEST_REMAINING_MS = 5_000


class SourceAccessEnforcementError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SourceAccessRequest:
    tenant_id: str
    project_id: str
    source_revision_id: str
    destination_id: str
    operation: GrantOperation
    transformation: GrantTransformation
    purpose: str
    policy_version: str
    manifest_id: str
    manifest_digest: str
    assignment_id: str
    lease_id: str
    destination_digest: str
    source_revision_digest: str | None = None
    source_access_grant_id: str | None = None
    source_access_grant_digest: str | None = None
    policy_digest: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "project_id",
            "source_revision_id",
            "destination_id",
            "purpose",
            "policy_version",
            "manifest_id",
            "assignment_id",
            "lease_id",
        ):
            if not _OPAQUE_ID.fullmatch(str(getattr(self, name) or "")):
                raise SourceAccessEnforcementError(f"{name}_invalid")
        for name in ("manifest_digest", "destination_digest"):
            if not _SHA256.fullmatch(str(getattr(self, name) or "")):
                raise SourceAccessEnforcementError(f"{name}_invalid")
        for name in (
            "source_revision_digest",
            "source_access_grant_digest",
            "policy_digest",
        ):
            value = getattr(self, name)
            if value is not None and not _SHA256.fullmatch(str(value)):
                raise SourceAccessEnforcementError(f"{name}_invalid")
        if (
            self.source_access_grant_id is not None
            and not _OPAQUE_ID.fullmatch(self.source_access_grant_id)
        ):
            raise SourceAccessEnforcementError(
                "source_access_grant_id_invalid"
            )


@dataclass(frozen=True)
class ResolvedSourceGrant:
    grant: SourceAccessGrant
    consumption_mode: str
    concurrency_version: int

    def __post_init__(self) -> None:
        if self.consumption_mode not in {"reusable", "one_time"}:
            raise SourceAccessEnforcementError("grant_consumption_mode_invalid")
        if self.concurrency_version < 1:
            raise SourceAccessEnforcementError("grant_concurrency_version_invalid")


class SourceGrantResolverPort(Protocol):
    def resolve_active(
        self,
        request: SourceAccessRequest,
    ) -> ResolvedSourceGrant | None: ...


class OneTimeGrantConsumptionPort(Protocol):
    def consume_once(
        self,
        *,
        grant_id: str,
        expected_version: int,
        consumption_digest: str,
    ) -> bool: ...


class OneTimeGrantConsumptionReceiptPort(Protocol):
    def verify_exact_consumption_receipt(
        self,
        *,
        grant_id: str,
        expected_policy_version: int,
        consumption_digest: str,
    ) -> bool: ...


class EnforcementManifestSignerPort(Protocol):
    def sign(self, *, manifest_digest: str) -> str: ...


class EnforcementManifestVerifierPort(Protocol):
    def verify_manifest(self, manifest: Mapping[str, object]) -> bool: ...


@dataclass(frozen=True)
class SourceAccessDecision:
    allowed: bool
    reason_code: str
    binding_digest: str
    grant_id: str | None


@dataclass(frozen=True)
class DelegatedSourceEnforcementManifest:
    schema: str
    authority: str
    source_revision_id: str
    destination_id: str
    destination_digest: str
    source_access_grant_id: str
    operation: str
    transformation: str
    purpose: str
    policy_version: str
    content_manifest_id: str
    content_manifest_digest: str
    assignment_id: str
    lease_id: str
    binding_digest: str
    signature: str
    grant_expires_at_epoch_ms: int = 0
    tenant_id: str = ""
    project_id: str = ""
    source_revision_digest: str = ""
    source_access_grant_digest: str = ""
    policy_digest: str = ""


@dataclass(frozen=True)
class AuthorizedSourceDispatch:
    decision: SourceAccessDecision
    manifest: DelegatedSourceEnforcementManifest


class SourceAccessEnforcementService:
    def __init__(
        self,
        *,
        grants: SourceGrantResolverPort,
        consumptions: OneTimeGrantConsumptionPort,
        signer: EnforcementManifestSignerPort,
        manifest_verifier: EnforcementManifestVerifierPort | None = None,
        consumption_receipts: OneTimeGrantConsumptionReceiptPort | None = None,
    ) -> None:
        self._grants = grants
        self._consumptions = consumptions
        self._signer = signer
        self._manifest_verifier = manifest_verifier
        self._consumption_receipts = consumption_receipts

    def authorize(
        self,
        request: SourceAccessRequest,
        *,
        now: datetime | None = None,
        allow_exact_consumption_recovery: bool = False,
        minimum_remaining_ms: int | None = None,
    ) -> AuthorizedSourceDispatch:
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            raise SourceAccessEnforcementError("current_time_must_be_aware")
        resolved = self._grants.resolve_active(request)
        if resolved is None:
            raise SourceAccessEnforcementError("source_access_grant_missing")
        grant = resolved.grant
        reason = _grant_mismatch_reason(
            grant,
            request=request,
            now=current_time,
        )
        if reason is not None:
            raise SourceAccessEnforcementError(reason)
        grant_expires_at_epoch_ms = int(
            grant.expires_at.timestamp() * 1000
        )
        required_remaining_ms = self._required_remaining_ms(
            minimum_remaining_ms
        )
        if grant_expires_at_epoch_ms <= (
            int(current_time.timestamp() * 1000)
            + required_remaining_ms
        ):
            raise SourceAccessEnforcementError("grant_expiring")
        binding_digest = source_access_binding_digest(
            request,
            grant_expires_at_epoch_ms=grant_expires_at_epoch_ms,
        )
        signature = self._signer.sign(manifest_digest=binding_digest)
        if not isinstance(signature, str) or not signature:
            raise SourceAccessEnforcementError("manifest_signing_failed")
        if resolved.consumption_mode == "one_time":
            consumed = self._consumptions.consume_once(
                grant_id=grant.grant_id,
                expected_version=resolved.concurrency_version,
                consumption_digest=binding_digest,
            )
            recovered = bool(
                not consumed
                and allow_exact_consumption_recovery
                and self._consumption_receipts is not None
                and self._consumption_receipts.verify_exact_consumption_receipt(
                    grant_id=grant.grant_id,
                    expected_policy_version=resolved.concurrency_version,
                    consumption_digest=binding_digest,
                )
            )
            if not consumed and not recovered:
                raise SourceAccessEnforcementError("grant_already_consumed")
        manifest = DelegatedSourceEnforcementManifest(
            schema="ananta.source-control.enforcement-manifest.v1",
            authority="hub",
            source_revision_id=request.source_revision_id,
            destination_id=request.destination_id,
            destination_digest=request.destination_digest,
            source_access_grant_id=grant.grant_id,
            operation=request.operation.value,
            transformation=request.transformation.value,
            purpose=request.purpose,
            policy_version=request.policy_version,
            content_manifest_id=request.manifest_id,
            content_manifest_digest=request.manifest_digest,
            assignment_id=request.assignment_id,
            lease_id=request.lease_id,
            binding_digest=binding_digest,
            signature=signature,
            grant_expires_at_epoch_ms=grant_expires_at_epoch_ms,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            source_revision_digest=str(
                request.source_revision_digest or ""
            ),
            source_access_grant_digest=str(
                request.source_access_grant_digest or ""
            ),
            policy_digest=str(request.policy_digest or ""),
        )
        return AuthorizedSourceDispatch(
            decision=SourceAccessDecision(
                allowed=True,
                reason_code="grant_match",
                binding_digest=binding_digest,
                grant_id=grant.grant_id,
            ),
            manifest=manifest,
        )

    def validate_delegated_manifest(
        self,
        manifest: Mapping[str, object],
        request: SourceAccessRequest,
        *,
        now: datetime | None = None,
        minimum_remaining_ms: int | None = None,
    ) -> DelegatedSourceEnforcementManifest:
        """Revalidate a persisted one-time dispatch capability without consuming it again."""

        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            raise SourceAccessEnforcementError("current_time_must_be_aware")
        expected_fields = set(DelegatedSourceEnforcementManifest.__dataclass_fields__)
        if set(manifest) != expected_fields:
            raise SourceAccessEnforcementError("delegated_manifest_fields_invalid")
        try:
            candidate = DelegatedSourceEnforcementManifest(**dict(manifest))
        except (TypeError, ValueError) as exc:
            raise SourceAccessEnforcementError(
                "delegated_manifest_invalid"
            ) from exc
        if (
            candidate.schema != "ananta.source-control.enforcement-manifest.v1"
            or candidate.authority != "hub"
        ):
            raise SourceAccessEnforcementError("delegated_manifest_authority_invalid")
        expiry = candidate.grant_expires_at_epoch_ms
        required_remaining_ms = self._required_remaining_ms(
            minimum_remaining_ms
        )
        if (
            isinstance(expiry, bool)
            or not isinstance(expiry, int)
            or expiry <= int(current_time.timestamp() * 1000)
        ):
            raise SourceAccessEnforcementError("delegated_manifest_expired")
        if expiry <= (
            int(current_time.timestamp() * 1000)
            + required_remaining_ms
        ):
            raise SourceAccessEnforcementError("delegated_manifest_expiring")
        expected_values = {
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "source_revision_id": request.source_revision_id,
            "source_revision_digest": str(request.source_revision_digest or ""),
            "destination_id": request.destination_id,
            "destination_digest": request.destination_digest,
            "source_access_grant_id": str(request.source_access_grant_id or ""),
            "source_access_grant_digest": str(
                request.source_access_grant_digest or ""
            ),
            "operation": request.operation.value,
            "transformation": request.transformation.value,
            "purpose": request.purpose,
            "policy_version": request.policy_version,
            "policy_digest": str(request.policy_digest or ""),
            "content_manifest_id": request.manifest_id,
            "content_manifest_digest": request.manifest_digest,
            "assignment_id": request.assignment_id,
            "lease_id": request.lease_id,
        }
        if any(
            str(getattr(candidate, name)) != str(expected)
            for name, expected in expected_values.items()
        ):
            raise SourceAccessEnforcementError("delegated_manifest_binding_mismatch")
        expected_digest = source_access_binding_digest(
            request,
            grant_expires_at_epoch_ms=expiry,
        )
        supplied_digest = candidate.binding_digest
        if not isinstance(supplied_digest, str) or not _SHA256.fullmatch(
            supplied_digest
        ):
            raise SourceAccessEnforcementError("delegated_manifest_digest_invalid")
        if not hmac.compare_digest(supplied_digest, expected_digest):
            raise SourceAccessEnforcementError("delegated_manifest_digest_invalid")
        verifier = self._manifest_verifier
        if verifier is None:
            raise SourceAccessEnforcementError(
                "delegated_manifest_verifier_unavailable"
            )
        if not verifier.verify_manifest(manifest):
            raise SourceAccessEnforcementError("delegated_manifest_signature_invalid")
        return candidate

    @staticmethod
    def _required_remaining_ms(value: int | None) -> int:
        if value is None:
            return _MIN_DELEGATED_MANIFEST_REMAINING_MS
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SourceAccessEnforcementError(
                "minimum_remaining_ms_invalid"
            )
        return max(value, _MIN_DELEGATED_MANIFEST_REMAINING_MS)


def _grant_mismatch_reason(
    grant: SourceAccessGrant,
    *,
    request: SourceAccessRequest,
    now: datetime,
) -> str | None:
    if grant.state is not GrantState.ACTIVE:
        return "grant_not_active"
    if grant.expires_at <= now:
        return "grant_expired"
    if (
        request.source_access_grant_id is not None
        and grant.grant_id != request.source_access_grant_id
    ):
        return "grant_id_mismatch"
    if (
        request.source_access_grant_digest is not None
        and source_access_grant_digest(grant)
        != request.source_access_grant_digest
    ):
        return "grant_digest_mismatch"
    comparisons = (
        (grant.tenant_id, request.tenant_id, "grant_tenant_mismatch"),
        (grant.project_id, request.project_id, "grant_project_mismatch"),
        (
            grant.source_revision_id,
            request.source_revision_id,
            "grant_revision_mismatch",
        ),
        (
            grant.destination_id,
            request.destination_id,
            "grant_destination_mismatch",
        ),
        (grant.operation, request.operation, "grant_operation_mismatch"),
        (
            grant.transformation,
            request.transformation,
            "grant_transformation_mismatch",
        ),
        (grant.purpose, request.purpose, "grant_purpose_mismatch"),
        (
            grant.policy_version,
            request.policy_version,
            "grant_policy_version_mismatch",
        ),
    )
    for actual, expected, reason in comparisons:
        if actual != expected:
            return reason
    return None


def source_access_binding_digest(
    request: SourceAccessRequest,
    *,
    grant_expires_at_epoch_ms: int | None = None,
) -> str:
    payload = {
        "assignment_id": request.assignment_id,
        "destination_digest": request.destination_digest,
        "destination_id": request.destination_id,
        "grant_expires_at_epoch_ms": grant_expires_at_epoch_ms,
        "lease_id": request.lease_id,
        "manifest_digest": request.manifest_digest,
        "manifest_id": request.manifest_id,
        "operation": request.operation.value,
        "policy_version": request.policy_version,
        "project_id": request.project_id,
        "purpose": request.purpose,
        "policy_digest": request.policy_digest,
        "source_revision_id": request.source_revision_id,
        "source_revision_digest": request.source_revision_digest,
        "source_access_grant_id": request.source_access_grant_id,
        "source_access_grant_digest": request.source_access_grant_digest,
        "tenant_id": request.tenant_id,
        "transformation": request.transformation.value,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def source_access_grant_digest(grant: SourceAccessGrant) -> str:
    return hashlib.sha256(
        json.dumps(
            grant.to_wire(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


_binding_digest = source_access_binding_digest
