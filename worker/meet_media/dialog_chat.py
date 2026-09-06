"""One bounded chat input/reply pump, independent of media capture and Hub policy."""

from concurrent.futures import ThreadPoolExecutor


def chat_scope_matches(scope, receipt, assignment):
    if not isinstance(scope, dict):
        return False
    expected = {"origin": assignment["meeting"]["origin"], **{k: assignment[k] for k in
        ("tenant_id", "project_id", "task_id", "runtime_id", "session_id")},
        "lease_id": receipt["lease"]["sessionId"], "generation": receipt["lease"]["generation"], "room_id": receipt["roomId"],
        "own_peer_id": receipt["peerId"], "membership_epoch": receipt["membershipEpoch"], "policy_revision": receipt["receiveRevision"]}
    return (set(scope) == set(expected) | {"deadline_ms"}
            and type(scope["deadline_ms"]) is int and 0 < scope["deadline_ms"] < 2**53
            and all(type(scope[k]) is type(v) and scope[k] == v for k, v in expected.items()))


class DialogChatPump:
    def __init__(self, page, hub, assignment):
        self.page, self.hub, self.assignment = page, hub, assignment
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meet-hub-chat")
        self.opened = None; self.pending = None; self.receipt = None; self.revision = 0

    def update(self, receipt, control):
        allowed = control["enabled"] and any(g["chatRead"] for g in receipt["grants"])
        if self.opened and (not allowed or control["revision"] != self.revision or not chat_scope_matches(self.opened, receipt, self.assignment)):
            self.invalidate()
        self.receipt, self.revision = receipt, control["revision"]
        if allowed and self.opened is None and {"chat.read", "chat.send"} <= set(self.assignment["capabilities"]):
            # HTTP and WebSocket receipts can arrive in either order. A not-yet
            # confirmed local grant remains closed until the next fresh Hub check.
            scope = self.page.evaluate("() => { try { return window.anantaMachine.chat.open(); } catch { return null; } }")
            if scope is not None:
                if not chat_scope_matches(scope, receipt, self.assignment):
                    self.invalidate(); raise ValueError("meet_dialog_chat_scope_changed")
                self.opened = scope

    def tick(self):
        if self.pending is not None and self.pending[0].done():
            future, scope, message_id, revision = self.pending; self.pending = None
            try:
                result = future.result()
            except ValueError:
                result = {"reply": None}
            if result["reply"] is not None and self.opened == scope and self.revision == revision:
                reply = result["reply"]
                if (not isinstance(reply, dict) or set(reply) != {"message_id", "text"} or reply["message_id"] != message_id
                        or not isinstance(reply["text"], str) or not 0 < len(reply["text"]) <= 450):
                    raise ValueError("meet_dialog_reply_invalid")
                # A late/expired input is discarded, not retried or made into a
                # new uncorrelated reply. The browser rechecks source authority.
                self.page.evaluate("([id, text]) => { try { window.anantaMachine.chat.reply(id, text); } catch {} }", [message_id, reply["text"]])
        if self.opened is None or self.pending is not None:
            return
        if not self.page.evaluate("window.anantaMachine.chat.status().open"):
            self.opened = None; return
        batch = self.page.evaluate("window.anantaMachine.chat.poll()")
        if not isinstance(batch, dict) or not isinstance(batch.get("events"), list) or len(batch["events"]) > 8:
            raise ValueError("meet_dialog_chat_batch_invalid")
        if batch["events"]:
            item = batch["events"][0]; event = item["event"]
            if not any(g["chatRead"] and g["publisherPeerId"] == event["sender_peer_id"] for g in self.receipt["grants"]):
                raise ValueError("meet_dialog_chat_source_denied")
            self.page.evaluate("cursor => window.anantaMachine.chat.ack(cursor)", item["cursor"])
            self.pending = (self.pool.submit(self.hub.call, "chat", meet_session_id=self.receipt["lease"]["sessionId"], event=event),
                            self.opened, event["message_id"], self.revision)

    def invalidate(self):
        self.opened = None
        self.page.evaluate("window.anantaMachine.chat.close()")

    def close(self):
        try:
            self.invalidate()
        finally:
            self.pool.shutdown(wait=False, cancel_futures=True)
