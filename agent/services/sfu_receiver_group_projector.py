"""Deterministic, bounded Hub projector for SFU receiver groups."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Protocol

from agent.services.sfu_member_digest_key_provider import SfuMemberDigest


class ReceiverGroupEventKind(str, Enum):
    JOIN = "join"
    LEAVE = "leave"
    REVOKE = "revoke"
    POLICY_CHANGE = "policy_change"


class ReceiverGroupProjectionMode(str, Enum):
    INCREMENTAL = "incremental"
    FULL_REBUILD = "full_rebuild"
    DENIED = "denied"


class ReceiverGroupProjectionReason(str, Enum):
    APPLIED = "group_projection_applied"
    DUPLICATE_IGNORED = "group_projection_duplicate_ignored"
    EVENT_GAP = "group_projection_event_gap"
    EVENT_CONFLICT = "group_projection_event_conflict"
    CHANGE_LIMIT = "group_projection_change_limit"
    DEADLINE = "group_projection_deadline"
    DIGEST_FAILURE = "group_projection_digest_failure"
    DIGEST_COLLISION = "group_projection_digest_collision"
    FULL_REBUILD_UNAVAILABLE = "group_projection_full_rebuild_unavailable"
    FULL_REBUILD_LIMIT = "group_projection_full_rebuild_limit"
    SCOPE_MISMATCH = "group_projection_scope_mismatch"


@dataclass(frozen=True, slots=True)
class ReceiverGroupProjectorConfig:
    groups_per_event_max: int = 8
    members_per_group_max: int = 7
    recompute_deadline_ms: int = 250
    full_rebuild_members_max: int = 2_000

    def __post_init__(self) -> None:
        values = (
            self.groups_per_event_max,
            self.members_per_group_max,
            self.recompute_deadline_ms,
            self.full_rebuild_members_max,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("receiver_group_projector_config_invalid")


@dataclass(frozen=True, slots=True)
class ReceiverGroupMember:
    stable_ref: str
    subscription_ref: str
    tenant_id: str
    room_ref: str
    publication_ref: str
    privacy_scope: str
    layer_profile: str
    membership_epoch: int
    key_epoch: int

    @property
    def bucket(self) -> tuple[str, str, str]:
        return self.publication_ref, self.privacy_scope, self.layer_profile


@dataclass(frozen=True, slots=True)
class ReceiverGroupEvent:
    event_id: str
    sequence: int
    kind: ReceiverGroupEventKind
    member: ReceiverGroupMember | None = None
    member_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectedReceiverGroup:
    group_ref: str
    subscription_ref: str
    publication_ref: str
    privacy_scope: str
    layer_profile: str
    member_refs: tuple[str, ...]
    group_digest: str
    membership_digest: str
    key_digest: str
    membership_epoch: int
    key_epoch: int
    topology_epoch: int

    @property
    def bucket(self) -> tuple[str, str, str]:
        return self.publication_ref, self.privacy_scope, self.layer_profile


@dataclass(frozen=True, slots=True)
class ReceiverGroupProjectionState:
    tenant_id: str
    room_ref: str
    publication_ref: str
    membership_epoch: int
    key_epoch: int
    topology_epoch: int
    last_sequence: int
    groups: tuple[ProjectedReceiverGroup, ...]
    event_receipts: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ReceiverGroupProjectionRequest:
    previous: ReceiverGroupProjectionState
    events: tuple[ReceiverGroupEvent, ...]
    authoritative_members: tuple[ReceiverGroupMember, ...] | None
    elapsed_ms: int = 0


@dataclass(frozen=True, slots=True)
class ReceiverGroupProjectionResult:
    state: ReceiverGroupProjectionState
    mode: ReceiverGroupProjectionMode
    reason_code: ReceiverGroupProjectionReason
    accepted: bool
    affected_buckets: tuple[tuple[str, str, str], ...]


class MemberDigestCreatePort(Protocol):
    def create_digest(
        self, *, member_identifier: str, scope: str
    ) -> SfuMemberDigest: ...


class SfuReceiverGroupProjector:
    def __init__(
        self,
        *,
        digest_provider: MemberDigestCreatePort,
        config: ReceiverGroupProjectorConfig,
    ) -> None:
        self._digests = digest_provider
        self._config = config

    def project(
        self, request: ReceiverGroupProjectionRequest
    ) -> ReceiverGroupProjectionResult:
        previous = request.previous
        if request.elapsed_ms > self._config.recompute_deadline_ms:
            return self._fallback(request, ReceiverGroupProjectionReason.DEADLINE)
        receipts = dict(previous.event_receipts)
        new_events: list[ReceiverGroupEvent] = []
        for event in sorted(request.events, key=lambda item: item.sequence):
            digest = _canonical_digest(asdict(event))
            if event.sequence <= previous.last_sequence:
                if receipts.get(event.sequence) == digest:
                    continue
                return self._fallback(
                    request, ReceiverGroupProjectionReason.EVENT_CONFLICT
                )
            new_events.append(event)
        if new_events:
            expected = previous.last_sequence + 1
            for event in new_events:
                if event.sequence != expected:
                    return self._fallback(
                        request, ReceiverGroupProjectionReason.EVENT_GAP
                    )
                expected += 1
        members = self._members_from_state(previous)
        affected: set[tuple[str, str, str]] = set()
        for event in new_events:
            old = members.get(event.member_ref or (event.member.stable_ref if event.member else ""))
            if old is not None:
                affected.add(old.bucket)
            if event.kind in (
                ReceiverGroupEventKind.JOIN,
                ReceiverGroupEventKind.POLICY_CHANGE,
            ):
                if event.member is None or not self._member_in_scope(previous, event.member):
                    return self._fallback(
                        request, ReceiverGroupProjectionReason.SCOPE_MISMATCH
                    )
                members[event.member.stable_ref] = event.member
                affected.add(event.member.bucket)
            else:
                member_ref = event.member_ref or (
                    event.member.stable_ref if event.member else None
                )
                if member_ref is not None:
                    members.pop(member_ref, None)

        if len(affected) > max(1, len(new_events)) * self._config.groups_per_event_max:
            return self._fallback(request, ReceiverGroupProjectionReason.CHANGE_LIMIT)
        try:
            rebuilt = self._build_groups(previous, tuple(members.values()), affected)
        except _DigestCollision:
            return self._fallback(
                request, ReceiverGroupProjectionReason.DIGEST_COLLISION
            )
        except Exception:
            return self._fallback(
                request, ReceiverGroupProjectionReason.DIGEST_FAILURE
            )
        changed_group_count = sum(
            1 for group in rebuilt if group.bucket in affected
        )
        if changed_group_count > max(1, len(new_events)) * self._config.groups_per_event_max:
            return self._fallback(request, ReceiverGroupProjectionReason.CHANGE_LIMIT)
        for event in new_events:
            receipts[event.sequence] = _canonical_digest(asdict(event))
        last_sequence = (
            new_events[-1].sequence if new_events else previous.last_sequence
        )
        state = ReceiverGroupProjectionState(
            tenant_id=previous.tenant_id,
            room_ref=previous.room_ref,
            publication_ref=previous.publication_ref,
            membership_epoch=previous.membership_epoch,
            key_epoch=previous.key_epoch,
            topology_epoch=previous.topology_epoch,
            last_sequence=last_sequence,
            groups=rebuilt,
            event_receipts=tuple(sorted(receipts.items())),
        )
        reason = (
            ReceiverGroupProjectionReason.APPLIED
            if new_events
            else ReceiverGroupProjectionReason.DUPLICATE_IGNORED
        )
        return ReceiverGroupProjectionResult(
            state,
            ReceiverGroupProjectionMode.INCREMENTAL,
            reason,
            True,
            tuple(sorted(affected)),
        )

    def full_rebuild(
        self,
        state: ReceiverGroupProjectionState,
        members: tuple[ReceiverGroupMember, ...],
        *,
        last_sequence: int | None = None,
        event_receipts: tuple[tuple[int, str], ...] | None = None,
    ) -> ReceiverGroupProjectionState:
        if len(members) > self._config.full_rebuild_members_max:
            raise ValueError(ReceiverGroupProjectionReason.FULL_REBUILD_LIMIT.value)
        if any(not self._member_in_scope(state, member) for member in members):
            raise ValueError(ReceiverGroupProjectionReason.SCOPE_MISMATCH.value)
        groups = self._build_groups(state, members, None)
        return ReceiverGroupProjectionState(
            tenant_id=state.tenant_id,
            room_ref=state.room_ref,
            publication_ref=state.publication_ref,
            membership_epoch=state.membership_epoch,
            key_epoch=state.key_epoch,
            topology_epoch=state.topology_epoch,
            last_sequence=state.last_sequence if last_sequence is None else last_sequence,
            groups=groups,
            event_receipts=(
                state.event_receipts if event_receipts is None else event_receipts
            ),
        )

    def _fallback(
        self,
        request: ReceiverGroupProjectionRequest,
        trigger: ReceiverGroupProjectionReason,
    ) -> ReceiverGroupProjectionResult:
        if request.authoritative_members is None:
            return ReceiverGroupProjectionResult(
                request.previous,
                ReceiverGroupProjectionMode.DENIED,
                ReceiverGroupProjectionReason.FULL_REBUILD_UNAVAILABLE,
                False,
                (),
            )
        if len(request.authoritative_members) > self._config.full_rebuild_members_max:
            return ReceiverGroupProjectionResult(
                request.previous,
                ReceiverGroupProjectionMode.DENIED,
                ReceiverGroupProjectionReason.FULL_REBUILD_LIMIT,
                False,
                (),
            )
        receipts = dict(request.previous.event_receipts)
        for event in request.events:
            receipts[event.sequence] = _canonical_digest(asdict(event))
        sequence = max(
            (event.sequence for event in request.events),
            default=request.previous.last_sequence,
        )
        try:
            rebuilt = self.full_rebuild(
                request.previous,
                request.authoritative_members,
                last_sequence=sequence,
                event_receipts=tuple(sorted(receipts.items())),
            )
        except _DigestCollision:
            reason = ReceiverGroupProjectionReason.DIGEST_COLLISION
        except Exception:
            reason = ReceiverGroupProjectionReason.DIGEST_FAILURE
        else:
            return ReceiverGroupProjectionResult(
                rebuilt,
                ReceiverGroupProjectionMode.FULL_REBUILD,
                trigger,
                True,
                tuple(sorted({member.bucket for member in request.authoritative_members})),
            )
        return ReceiverGroupProjectionResult(
            request.previous,
            ReceiverGroupProjectionMode.DENIED,
            reason,
            False,
            (),
        )

    def _build_groups(
        self,
        state: ReceiverGroupProjectionState,
        members: tuple[ReceiverGroupMember, ...],
        affected: set[tuple[str, str, str]] | None,
    ) -> tuple[ProjectedReceiverGroup, ...]:
        retained = (
            []
            if affected is None
            else [group for group in state.groups if group.bucket not in affected]
        )
        buckets: dict[tuple[str, str, str], list[ReceiverGroupMember]] = {}
        for member in members:
            if affected is None or member.bucket in affected:
                buckets.setdefault(member.bucket, []).append(member)
        generated: list[ProjectedReceiverGroup] = []
        seen_digests: dict[str, tuple[str, ...]] = {
            group.membership_digest: group.member_refs for group in retained
        }
        for bucket in sorted(buckets):
            ordered = sorted(buckets[bucket], key=lambda member: member.stable_ref)
            for index in range(0, len(ordered), self._config.members_per_group_max):
                chunk = ordered[index : index + self._config.members_per_group_max]
                member_refs = tuple(member.stable_ref for member in chunk)
                canonical_members = _canonical_json(
                    {
                        "domain": "ananta:sfu-receiver-group-members:v1",
                        "members": member_refs,
                    }
                )
                scope = (
                    f"{state.tenant_id}/{state.room_ref}/{state.publication_ref}/"
                    f"key-epoch/{state.key_epoch}"
                )
                digest = self._digests.create_digest(
                    member_identifier=canonical_members, scope=scope
                )
                membership_digest = _digest_to_hex(digest)
                previous_members = seen_digests.get(membership_digest)
                if previous_members is not None and previous_members != member_refs:
                    raise _DigestCollision
                seen_digests[membership_digest] = member_refs
                ordinal = index // self._config.members_per_group_max
                identity = _canonical_digest(
                    {
                        "domain": "ananta:sfu-receiver-group-ref:v1",
                        "tenant_id": state.tenant_id,
                        "room_ref": state.room_ref,
                        "bucket": bucket,
                        "ordinal": ordinal,
                        "membership_epoch": state.membership_epoch,
                        "key_epoch": state.key_epoch,
                    }
                )
                group_ref = f"sfu-group-{identity[:24]}"
                subscription_ref = f"sfu-subscription-{identity[24:48]}"
                group_digest = _canonical_digest(
                    {
                        "domain": "ananta:sfu-receiver-group:v1",
                        "group_ref": group_ref,
                        "bucket": bucket,
                        "membership_digest": membership_digest,
                        "membership_epoch": state.membership_epoch,
                        "key_epoch": state.key_epoch,
                        "topology_epoch": state.topology_epoch,
                    }
                )
                generated.append(
                    ProjectedReceiverGroup(
                        group_ref=group_ref,
                        subscription_ref=subscription_ref,
                        publication_ref=bucket[0],
                        privacy_scope=bucket[1],
                        layer_profile=bucket[2],
                        member_refs=member_refs,
                        group_digest=group_digest,
                        membership_digest=membership_digest,
                        key_digest=hashlib.sha256(digest.key_id.encode()).hexdigest(),
                        membership_epoch=state.membership_epoch,
                        key_epoch=state.key_epoch,
                        topology_epoch=state.topology_epoch,
                    )
                )
        return tuple(sorted((*retained, *generated), key=lambda group: group.group_ref))

    @staticmethod
    def _members_from_state(
        state: ReceiverGroupProjectionState,
    ) -> dict[str, ReceiverGroupMember]:
        members: dict[str, ReceiverGroupMember] = {}
        for group in state.groups:
            for stable_ref in group.member_refs:
                members[stable_ref] = ReceiverGroupMember(
                    stable_ref=stable_ref,
                    subscription_ref=group.subscription_ref,
                    tenant_id=state.tenant_id,
                    room_ref=state.room_ref,
                    publication_ref=group.publication_ref,
                    privacy_scope=group.privacy_scope,
                    layer_profile=group.layer_profile,
                    membership_epoch=state.membership_epoch,
                    key_epoch=state.key_epoch,
                )
        return members

    @staticmethod
    def _member_in_scope(
        state: ReceiverGroupProjectionState, member: ReceiverGroupMember
    ) -> bool:
        return (
            member.tenant_id == state.tenant_id
            and member.room_ref == state.room_ref
            and member.publication_ref == state.publication_ref
            and member.membership_epoch == state.membership_epoch
            and member.key_epoch == state.key_epoch
        )


class _DigestCollision(RuntimeError):
    pass


def _digest_to_hex(value: SfuMemberDigest) -> str:
    if value.algorithm != "HMAC-SHA256":
        raise ValueError("group_digest_algorithm_invalid")
    raw = base64.urlsafe_b64decode(value.digest + "=" * (-len(value.digest) % 4))
    if len(raw) != hashlib.sha256().digest_size:
        raise ValueError("group_digest_length_invalid")
    return raw.hex()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


__all__ = [
    "ProjectedReceiverGroup",
    "ReceiverGroupEvent",
    "ReceiverGroupEventKind",
    "ReceiverGroupMember",
    "ReceiverGroupProjectionMode",
    "ReceiverGroupProjectionReason",
    "ReceiverGroupProjectionRequest",
    "ReceiverGroupProjectionResult",
    "ReceiverGroupProjectionState",
    "ReceiverGroupProjectorConfig",
    "SfuReceiverGroupProjector",
]
