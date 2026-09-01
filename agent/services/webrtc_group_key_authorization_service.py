"""Hub authorization for opaque client-managed group key epochs."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import replace
from typing import Mapping, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agent.models.sfu_group_keys import GroupKeyEpochAuthorization
from agent.repositories.webrtc_epoch_repository import WebrtcEpochRepository
from agent.services.sfu_broadcast_participant_limits import SFU_BROADCAST_MAX_GROUP_MEMBERS


class GroupKeyAuthorizationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class WebrtcGroupKeyAuthorizationService:
    """Signs membership/epoch metadata; content keys never cross this port."""

    def __init__(
        self,
        *,
        private_key: Ed25519PrivateKey,
        hub_key_id: str,
        epoch_repository: WebrtcEpochRepository | None = None,
        clock=time.time,
    ) -> None:
        self._private_key = private_key
        self._hub_key_id = hub_key_id
        self._epochs = epoch_repository or WebrtcEpochRepository()
        self._clock = clock

    def authorize(
        self,
        *,
        tenant_id: str,
        room_id: str,
        publication_id: str,
        epoch: int,
        previous_epoch: int,
        active_member_ids: Sequence[str],
        key_package_refs: Mapping[str, str],
        reason: str,
        rekey_deadline_ms: int,
        expires_at_ms: int,
        membership_epoch: int | None = None,
        authority_scope_kind: str = "room",
        authority_scope_id: str | None = None,
    ) -> GroupKeyEpochAuthorization:
        if reason not in {"create", "join", "leave", "revoke", "hub_failover", "refresh"}:
            raise GroupKeyAuthorizationError("rekey_reason_invalid")
        scope_id = authority_scope_id or room_id
        current = self._epochs.get(authority_scope_kind, scope_id)
        if current is None or current.closed_at is not None or current.epoch != epoch:
            raise GroupKeyAuthorizationError("epoch_not_authoritative")
        members = tuple(sorted(set(active_member_ids)))
        if not 1 <= len(members) <= SFU_BROADCAST_MAX_GROUP_MEMBERS or any(not _id(value) for value in members):
            raise GroupKeyAuthorizationError("member_set_invalid")
        if set(key_package_refs) != set(members):
            raise GroupKeyAuthorizationError("key_package_set_mismatch")
        if any(not _id(ref) for ref in key_package_refs.values()):
            raise GroupKeyAuthorizationError("key_package_ref_invalid")
        if previous_epoch >= epoch or previous_epoch < 0:
            raise GroupKeyAuthorizationError("epoch_transition_invalid")
        if membership_epoch is not None and (
            not isinstance(membership_epoch, int)
            or isinstance(membership_epoch, bool)
            or membership_epoch < 1
        ):
            raise GroupKeyAuthorizationError("membership_epoch_invalid")
        now_ms = int(self._clock() * 1000)
        if not now_ms <= rekey_deadline_ms <= now_ms + 30_000:
            raise GroupKeyAuthorizationError("rekey_deadline_invalid")
        if not rekey_deadline_ms <= expires_at_ms <= now_ms + 10 * 60 * 1000:
            raise GroupKeyAuthorizationError("group_authorization_expiry_invalid")

        digest = member_set_digest(members)
        auth = GroupKeyEpochAuthorization(
            version=1,
            authorization_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            room_id=room_id,
            publication_id=publication_id,
            epoch=epoch,
            previous_epoch=previous_epoch,
            member_set_digest=digest,
            member_ids=members,
            key_package_refs=dict(sorted(key_package_refs.items())),
            valid_from_ms=now_ms,
            expires_at_ms=expires_at_ms,
            rekey_deadline_ms=rekey_deadline_ms,
            reason=reason,
            hub_key_id=self._hub_key_id,
            membership_epoch=membership_epoch,
        )
        signature = self._private_key.sign(_canonical(auth.unsigned_dict()))
        return replace(auth, signature_b64=base64.b64encode(signature).decode("ascii"))

    @property
    def hub_key_id(self) -> str:
        return self._hub_key_id

    def hub_public_key_b64(self) -> str:
        raw = self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")


def member_set_digest(member_ids: Sequence[str]) -> str:
    canonical = json.dumps(sorted(set(member_ids)), separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value[0].isalnum()
        and all(char.isalnum() or char in "._:-" for char in value)
    )


__all__ = [
    "GroupKeyAuthorizationError",
    "GroupKeyEpochAuthorization",
    "WebrtcGroupKeyAuthorizationService",
    "member_set_digest",
]
