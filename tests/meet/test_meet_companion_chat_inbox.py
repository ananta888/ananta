"""Buffering never acknowledges inputs, expands bounds or trusts changed delivery."""

from copy import deepcopy

import pytest

from worker.meet_media.companion_chat_inbox import ChatInboxError, CompanionChatInbox


def page(start=1, count=8):
    return {"schema": "ananta.meet-chat-batch.draft1", "events": [
        {"cursor": number, "event": {"message_id": f"{number:032x}", "text": "synthetic",
                                     "sender_peer_id": "synthetic-human"}}
        for number in range(start, start + count)]}


def test_all_four_pages_retained_until_explicit_processing_without_ack():
    inbox = CompanionChatInbox()
    for start in (1, 9, 17, 25):
        inbox.accept(page(start))
    assert inbox.cursor == 32
    assert inbox.batch()["events"][0]["cursor"] == 1
    assert "acknowledged" not in inbox.batch()
    for start in (1, 9, 17, 25):
        batch = inbox.batch()
        assert batch["events"][0]["cursor"] == start
        inbox.completed([item["event"]["message_id"] for item in batch["events"]])
    assert not inbox.batch()["events"]


def test_copies_and_exact_redelivery_cannot_mutate_or_duplicate_pending_input():
    inbox, original = CompanionChatInbox(), page()
    inbox.accept(original)
    inbox.accept(deepcopy(original))
    original["events"][0]["event"]["text"] = "changed"
    observed = inbox.batch()
    observed["events"][0]["event"]["text"] = "changed again"
    assert inbox.batch()["events"][0]["event"]["text"] == "synthetic"
    with pytest.raises(ChatInboxError, match="delivery_conflict"):
        inbox.accept(original)


def test_overflow_rejects_entire_page_and_clear_removes_content_and_cursor():
    inbox = CompanionChatInbox()
    for start in (1, 9, 17, 25):
        inbox.accept(page(start))
    with pytest.raises(ChatInboxError, match="exhausted"):
        inbox.accept(page(33))
    assert inbox.cursor == 32
    inbox.clear()
    assert inbox.cursor == 0 and not inbox.batch()["events"]
    inbox.accept(page())


def test_invalid_later_item_never_partially_admits_page():
    inbox, batch = CompanionChatInbox(), page()
    batch["events"][-1]["event"]["message_id"] += "\n"
    with pytest.raises(ChatInboxError, match="identity_invalid"):
        inbox.accept(batch)
    assert inbox.cursor == 0 and not inbox.batch()["events"]


def test_changed_cursor_and_excessive_content_are_denied():
    inbox = CompanionChatInbox()
    inbox.accept(page())
    batch = page()
    batch["events"][0]["cursor"] = 9
    with pytest.raises(ChatInboxError, match="delivery_conflict"):
        inbox.accept(batch)
    large = page(9, 1)
    large["events"][0]["event"]["text"] = "x" * 8193
    with pytest.raises(ChatInboxError, match="event_size"):
        inbox.accept(large)


def test_completion_validation_is_atomic_and_total_bytes_are_bounded():
    inbox = CompanionChatInbox()
    inbox.accept(page())
    with pytest.raises(ChatInboxError, match="completion_invalid"):
        inbox.completed([f"{1:032x}", "invalid"])
    assert inbox.batch()["events"][0]["cursor"] == 1
    large = page(9)
    for item in large["events"]:
        item["event"]["text"] = "x" * 8000
    inbox.accept(large)
    large = page(17)
    for item in large["events"]:
        item["event"]["text"] = "x" * 8000
    inbox.accept(large)
    large = page(25)
    for item in large["events"]:
        item["event"]["text"] = "x" * 8000
    with pytest.raises(ChatInboxError, match="exhausted"):
        inbox.accept(large)
    assert inbox.cursor == 24


