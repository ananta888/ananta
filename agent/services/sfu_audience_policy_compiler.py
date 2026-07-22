"""Pure, default-deny Hub compiler for immutable SFU audiences."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Protocol


class AudienceReasonCode(str, Enum):
    ELIGIBLE = "audience_eligible"
    PARENT_MISSING = "audience_parent_missing"
    PARENT_INCOMPLETE = "audience_parent_incomplete"
    PARENT_INACTIVE = "audience_parent_inactive"
    CROSS_TENANT = "audience_cross_tenant"
    CROSS_ROOM = "audience_cross_room"
    CROSS_PUBLICATION = "audience_cross_publication"
    UNKNOWN_ROLE = "audience_unknown_role"
    ROOM_GRANT_MISSING = "audience_room_grant_missing"
    PUBLISH_GRANT_MISSING = "audience_publish_grant_missing"
    SUBSCRIBE_GRANT_MISSING = "audience_subscribe_grant_missing"
    POLICY_EPOCH_STALE = "audience_policy_epoch_stale"
    MEMBERSHIP_EPOCH_STALE = "audience_membership_epoch_stale"
    KEY_EPOCH_STALE = "audience_key_epoch_stale"
    CONSENT_MISSING = "audience_consent_missing"
    CONSENT_REVOKED = "audience_consent_revoked"
    CONSENT_STALE = "audience_consent_stale"
    PRIVACY_SCOPE_DENIED = "audience_privacy_scope_denied"
    SUBSCRIPTION_EXPIRED = "audience_subscription_expired"
    CAPABILITY_MISSING = "audience_capability_missing"
    E2EE_CAPABILITY_MISSING = "audience_e2ee_capability_missing"
    CAPABILITY_CONFLICT = "audience_capability_conflict"
    DUPLICATE_RECEIVER = "audience_duplicate_receiver"


KNOWN_AUDIENCE_ROLES = frozenset(
    {"publisher", "subscriber", "viewer", "moderator"}
)


@dataclass(frozen=True, slots=True)
class AudienceCompileRequest:
    tenant_id: str
    room_ref: str
    publication_ref: str
    publisher_ref: str
    receiver_refs: tuple[str, ...]
    privacy_scope: str
    consent_scope: str
    media_kind: str
    require_e2ee: bool
    policy_epoch: int
    membership_epoch: int
    key_epoch: int
    evaluated_at_ms: int


@dataclass(frozen=True, slots=True)
class AudienceParentSnapshot:
    tenant_id: str
    room_ref: str
    publication_ref: str
    complete: bool
    active: bool
    policy_epoch: int
    membership_epoch: int
    key_epoch: int
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class AudienceGrantSnapshot:
    tenant_id: str
    room_ref: str
    subject_ref: str
    role: str
    room_granted: bool
    publish_publication_refs: tuple[str, ...]
    subscribe_publication_refs: tuple[str, ...]
    policy_epoch: int
    membership_epoch: int
    valid_until_ms: int


@dataclass(frozen=True, slots=True)
class AudienceConsentSnapshot:
    tenant_id: str
    room_ref: str
    receiver_ref: str
    publication_ref: str
    consent_scopes: tuple[str, ...]
    privacy_scopes: tuple[str, ...]
    revoked: bool
    valid_until_ms: int
    membership_epoch: int


@dataclass(frozen=True, slots=True)
class AudienceCapabilitySnapshot:
    tenant_id: str
    room_ref: str
    receiver_ref: str
    media_kinds: tuple[str, ...]
    e2ee: bool
    contradictory: bool
    key_epoch: int


class AudienceParentReadPort(Protocol):
    def get_parent(
        self, *, tenant_id: str, room_ref: str, publication_ref: str
    ) -> AudienceParentSnapshot | None: ...


class AudienceGrantReadPort(Protocol):
    def get_grant(
        self, *, tenant_id: str, room_ref: str, subject_ref: str
    ) -> AudienceGrantSnapshot | None: ...


class AudienceConsentReadPort(Protocol):
    def get_consent(
        self,
        *,
        tenant_id: str,
        room_ref: str,
        receiver_ref: str,
        publication_ref: str,
    ) -> AudienceConsentSnapshot | None: ...


class AudienceCapabilityReadPort(Protocol):
    def get_capability(
        self, *, tenant_id: str, room_ref: str, receiver_ref: str
    ) -> AudienceCapabilitySnapshot | None: ...


@dataclass(frozen=True, slots=True)
class AudienceDecision:
    receiver_ref: str
    eligible: bool
    reason_code: AudienceReasonCode


@dataclass(frozen=True, slots=True)
class CompiledAudienceProjection:
    tenant_id: str
    room_ref: str
    publication_ref: str
    receiver_refs: tuple[str, ...]
    decisions: tuple[AudienceDecision, ...]
    audience_digest: str
    policy_digest: str
    membership_digest: str
    policy_epoch: int
    membership_epoch: int
    key_epoch: int


class SfuAudiencePolicyCompiler:
    """Reads exact Hub snapshots and applies a deterministic intersection."""

    def __init__(
        self,
        *,
        parents: AudienceParentReadPort,
        grants: AudienceGrantReadPort,
        consents: AudienceConsentReadPort,
        capabilities: AudienceCapabilityReadPort,
    ) -> None:
        self._parents = parents
        self._grants = grants
        self._consents = consents
        self._capabilities = capabilities

    def compile(self, request: AudienceCompileRequest) -> CompiledAudienceProjection:
        ordered_receivers = tuple(sorted(request.receiver_refs))
        request = replace(request, receiver_refs=ordered_receivers)
        if len(set(ordered_receivers)) != len(ordered_receivers):
            return self._deny_all(
                request, ordered_receivers, AudienceReasonCode.DUPLICATE_RECEIVER
            )
        parent = self._parents.get_parent(
            tenant_id=request.tenant_id,
            room_ref=request.room_ref,
            publication_ref=request.publication_ref,
        )
        parent_reason = self._parent_reason(request, parent)
        if parent_reason is not None:
            return self._deny_all(request, ordered_receivers, parent_reason)
        assert parent is not None
        publisher = self._grants.get_grant(
            tenant_id=request.tenant_id,
            room_ref=request.room_ref,
            subject_ref=request.publisher_ref,
        )
        publisher_reason = self._publisher_reason(request, publisher)
        if publisher_reason is not None:
            return self._deny_all(request, ordered_receivers, publisher_reason)

        decisions = tuple(
            self._receiver_decision(request, receiver_ref)
            for receiver_ref in ordered_receivers
        )
        eligible = tuple(
            decision.receiver_ref for decision in decisions if decision.eligible
        )
        return self._projection(request, eligible, decisions)

    def _parent_reason(
        self,
        request: AudienceCompileRequest,
        parent: AudienceParentSnapshot | None,
    ) -> AudienceReasonCode | None:
        if parent is None:
            return AudienceReasonCode.PARENT_MISSING
        if not parent.complete:
            return AudienceReasonCode.PARENT_INCOMPLETE
        if parent.tenant_id != request.tenant_id:
            return AudienceReasonCode.CROSS_TENANT
        if parent.room_ref != request.room_ref:
            return AudienceReasonCode.CROSS_ROOM
        if parent.publication_ref != request.publication_ref:
            return AudienceReasonCode.CROSS_PUBLICATION
        if not parent.active or parent.expires_at_ms <= request.evaluated_at_ms:
            return AudienceReasonCode.PARENT_INACTIVE
        if parent.policy_epoch != request.policy_epoch:
            return AudienceReasonCode.POLICY_EPOCH_STALE
        if parent.membership_epoch != request.membership_epoch:
            return AudienceReasonCode.MEMBERSHIP_EPOCH_STALE
        if parent.key_epoch != request.key_epoch:
            return AudienceReasonCode.KEY_EPOCH_STALE
        return None

    def _publisher_reason(
        self,
        request: AudienceCompileRequest,
        grant: AudienceGrantSnapshot | None,
    ) -> AudienceReasonCode | None:
        common = self._grant_reason(request, grant)
        if common is not None:
            return common
        assert grant is not None
        if request.publication_ref not in grant.publish_publication_refs:
            return AudienceReasonCode.PUBLISH_GRANT_MISSING
        return None

    def _receiver_decision(
        self, request: AudienceCompileRequest, receiver_ref: str
    ) -> AudienceDecision:
        grant = self._grants.get_grant(
            tenant_id=request.tenant_id,
            room_ref=request.room_ref,
            subject_ref=receiver_ref,
        )
        reason = self._grant_reason(request, grant)
        if reason is None and grant is not None:
            if request.publication_ref not in grant.subscribe_publication_refs:
                reason = AudienceReasonCode.SUBSCRIBE_GRANT_MISSING
            elif grant.valid_until_ms <= request.evaluated_at_ms:
                reason = AudienceReasonCode.SUBSCRIPTION_EXPIRED
        consent = self._consents.get_consent(
            tenant_id=request.tenant_id,
            room_ref=request.room_ref,
            receiver_ref=receiver_ref,
            publication_ref=request.publication_ref,
        )
        if reason is None:
            reason = self._consent_reason(request, receiver_ref, consent)
        capability = self._capabilities.get_capability(
            tenant_id=request.tenant_id,
            room_ref=request.room_ref,
            receiver_ref=receiver_ref,
        )
        if reason is None:
            reason = self._capability_reason(request, receiver_ref, capability)
        effective = reason or AudienceReasonCode.ELIGIBLE
        return AudienceDecision(
            receiver_ref,
            effective is AudienceReasonCode.ELIGIBLE,
            effective,
        )

    @staticmethod
    def _grant_reason(
        request: AudienceCompileRequest,
        grant: AudienceGrantSnapshot | None,
    ) -> AudienceReasonCode | None:
        if grant is None:
            return AudienceReasonCode.SUBSCRIBE_GRANT_MISSING
        if grant.tenant_id != request.tenant_id:
            return AudienceReasonCode.CROSS_TENANT
        if grant.room_ref != request.room_ref:
            return AudienceReasonCode.CROSS_ROOM
        if grant.role not in KNOWN_AUDIENCE_ROLES:
            return AudienceReasonCode.UNKNOWN_ROLE
        if not grant.room_granted:
            return AudienceReasonCode.ROOM_GRANT_MISSING
        if grant.policy_epoch != request.policy_epoch:
            return AudienceReasonCode.POLICY_EPOCH_STALE
        if grant.membership_epoch != request.membership_epoch:
            return AudienceReasonCode.MEMBERSHIP_EPOCH_STALE
        return None

    @staticmethod
    def _consent_reason(
        request: AudienceCompileRequest,
        receiver_ref: str,
        consent: AudienceConsentSnapshot | None,
    ) -> AudienceReasonCode | None:
        if consent is None:
            return AudienceReasonCode.CONSENT_MISSING
        if consent.tenant_id != request.tenant_id:
            return AudienceReasonCode.CROSS_TENANT
        if consent.room_ref != request.room_ref:
            return AudienceReasonCode.CROSS_ROOM
        if (
            consent.receiver_ref != receiver_ref
            or consent.publication_ref != request.publication_ref
        ):
            return AudienceReasonCode.CROSS_PUBLICATION
        if consent.revoked:
            return AudienceReasonCode.CONSENT_REVOKED
        if (
            consent.valid_until_ms <= request.evaluated_at_ms
            or consent.membership_epoch != request.membership_epoch
        ):
            return AudienceReasonCode.CONSENT_STALE
        if request.consent_scope not in consent.consent_scopes:
            return AudienceReasonCode.CONSENT_MISSING
        if request.privacy_scope not in consent.privacy_scopes:
            return AudienceReasonCode.PRIVACY_SCOPE_DENIED
        return None

    @staticmethod
    def _capability_reason(
        request: AudienceCompileRequest,
        receiver_ref: str,
        capability: AudienceCapabilitySnapshot | None,
    ) -> AudienceReasonCode | None:
        if capability is None:
            return AudienceReasonCode.CAPABILITY_MISSING
        if capability.tenant_id != request.tenant_id:
            return AudienceReasonCode.CROSS_TENANT
        if capability.room_ref != request.room_ref:
            return AudienceReasonCode.CROSS_ROOM
        if capability.receiver_ref != receiver_ref or capability.contradictory:
            return AudienceReasonCode.CAPABILITY_CONFLICT
        if capability.key_epoch != request.key_epoch:
            return AudienceReasonCode.KEY_EPOCH_STALE
        if request.media_kind not in capability.media_kinds:
            return AudienceReasonCode.CAPABILITY_MISSING
        if request.require_e2ee and not capability.e2ee:
            return AudienceReasonCode.E2EE_CAPABILITY_MISSING
        return None

    def _deny_all(
        self,
        request: AudienceCompileRequest,
        receiver_refs: tuple[str, ...],
        reason: AudienceReasonCode,
    ) -> CompiledAudienceProjection:
        decisions = tuple(
            AudienceDecision(receiver_ref, False, reason)
            for receiver_ref in receiver_refs
        )
        return self._projection(request, (), decisions)

    @staticmethod
    def _projection(
        request: AudienceCompileRequest,
        receiver_refs: tuple[str, ...],
        decisions: tuple[AudienceDecision, ...],
    ) -> CompiledAudienceProjection:
        policy_payload = {
            "domain": "ananta:sfu-audience-policy:v1",
            "request": asdict(request),
            "decisions": [
                {
                    "receiver_ref": item.receiver_ref,
                    "eligible": item.eligible,
                    "reason_code": item.reason_code.value,
                }
                for item in decisions
            ],
        }
        membership_payload = {
            "domain": "ananta:sfu-audience-membership:v1",
            "tenant_id": request.tenant_id,
            "room_ref": request.room_ref,
            "publication_ref": request.publication_ref,
            "receiver_refs": receiver_refs,
            "membership_epoch": request.membership_epoch,
            "key_epoch": request.key_epoch,
        }
        policy_digest = _canonical_digest(policy_payload)
        membership_digest = _canonical_digest(membership_payload)
        audience_digest = _canonical_digest(
            {
                "domain": "ananta:sfu-audience:v1",
                "policy_digest": policy_digest,
                "membership_digest": membership_digest,
                "receiver_refs": receiver_refs,
            }
        )
        return CompiledAudienceProjection(
            tenant_id=request.tenant_id,
            room_ref=request.room_ref,
            publication_ref=request.publication_ref,
            receiver_refs=receiver_refs,
            decisions=decisions,
            audience_digest=audience_digest,
            policy_digest=policy_digest,
            membership_digest=membership_digest,
            policy_epoch=request.policy_epoch,
            membership_epoch=request.membership_epoch,
            key_epoch=request.key_epoch,
        )


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "AudienceCapabilityReadPort",
    "AudienceCapabilitySnapshot",
    "AudienceCompileRequest",
    "AudienceConsentReadPort",
    "AudienceConsentSnapshot",
    "AudienceDecision",
    "AudienceGrantReadPort",
    "AudienceGrantSnapshot",
    "AudienceParentReadPort",
    "AudienceParentSnapshot",
    "AudienceReasonCode",
    "CompiledAudienceProjection",
    "SfuAudiencePolicyCompiler",
]
