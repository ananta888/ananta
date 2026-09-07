"""One bounded chat input/reply pump, independent of media capture and Hub policy."""

from concurrent.futures import ThreadPoolExecutor

from worker.meet_media.dialog_chat_browser import DialogChatBrowser


def chat_scope_matches(scope, receipt, assignment):
    if not isinstance(scope, dict):
        return False
    expected = {
        "origin": assignment["meeting"]["origin"],
        **{k: assignment[k] for k in ("tenant_id", "project_id", "task_id", "runtime_id", "session_id")},
        "lease_id": receipt["lease"]["sessionId"],
        "generation": receipt["lease"]["generation"],
        "room_id": receipt["roomId"],
        "own_peer_id": receipt["peerId"],
        "membership_epoch": receipt["membershipEpoch"],
        "policy_revision": receipt["receiveRevision"],
    }
    return (
        set(scope) == set(expected) | {"deadline_ms"}
        and type(scope["deadline_ms"]) is int
        and 0 < scope["deadline_ms"] < 2**53
        and all(type(scope[k]) is type(v) and scope[k] == v for k, v in expected.items())
    )


def _only_policy_revision_changed(scope, receipt, assignment):
    return (
        isinstance(scope, dict)
        and type(scope.get("policy_revision")) is int
        and 0 < scope["policy_revision"] < 2**53
        and chat_scope_matches(scope | {"policy_revision": receipt["receiveRevision"]}, receipt, assignment)
    )


class DialogChatPump:
    def __init__(self, page, hub, assignment, *, speech=None):
        self.page, self.hub, self.assignment = page, hub, assignment
        self.browser_chat = DialogChatBrowser(page)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meet-hub-chat")
        self.opened = None
        self.pending = None
        self.receipt = None
        self.revision = 0
        self.speech = speech
        self.pending_speech = None
        self.pending_refresh = None

    @property
    def needs_refresh(self):
        return self.pending_refresh is not None and self.speech.version <= self.pending_refresh

    def update(self, receipt, control):
        allowed = control["enabled"] and any(g["chatRead"] for g in receipt["grants"])
        if self.opened and (
            not allowed
            or control["revision"] != self.revision
            or not chat_scope_matches(self.opened, receipt, self.assignment)
        ):
            self.invalidate()
        self.receipt, self.revision = receipt, control["revision"]
        if allowed and self.opened is None and {"chat.read", "chat.send"} <= set(self.assignment["capabilities"]):
            # HTTP and WebSocket receipts can arrive in either order. A not-yet
            # confirmed local grant remains closed until the next fresh Hub check.
            scope = self.page.evaluate(
                "() => { try { return window.anantaMachine.chat.open(); } catch { return null; } }"
            )
            if scope is not None:
                if not chat_scope_matches(scope, receipt, self.assignment):
                    self.invalidate()
                    if _only_policy_revision_changed(scope, receipt, self.assignment):
                        return  # Browser/HTTP ordering: accept neither until the next fresh Hub check.
                    raise ValueError("meet_dialog_chat_scope_changed")
                self.opened = scope

    def tick(self):
        if self.pending is not None and self.pending[0].done():
            self._complete()
        if self.opened is None or self.pending is not None or self.speech is not None and self.speech.busy:
            return
        if not self.page.evaluate("window.anantaMachine.chat.status().open"):
            self.opened = None
            return
        batch = self.browser_chat.poll()
        if batch is None:
            self.invalidate()
            return  # Only a fresh Hub exchange may reopen after browser revocation.
        if not isinstance(batch, dict) or not isinstance(batch.get("events"), list) or len(batch["events"]) > 8:
            raise ValueError("meet_dialog_chat_batch_invalid")
        if batch["events"]:
            item = batch["events"][0]
            event = item["event"]
            if not any(
                g["chatRead"] and g["publisherPeerId"] == event["sender_peer_id"] for g in self.receipt["grants"]
            ):
                raise ValueError("meet_dialog_chat_source_denied")
            if not self.browser_chat.ack(item["cursor"]):
                self.invalidate()
                return  # No input dispatch after the source changed during ACK.
            try:
                self.pending_speech = self.speech.prepare(event) if self.speech is not None else None
            except ValueError:
                return  # Consumed stale input is not converted to a text retry.
            future = (
                self.pool.submit(self.hub.spoken, event, self.pending_speech)
                if self.pending_speech is not None
                else self.pool.submit(
                    self.hub.call, "chat", meet_session_id=self.receipt["lease"]["sessionId"], event=event
                )
            )
            self.pending = (
                future,
                self.opened,
                event["message_id"],
                self.revision,
            )

    def _complete(self):
        if self.pending_speech is not None:
            if self.pending_refresh is None:
                self.pending_refresh = self.speech.version
                return  # The runtime must exchange fresh Hub state after inference.
            if self.needs_refresh:
                return
        future, scope, message_id, revision = self.pending
        binding, self.pending_speech = self.pending_speech, None
        self.pending = self.pending_refresh = None
        try:
            result = future.result()
        except ValueError:
            return
        if self.opened is not scope or self.revision != revision:
            return
        if binding is not None:
            if result is None or result.message_id != message_id or not self.speech.accept(result, binding):
                return
            reply = {"message_id": message_id, "text": result.text}
        else:
            reply = result["reply"]
        if reply is None:
            return
        if (
            not isinstance(reply, dict)
            or set(reply) != {"message_id", "text"}
            or reply["message_id"] != message_id
            or not isinstance(reply["text"], str)
            or not 0 < len(reply["text"]) <= 450
        ):
            raise ValueError("meet_dialog_reply_invalid")
        # Reserve browser correlation before any PCM is pushed. Uncertain text
        # delivery is never retried; failed correlation also closes speech.
        sent = self.page.evaluate(
            """([id, text]) => {
              try { window.anantaMachine.chat.reply(id, text); return true; } catch { return false; }
            }""",
            [message_id, reply["text"]],
        )
        if binding is not None and sent is not True:
            self.speech.close()

    def invalidate(self):
        self.opened = None
        if self.speech is not None:
            self.speech.invalidate()
        self.page.evaluate("window.anantaMachine.chat.close()")

    def close(self):
        try:
            self.invalidate()
        finally:
            self.pending = self.pending_speech = self.pending_refresh = None
            self.pool.shutdown(wait=False, cancel_futures=True)
