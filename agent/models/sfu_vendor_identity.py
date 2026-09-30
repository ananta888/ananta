"""Hub-owned opaque SFU vendor identity and destination bindings."""

from __future__ import annotations

from dataclasses import dataclass

from agent.models.sfu_group_keys import SfuHubSealedSecret


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


__all__ = [
    "SfuVendorDestinationBinding",
    "SfuVendorIdentityBinding",
    "SfuVendorIdentityError",
    "SfuVendorIdentityMutationResult",
]
