from __future__ import annotations

import base64
import hashlib
import hmac
import random

from agent.services.sfu_member_digest_key_provider import SfuMemberDigest
from agent.services.sfu_receiver_group_projector import (
    ReceiverGroupEvent,
    ReceiverGroupEventKind,
    ReceiverGroupMember,
    ReceiverGroupProjectionMode,
    ReceiverGroupProjectionRequest,
    ReceiverGroupProjectionState,
    ReceiverGroupProjectorConfig,
    SfuReceiverGroupProjector,
)


class Digests:
    def create_digest(self, *, member_identifier, scope):
        raw = hmac.new(hashlib.sha256(scope.encode()).digest(), member_identifier.encode(), hashlib.sha256).digest()
        return SfuMemberDigest("HMAC-SHA256", "hub-member-key", scope, base64.urlsafe_b64encode(raw).decode().rstrip("="))


class CollidingDigests:
    def create_digest(self, *, member_identifier, scope):
        raw = b"x" * 32
        return SfuMemberDigest("HMAC-SHA256", "hub-member-key", scope, base64.urlsafe_b64encode(raw).decode().rstrip("="))


def member(ref, privacy="team", layer="medium"):
    return ReceiverGroupMember(ref, f"sub-{ref}", "tenant-a", "room-a", "publication-a", privacy, layer, 4, 5)


def empty_state(room="room-a", key_epoch=5):
    return ReceiverGroupProjectionState("tenant-a", room, "publication-a", 4, key_epoch, 2, 0, ())


def projector(provider=None, members_per_group=2):
    return SfuReceiverGroupProjector(
        digest_provider=provider or Digests(),
        config=ReceiverGroupProjectorConfig(4, members_per_group, 100, 100),
    )


def test_seeded_incremental_matches_full_rebuild_under_reorder_and_join_leave_storm() -> None:
    rng = random.Random(7)
    initial = tuple(member(f"receiver-{index:02}", layer="low" if index % 2 else "medium") for index in range(8))
    state = projector().full_rebuild(empty_state(), initial)
    authoritative = {item.stable_ref: item for item in initial}
    sequence = 0
    for index in range(20):
        sequence += 1
        if rng.random() < 0.5 and authoritative:
            ref = rng.choice(sorted(authoritative))
            authoritative.pop(ref)
            event = ReceiverGroupEvent(f"event-{index}", sequence, ReceiverGroupEventKind.LEAVE, member_ref=ref)
        else:
            item = member(f"joined-{index:02}", layer=rng.choice(("low", "medium")))
            authoritative[item.stable_ref] = item
            event = ReceiverGroupEvent(f"event-{index}", sequence, ReceiverGroupEventKind.JOIN, member=item)
        result = projector().project(ReceiverGroupProjectionRequest(state, (event,), tuple(authoritative.values())))
        assert result.accepted
        expected = projector().full_rebuild(result.state, tuple(reversed(tuple(authoritative.values()))), last_sequence=sequence, event_receipts=result.state.event_receipts)
        assert result.state.groups == expected.groups
        state = result.state


def test_duplicate_is_idempotent_and_gap_uses_bounded_full_rebuild() -> None:
    item = member("receiver-a")
    event = ReceiverGroupEvent("event-1", 1, ReceiverGroupEventKind.JOIN, member=item)
    first = projector().project(ReceiverGroupProjectionRequest(empty_state(), (event,), (item,)))
    duplicate = projector().project(ReceiverGroupProjectionRequest(first.state, (event,), (item,)))
    assert duplicate.accepted and duplicate.state == first.state
    gap_item = member("receiver-b")
    gap = ReceiverGroupEvent("event-3", 3, ReceiverGroupEventKind.JOIN, member=gap_item)
    rebuilt = projector().project(ReceiverGroupProjectionRequest(first.state, (gap,), (item, gap_item)))
    assert rebuilt.accepted and rebuilt.mode is ReceiverGroupProjectionMode.FULL_REBUILD


def test_digest_collision_never_authorizes_partial_state() -> None:
    members = (member("a"), member("b"))
    event = ReceiverGroupEvent("gap", 2, ReceiverGroupEventKind.JOIN, member=members[0])
    result = projector(CollidingDigests(), members_per_group=1).project(
        ReceiverGroupProjectionRequest(empty_state(), (event,), members)
    )
    assert not result.accepted
    assert result.state == empty_state()


def test_digest_has_no_cross_room_or_cross_epoch_linkability() -> None:
    item = member("receiver-a")
    room_a = projector().full_rebuild(empty_state(), (item,)).groups[0].membership_digest
    room_b_item = ReceiverGroupMember("receiver-a", "sub-a", "tenant-a", "room-b", "publication-a", "team", "medium", 4, 5)
    room_b = projector().full_rebuild(empty_state(room="room-b"), (room_b_item,)).groups[0].membership_digest
    epoch_item = ReceiverGroupMember("receiver-a", "sub-a", "tenant-a", "room-a", "publication-a", "team", "medium", 4, 6)
    epoch = projector().full_rebuild(empty_state(key_epoch=6), (epoch_item,)).groups[0].membership_digest
    assert len({room_a, room_b, epoch}) == 3
