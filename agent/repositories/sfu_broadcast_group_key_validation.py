"""Invariant checks shared by in-memory and SQL SFU group-key stores."""

from __future__ import annotations

from typing import Any, Sequence

from agent.services.sfu_broadcast_group_key_repository_port import (
    SfuGroupKeyEpochState,
    SfuGroupKeyMutationResult,
    SfuGroupKeyPackageWrite,
)
from agent.services.sfu_hub_secret_envelope import SfuHubSecretEnvelopePort
from agent.services.webrtc_group_key_authorization_service import GroupKeyEpochAuthorization

MAX_GROUP_KEY_PACKAGES = 250
MAX_GROUP_KEY_PACKAGE_BYTES = 8 * 1024
MAX_GROUP_KEY_TOTAL_BYTES = MAX_GROUP_KEY_PACKAGES * MAX_GROUP_KEY_PACKAGE_BYTES


class SfuBroadcastGroupKeyRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def mutation_result(
    status: str,
    *,
    state: SfuGroupKeyEpochState | None = None,
    replayed: bool = False,
    reason: str | None = None,
) -> SfuGroupKeyMutationResult:
    return SfuGroupKeyMutationResult(status, state, replayed, reason)  # type: ignore[arg-type]


def validate_state(state: SfuGroupKeyEpochState) -> None:
    authorization = state.authorization
    if (
        state.distribution_mode != "bounded_rewrap"
        or state.status != "active"
        or not 1 <= len(authorization.member_ids) <= MAX_GROUP_KEY_PACKAGES
        or authorization.membership_epoch is None
        or authorization.membership_epoch < 1
        or state.fencing_token < 1
        or state.version != 1
    ):
        raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_state_invalid")


def validate_packages(
    state: SfuGroupKeyEpochState,
    packages: tuple[SfuGroupKeyPackageWrite, ...],
    envelope: SfuHubSecretEnvelopePort,
) -> SfuGroupKeyMutationResult | None:
    expected_members = {
        member
        for member in state.authorization.member_ids
        if state.publisher_digest
        not in {
            candidate.digest
            for candidate in envelope.blind_candidates(
                purpose="sfu-group-key-subject",
                scope=f"{state.authorization.tenant_id}:{state.session_id}",
                value=member,
            )
        }
    }
    if len(packages) != len(expected_members) or {item.recipient_id for item in packages} != expected_members:
        return mutation_result("conflict", state=state, reason="sfu_group_package_set_mismatch")
    if (
        len(packages) > MAX_GROUP_KEY_PACKAGES
        or sum(len(item.opaque_package) for item in packages) > MAX_GROUP_KEY_TOTAL_BYTES
        or any(not 1 <= len(item.opaque_package) <= MAX_GROUP_KEY_PACKAGE_BYTES for item in packages)
        or len({item.package_ref for item in packages}) != len(packages)
        or any(state.authorization.key_package_refs.get(item.recipient_id) != item.package_ref for item in packages)
    ):
        return mutation_result("conflict", state=state, reason="sfu_group_package_bounds_exceeded")
    return None


def publisher_id(
    authorization: GroupKeyEpochAuthorization,
    session_id: str,
    publisher_digest: str,
    envelope: SfuHubSecretEnvelopePort,
) -> str:
    for member in authorization.member_ids:
        candidates = envelope.blind_candidates(
            purpose="sfu-group-key-subject",
            scope=f"{authorization.tenant_id}:{session_id}",
            value=member,
        )
        if any(candidate.digest == publisher_digest for candidate in candidates):
            return member
    raise SfuBroadcastGroupKeyRepositoryError("sfu_group_publisher_unavailable")


def same_packages(existing: Sequence[Any], desired: tuple[SfuGroupKeyPackageWrite, ...]) -> bool:
    left = {(row.package_ref, row.recipient_digest, row.package_digest, row.package_bytes) for row in existing}
    right = {(row.package_ref, row.recipient_digest, row.package_digest, len(row.opaque_package)) for row in desired}
    return left == right
