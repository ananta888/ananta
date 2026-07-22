"""Hub-owned opaque LiveKit identities and destination authorization handles."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from agent.services.sfu_broadcast_data_port import AuthorizedSfuDataAudienceV1
from agent.services.sfu_hub_secret_envelope import (
    SfuHubSealedSecret,
    SfuHubSecretEnvelopeError,
    SfuHubSecretEnvelopePort,
)


MAX_VENDOR_IDENTITY_TTL_SECONDS = 3600
MAX_DESTINATION_TTL_SECONDS = 300


class SfuVendorIdentityError(RuntimeError):
    def __init__(self, reason_code: str, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuVendorIdentityBinding:
    identity_handle: str
    tenant_id: str
    room_id: str
    membership_digest: str
    sealed_membership: SfuHubSealedSecret | None
    membership_epoch: int
    identity_epoch: int
    status: str
    fencing_token: int
    version: int
    issued_at: float
    expires_at: float
    revoked_at: float | None = None
    membership_digest_key_id: str | None = None


@dataclass(frozen=True, slots=True)
class SfuVendorDestinationBinding:
    destination_handle: str
    identity_handle: str
    tenant_id: str
    room_id: str
    route_digest: str
    publication_digest: str
    audience_digest: str
    membership_epoch: int
    identity_epoch: int
    route_epoch: int
    key_epoch: int
    status: str
    fencing_token: int
    version: int
    issued_at: float
    expires_at: float
    revoked_at: float | None = None


@dataclass(frozen=True, slots=True)
class SfuVendorIdentityMutationResult:
    status: str
    identity: SfuVendorIdentityBinding | None = None
    destination: SfuVendorDestinationBinding | None = None
    replayed: bool = False
    reason_code: str | None = None

    @property
    def committed(self) -> bool:
        return self.status == "saved"


@runtime_checkable
class SfuVendorIdentityRepositoryPort(Protocol):
    def active_for_membership(
        self, *, tenant_id: str, room_id: str, membership_digest: str,
        membership_epoch: int, identity_epoch: int,
        membership_digest_candidates: tuple[str, ...] = (),
        membership_digest_key_id: str | None = None,
    ) -> SfuVendorIdentityBinding | None: ...

    def get_identity(self, *, tenant_id: str, room_id: str, identity_handle: str) -> SfuVendorIdentityBinding | None: ...

    def save_identity(
        self, binding: SfuVendorIdentityBinding, *, expected_version: int,
    ) -> SfuVendorIdentityMutationResult: ...

    def save_destination(
        self, binding: SfuVendorDestinationBinding,
    ) -> SfuVendorIdentityMutationResult: ...

    def get_destination(
        self, *, tenant_id: str, room_id: str, destination_handle: str,
    ) -> SfuVendorDestinationBinding | None: ...

    def revoke_scope(
        self, *, tenant_id: str, room_id: str, now: float,
        membership_digest: str | None = None, before_membership_epoch: int | None = None,
        minimum_fencing_token: int,
    ) -> int: ...

    def purge_expired(self, *, now: float, limit: int) -> int: ...


class SfuVendorIdentityService:
    """The only component allowed to reverse an opaque identity binding."""

    def __init__(
        self,
        repository: SfuVendorIdentityRepositoryPort,
        envelope: SfuHubSecretEnvelopePort,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._envelope = envelope
        self._clock = clock

    def issue_identity(
        self,
        *,
        tenant_id: str,
        room_id: str,
        membership_ref: str,
        membership_epoch: int,
        identity_epoch: int,
        ttl_seconds: int = 600,
        fencing_token: int,
    ) -> SfuVendorIdentityBinding:
        _scope(tenant_id, room_id)
        _identifier(membership_ref, "membership_ref")
        _epochs(membership_epoch, identity_epoch, fencing_token)
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= MAX_VENDOR_IDENTITY_TTL_SECONDS:
            raise SfuVendorIdentityError("sfu_vendor_identity_ttl_invalid")
        now = float(self._clock())
        digest_index = self._envelope.blind_index(
            purpose="sfu-vendor-membership",
            scope=f"{tenant_id}:{room_id}",
            value=membership_ref,
        )
        digest = digest_index.digest
        current = self._repository.active_for_membership(
            tenant_id=tenant_id,
            room_id=room_id,
            membership_digest=digest,
            membership_epoch=membership_epoch,
            identity_epoch=identity_epoch,
            membership_digest_candidates=tuple(
                item.digest
                for item in self._envelope.blind_candidates(
                    purpose="sfu-vendor-membership",
                    scope=f"{tenant_id}:{room_id}",
                    value=membership_ref,
                )
            ),
            membership_digest_key_id=digest_index.key_id,
        )
        if current is not None and current.status == "active" and current.expires_at >= now + 30:
            return current
        if current is not None:
            fencing_token = max(fencing_token, current.fencing_token + 1)
            self._repository.revoke_scope(
                tenant_id=tenant_id,
                room_id=room_id,
                membership_digest=current.membership_digest,
                now=now,
                minimum_fencing_token=fencing_token,
            )
        for _attempt in range(3):
            handle = "lk_" + secrets.token_urlsafe(24)
            aad = _identity_aad(handle, tenant_id, room_id, membership_epoch, identity_epoch)
            sealed = self._envelope.seal(
                membership_ref.encode("utf-8"),
                purpose="sfu-vendor-membership",
                scope=f"{tenant_id}:{room_id}",
                aad=aad,
            )
            binding = SfuVendorIdentityBinding(
                identity_handle=handle,
                tenant_id=tenant_id,
                room_id=room_id,
                membership_digest=digest,
                sealed_membership=sealed,
                membership_digest_key_id=digest_index.key_id,
                membership_epoch=membership_epoch,
                identity_epoch=identity_epoch,
                status="active",
                fencing_token=fencing_token,
                version=1,
                issued_at=now,
                expires_at=now + ttl_seconds,
            )
            result = self._repository.save_identity(binding, expected_version=0)
            if result.committed and result.identity is not None:
                return result.identity
            if result.reason_code != "sfu_vendor_identity_handle_collision":
                raise SfuVendorIdentityError(result.reason_code or "sfu_vendor_identity_store_conflict")
        raise SfuVendorIdentityError("sfu_vendor_identity_handle_collision")

    def resolve_identity(
        self,
        *,
        tenant_id: str,
        room_id: str,
        identity_handle: str,
        membership_epoch: int,
        identity_epoch: int,
    ) -> str:
        binding = self._active_identity(
            tenant_id=tenant_id,
            room_id=room_id,
            identity_handle=identity_handle,
            membership_epoch=membership_epoch,
            identity_epoch=identity_epoch,
        )
        if binding.sealed_membership is None:
            raise SfuVendorIdentityError("sfu_vendor_identity_revoked", 410)
        aad = _identity_aad(
            binding.identity_handle, binding.tenant_id, binding.room_id,
            binding.membership_epoch, binding.identity_epoch,
        )
        try:
            raw = self._envelope.open(
                binding.sealed_membership,
                purpose="sfu-vendor-membership",
                scope=f"{tenant_id}:{room_id}",
                aad=aad,
            )
            return raw.decode("utf-8")
        except (SfuHubSecretEnvelopeError, UnicodeError) as exc:
            raise SfuVendorIdentityError("sfu_vendor_identity_binding_invalid", 503) from exc

    def authorize_destination(
        self,
        *,
        tenant_id: str,
        room_id: str,
        identity_handle: str,
        membership_epoch: int,
        identity_epoch: int,
        route_ref: str,
        publication_ref: str,
        audience_ref: str,
        route_epoch: int,
        key_epoch: int,
        fencing_token: int,
        ttl_seconds: int = 60,
    ) -> SfuVendorDestinationBinding:
        identity = self._active_identity(
            tenant_id=tenant_id, room_id=room_id, identity_handle=identity_handle,
            membership_epoch=membership_epoch, identity_epoch=identity_epoch,
        )
        for value, label in (
            (route_ref, "route_ref"), (publication_ref, "publication_ref"), (audience_ref, "audience_ref")
        ):
            _identifier(value, label)
        _epochs(route_epoch, key_epoch, fencing_token)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_DESTINATION_TTL_SECONDS:
            raise SfuVendorIdentityError("sfu_destination_ttl_invalid")
        now = float(self._clock())
        expires = min(identity.expires_at, now + ttl_seconds)
        if expires <= now:
            raise SfuVendorIdentityError("sfu_vendor_identity_expired", 410)
        binding = SfuVendorDestinationBinding(
            destination_handle="dst_" + secrets.token_urlsafe(24),
            identity_handle=identity.identity_handle,
            tenant_id=tenant_id,
            room_id=room_id,
            route_digest=self._intent_digest("route", tenant_id, room_id, route_ref),
            publication_digest=self._intent_digest("publication", tenant_id, room_id, publication_ref),
            audience_digest=self._intent_digest("audience", tenant_id, room_id, audience_ref),
            membership_epoch=membership_epoch,
            identity_epoch=identity_epoch,
            route_epoch=route_epoch,
            key_epoch=key_epoch,
            status="active",
            fencing_token=fencing_token,
            version=1,
            issued_at=now,
            expires_at=expires,
        )
        result = self._repository.save_destination(binding)
        if not result.committed or result.destination is None:
            raise SfuVendorIdentityError(result.reason_code or "sfu_destination_store_conflict")
        return result.destination

    def resolve_destination(
        self,
        *,
        tenant_id: str,
        room_id: str,
        destination_handle: str,
        route_ref: str,
        publication_ref: str,
        audience_ref: str,
        membership_epoch: int,
        route_epoch: int,
        key_epoch: int,
        fencing_token: int,
    ) -> str:
        destination = self._repository.get_destination(
            tenant_id=tenant_id, room_id=room_id, destination_handle=destination_handle
        )
        now = float(self._clock())
        expected = (
            self._intent_digest("route", tenant_id, room_id, route_ref),
            self._intent_digest("publication", tenant_id, room_id, publication_ref),
            self._intent_digest("audience", tenant_id, room_id, audience_ref),
            membership_epoch, route_epoch, key_epoch, fencing_token,
        )
        actual = None if destination is None else (
            destination.route_digest, destination.publication_digest, destination.audience_digest,
            destination.membership_epoch, destination.route_epoch, destination.key_epoch,
            destination.fencing_token,
        )
        if (
            destination is None or destination.status != "active" or destination.expires_at <= now
            or actual != expected
        ):
            raise SfuVendorIdentityError("sfu_destination_authorization_stale", 403)
        identity = self._active_identity(
            tenant_id=tenant_id, room_id=room_id,
            identity_handle=destination.identity_handle,
            membership_epoch=destination.membership_epoch,
            identity_epoch=destination.identity_epoch,
        )
        return identity.identity_handle

    def authorized_data_audience(
        self,
        *,
        tenant_id: str,
        room_id: str,
        publication_ref: str,
        audience_ref: str,
        route_ref: str,
        destination_handles: tuple[str, ...],
        membership_epoch: int,
        route_epoch: int,
        key_epoch: int,
        fencing_token: int,
        expires_at_ms: int,
    ) -> AuthorizedSfuDataAudienceV1:
        identities = tuple(sorted({
            self.resolve_destination(
                tenant_id=tenant_id, room_id=room_id, destination_handle=handle,
                route_ref=route_ref, publication_ref=publication_ref, audience_ref=audience_ref,
                membership_epoch=membership_epoch, route_epoch=route_epoch,
                key_epoch=key_epoch, fencing_token=fencing_token,
            )
            for handle in destination_handles
        }))
        if len(identities) != len(destination_handles):
            raise SfuVendorIdentityError("sfu_destination_identity_duplicate")
        return AuthorizedSfuDataAudienceV1(
            tenant_ref=tenant_id,
            room_ref=room_id,
            publication_ref=publication_ref,
            audience_ref=audience_ref,
            membership_epoch=membership_epoch,
            route_epoch=route_epoch,
            key_epoch=key_epoch,
            fencing_token=str(fencing_token),
            destination_handles=identities,
            expires_at_ms=expires_at_ms,
        )

    def revoke_membership(
        self, *, tenant_id: str, room_id: str, membership_ref: str,
        fencing_token: int,
    ) -> int:
        return self._repository.revoke_scope(
            tenant_id=tenant_id, room_id=room_id,
            membership_digest=self._membership_digest(tenant_id, room_id, membership_ref),
            now=float(self._clock()), minimum_fencing_token=fencing_token,
        )

    def revoke_room_before_epoch(
        self, *, tenant_id: str, room_id: str, membership_epoch: int,
        fencing_token: int,
    ) -> int:
        return self._repository.revoke_scope(
            tenant_id=tenant_id, room_id=room_id,
            before_membership_epoch=membership_epoch,
            now=float(self._clock()), minimum_fencing_token=fencing_token,
        )

    def purge_expired(self, *, limit: int = 200) -> int:
        return self._repository.purge_expired(now=float(self._clock()), limit=limit)

    def _active_identity(
        self, *, tenant_id: str, room_id: str, identity_handle: str,
        membership_epoch: int, identity_epoch: int,
    ) -> SfuVendorIdentityBinding:
        binding = self._repository.get_identity(
            tenant_id=tenant_id, room_id=room_id, identity_handle=identity_handle
        )
        if (
            binding is None or binding.status != "active" or binding.expires_at <= float(self._clock())
            or binding.membership_epoch != membership_epoch or binding.identity_epoch != identity_epoch
        ):
            raise SfuVendorIdentityError("sfu_vendor_identity_stale", 403)
        return binding

    def _membership_digest(self, tenant_id: str, room_id: str, membership_ref: str) -> str:
        return self._envelope.blind(
            purpose="sfu-vendor-membership", scope=f"{tenant_id}:{room_id}", value=membership_ref
        )

    def _intent_digest(self, purpose: str, tenant_id: str, room_id: str, value: str) -> str:
        return self._envelope.blind(
            purpose=f"sfu-destination-{purpose}", scope=f"{tenant_id}:{room_id}", value=value
        )


def _identity_aad(handle: str, tenant: str, room: str, membership_epoch: int, identity_epoch: int) -> bytes:
    return f"ananta:sfu-vendor-identity:v1\0{handle}\0{tenant}\0{room}\0{membership_epoch}\0{identity_epoch}".encode()


def _scope(tenant_id: str, room_id: str) -> None:
    _identifier(tenant_id, "tenant_id")
    _identifier(room_id, "room_id")


def _identifier(value: object, label: str) -> str:
    if (
        not isinstance(value, str) or not 1 <= len(value) <= 128
        or not value[0].isalnum()
        or any(not (character.isalnum() or character in "._:-") for character in value)
    ):
        raise SfuVendorIdentityError(f"sfu_vendor_{label}_invalid", 400)
    return value


def _epochs(first: int, second: int, fencing_token: int) -> None:
    if any(type(value) is not int or value < 1 for value in (first, second, fencing_token)):
        raise SfuVendorIdentityError("sfu_vendor_epoch_invalid", 400)


__all__ = [
    "MAX_DESTINATION_TTL_SECONDS",
    "MAX_VENDOR_IDENTITY_TTL_SECONDS",
    "SfuVendorDestinationBinding",
    "SfuVendorIdentityBinding",
    "SfuVendorIdentityError",
    "SfuVendorIdentityMutationResult",
    "SfuVendorIdentityRepositoryPort",
    "SfuVendorIdentityService",
]
