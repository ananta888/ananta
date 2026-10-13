"""Bounded execution buffer for already-authorized browser chat deliveries.

This buffer grants no chat authority and sends no ACK. Its owner must clear it
when the authorized conversation ends and revalidate deliveries after a fence.
"""

import json
from collections import OrderedDict
from copy import deepcopy

from worker.meet_media.companion_chat_ledger import MESSAGE_ID

MAX_EVENTS = 32
MAX_BYTES = 131_072
MAX_EVENT_BYTES = 8192
MAX_BATCH_EVENTS = 8
_CONTINUITY = ("origin", "tenant_id", "project_id", "task_id", "session_id", "runtime_id",
               "lease_id", "room_id", "own_peer_id")
_REVISIONS = ("generation", "membership_epoch", "policy_revision")
_SCOPE_FIELDS = frozenset((*_CONTINUITY, *_REVISIONS, "deadline_ms"))


class ChatInboxError(ValueError):
    pass


def _delivery_content(value):
    frozen = {key: val for key, val in value.items() if key not in {"generation", "membership_epoch"}}
    return json.dumps(frozen, sort_keys=True, ensure_ascii=False, allow_nan=False)


class CompanionChatInbox:
    def __init__(self):
        self._items = OrderedDict()
        self._bytes = 0
        self.cursor = 0
        self.acknowledgable_cursor = 0
        self._processed_cursor = 0
        self._scope = None

    def clear(self):
        self._items.clear()
        self._bytes = 0
        self.cursor = 0
        self.acknowledgable_cursor = 0
        self._processed_cursor = 0
        self._scope = None

    def pending_message_ids(self):
        """Owned bounded identities only; no content or authorization claims."""
        return list(self._items)

    def bind(self, scope):
        """Bind a trusted browser open receipt, never caller/chat authority.

        Reopening resets delivery cursors. Held content belongs to the exact
        continuing conversation; current browser reply checks remain mandatory.
        """
        valid = (isinstance(scope, dict) and set(scope) == _SCOPE_FIELDS
                 and all(isinstance(scope[key], str) and 0 < len(scope[key]) <= 2048 for key in _CONTINUITY)
                 and all(type(scope[key]) is int and 0 < scope[key] <= 2**53 - 1
                         for key in (*_REVISIONS, "deadline_ms")))
        if not valid or (self._scope is not None and (
                any(scope[key] != self._scope[key] for key in _CONTINUITY)
                or any(scope[key] < self._scope[key] for key in _REVISIONS))):
            self.clear()
            raise ChatInboxError("meet_chat_inbox_scope_changed")
        self._scope = deepcopy(scope)
        self.cursor = self.acknowledgable_cursor = self._processed_cursor = 0
        for item, _encoded in self._items.values():
            item["cursor"] = None

    def accept(self, batch):
        """Atomically retain one bounded page from the current trusted poll port."""
        if (not isinstance(batch, dict) or batch.get("schema") != "ananta.meet-chat-batch.draft1"
                or not isinstance(batch.get("events"), list) or len(batch["events"]) > MAX_BATCH_EVENTS):
            raise ChatInboxError("meet_chat_inbox_batch_invalid")
        additions = OrderedDict()
        cursor = self.cursor
        for item in batch["events"]:
            if not isinstance(item, dict) or set(item) != {"cursor", "event"}:
                raise ChatInboxError("meet_chat_inbox_item_invalid")
            position, event = item["cursor"], item["event"]
            if type(position) is not int or not 0 < position <= 2**53 - 1 or not isinstance(event, dict):
                raise ChatInboxError("meet_chat_inbox_item_invalid")
            identity = event.get("message_id")
            if not isinstance(identity, str) or not MESSAGE_ID.fullmatch(identity):
                raise ChatInboxError("meet_chat_inbox_identity_invalid")
            if self._scope is not None and any(
                    type(event.get(key)) is not type(self._scope[key]) or event.get(key) != self._scope[key]
                    for key in ("session_id", "room_id", "generation", "membership_epoch")):
                raise ChatInboxError("meet_chat_inbox_event_scope_invalid")
            try:
                encoded = json.dumps(event, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
            except (TypeError, ValueError, UnicodeError):
                raise ChatInboxError("meet_chat_inbox_event_invalid") from None
            if len(encoded) > MAX_EVENT_BYTES:
                raise ChatInboxError("meet_chat_inbox_event_size")
            previous = additions.get(identity) or self._items.get(identity)
            if previous:
                rebound = previous[0]["cursor"] is None and self._scope is not None
                original = previous[0]["event"]
                if rebound:
                    same = _delivery_content(original) == _delivery_content(event)
                else:
                    same = previous[1] == encoded and previous[0]["cursor"] == position
                if not same:
                    raise ChatInboxError("meet_chat_inbox_delivery_conflict")
                if rebound:
                    if position <= cursor:
                        raise ChatInboxError("meet_chat_inbox_cursor_invalid")
                    additions[identity] = (deepcopy(item), encoded)
            else:
                if position <= cursor:
                    raise ChatInboxError("meet_chat_inbox_cursor_invalid")
                additions[identity] = (deepcopy(item), encoded)
            cursor = max(cursor, position)
        added_bytes = sum(len(value[1]) - (len(self._items[key][1]) if key in self._items else 0)
                          for key, value in additions.items())
        added_events = sum(key not in self._items for key in additions)
        if len(self._items) + added_events > MAX_EVENTS or self._bytes + added_bytes > MAX_BYTES:
            raise ChatInboxError("meet_chat_inbox_exhausted")
        self._items.update(additions)
        self._bytes += added_bytes
        self.cursor = cursor

    def batch(self):
        """Read pending work without deleting it or advancing the browser ACK."""
        return {"schema": "ananta.meet-chat-batch.draft1",
                "events": [deepcopy(value[0]) for value in list(self._items.values())[:MAX_BATCH_EVENTS]]}

    def completed(self, identities):
        """Discard only locally processed inputs; the owner controls browser ACK."""
        if (not isinstance(identities, (list, tuple)) or len(identities) > MAX_BATCH_EVENTS
                or any(not isinstance(identity, str) or not MESSAGE_ID.fullmatch(identity) for identity in identities)):
            raise ChatInboxError("meet_chat_inbox_completion_invalid")
        if list(self._items)[:len(identities)] != list(identities):
            raise ChatInboxError("meet_chat_inbox_completion_order_invalid")
        completed_cursor = None
        for identity in identities:
            value = self._items.pop(identity)
            self._bytes -= len(value[1])
            position = value[0]["cursor"]
            if position is not None:
                self._processed_cursor = max(self._processed_cursor, position)
        pending = [value[0]["cursor"] for value in self._items.values() if value[0]["cursor"] is not None]
        candidate = min(self._processed_cursor, min(pending) - 1) if pending else self._processed_cursor
        if candidate > self.acknowledgable_cursor:
            self.acknowledgable_cursor = candidate
            completed_cursor = candidate
        return completed_cursor