def test_ack_cursor_is_processed_prefix_even_when_later_pages_arrive():
    inbox = CompanionChatInbox()
    inbox.accept(page())
    inbox.accept(page(9))
    assert inbox.completed([f"{number:032x}" for number in range(1, 9)]) == 8
    assert inbox.cursor == 16 and inbox.acknowledgable_cursor == 8
    with pytest.raises(ChatInboxError, match="completion_order_invalid"):
        inbox.completed([f"{16:032x}"])
    assert inbox.batch()["events"][0]["cursor"] == 9
    assert inbox.completed([]) is None


def scope(revision=1, **overrides):
    return {"origin": "https://meet.invalid", "tenant_id": "tenant", "project_id": "project",
            "task_id": "task", "session_id": "session", "runtime_id": "runtime", "lease_id": "lease",
            "room_id": "room-aaaaaaaaaaaaaaaaaa", "own_peer_id": "machine", "generation": revision,
            "membership_epoch": 1, "policy_revision": 1, "deadline_ms": 1_000_000, **overrides}


def bound_page(start=1, count=8, generation=1):
    value = page(start, count)
    for item in value["events"]:
        item["event"].update(session_id="session", room_id="room-aaaaaaaaaaaaaaaaaa",
                             generation=generation, membership_epoch=1)
    return value


def test_renewal_preserves_input_content_but_never_reuses_old_ack_cursor():
    inbox = CompanionChatInbox()
    inbox.bind(scope())
    inbox.accept(bound_page(1, 1))
    inbox.bind(scope(2))
    assert inbox.cursor == 0 and inbox.batch()["events"][0]["cursor"] is None
    assert inbox.completed([f"{1:032x}"]) is None


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "task_id", "session_id", "runtime_id",
                                  "lease_id", "room_id", "own_peer_id", "origin"])
def test_different_conversation_binding_wipes_held_input(field):
    inbox = CompanionChatInbox()
    inbox.bind(scope())
    inbox.accept(bound_page())
    with pytest.raises(ChatInboxError, match="scope_changed"):
        inbox.bind(scope(2, **{field: "foreign"}))
    assert not inbox.batch()["events"] and inbox.cursor == 0


def test_rebound_delivery_cannot_change_content_or_claim_old_generation():
    inbox = CompanionChatInbox()
    inbox.bind(scope())
    inbox.accept(bound_page(1, 1))
    inbox.bind(scope(2))
    with pytest.raises(ChatInboxError, match="event_scope_invalid"):
        inbox.accept(bound_page(1, 1))
    changed = bound_page(1, 1, generation=2)
    changed["events"][0]["event"]["text"] = "changed"
    with pytest.raises(ChatInboxError, match="delivery_conflict"):
        inbox.accept(changed)
    inbox.accept(bound_page(1, 1, generation=2))
    assert inbox.completed([f"{1:032x}"]) == 1


def test_reordered_new_queue_cursor_does_not_ack_unprocessed_input():
    inbox = CompanionChatInbox()
    inbox.bind(scope())
    inbox.accept(bound_page(1, 1))
    inbox.bind(scope(2))
    current = bound_page(2, 1, generation=2)
    current["events"][0]["cursor"] = 1
    inbox.accept(current)
    current = bound_page(1, 1, generation=2)
    current["events"][0]["cursor"] = 2
    inbox.accept(current)
    assert inbox.completed([f"{1:032x}"]) is None
    assert inbox.completed([f"{2:032x}"]) == 2


@pytest.mark.parametrize("patch", [{"generation": 1}, {"membership_epoch": 0}, {"policy_revision": True}])
def test_scope_rollback_or_invalid_revision_discards_buffer(patch):
    inbox = CompanionChatInbox()
    inbox.bind(scope(2))
    inbox.accept(bound_page(1, 1, generation=2))
    with pytest.raises(ChatInboxError, match="scope_changed"):
        inbox.bind(scope(2, **patch))
    assert not inbox.batch()["events"]
