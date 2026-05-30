"""T03.02 + T03.03 / SS04.01: Nicht-blockierendes Chat-Transport-Layer.

- Polling: läuft im Thread, nicht im Render-Hotpath
- Default-Intervall: 1000ms, Backoff bei Fehler bis 10s
- Deduplication per message.id
- Ausgehende Queue mit Retry + delivery_state Tracking
- Notes gehen NIEMALS in Transport-Queue
- ShareSession-Routing: share_session_id + optionale Payload-Verschlüsselung
"""
from __future__ import annotations

import time
import uuid
import urllib.error
import urllib.request
import json
import threading
from typing import Any, Callable


_POLL_INTERVAL_DEFAULT = 1.0
_POLL_INTERVAL_MAX = 10.0
_POLL_BACKOFF_FACTOR = 2.0


class ChatTransport:
    def __init__(self, hub_url: str, snake_id: str, token: str) -> None:
        self._hub_url = hub_url.rstrip("/")
        self._snake_id = snake_id
        self._token = token
        self._poll_interval = _POLL_INTERVAL_DEFAULT
        self._last_poll: float = 0.0
        self._last_cursor: str = ""
        self._seen_ids: set[str] = set()
        self._outbox: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._on_messages: Callable[[list[dict[str, Any]]], None] | None = None

    def set_message_handler(self, handler: Callable[[list[dict[str, Any]]], None]) -> None:
        self._on_messages = handler

    def enqueue(self, msg: dict[str, Any]) -> bool:
        """Add message to outbox. Notes (local_only) are rejected."""
        if str(msg.get("visibility") or "") == "local_only":
            return False
        if str(msg.get("channel_type") or "") == "notes":
            return False
        item = dict(msg)
        if "delivery_state" not in item:
            item["delivery_state"] = "queued"
        with self._lock:
            self._outbox.append(item)
        return True

    def enqueue_share_session_message(
        self,
        msg: dict[str, Any],
        *,
        share_session_id: str,
        encrypted_payload: dict[str, Any] | None = None,
    ) -> bool:
        """Nachrichten über eine ShareSession senden. Notes werden abgelehnt."""
        if str(msg.get("visibility") or "") == "local_only":
            return False
        if str(msg.get("channel_type") or "") == "notes":
            return False
        item = dict(msg)
        item["share_session_id"] = str(share_session_id)
        item["delivery_state"] = "queued"
        if encrypted_payload is not None:
            item["encrypted_payload"] = encrypted_payload
            item["_is_encrypted"] = True
        with self._lock:
            self._outbox.append(item)
        return True

    def tick(self, now: float) -> None:
        """Call from the snake tick – runs non-blocking in background thread."""
        if now - self._last_poll < self._poll_interval:
            return
        self._last_poll = now
        t = threading.Thread(target=self._do_poll_and_send, daemon=True)
        t.start()

    def _do_poll_and_send(self) -> None:
        self._send_outbox()
        self._poll_inbox()

    def _poll_inbox(self) -> None:
        url = f"{self._hub_url}/snakes/{self._snake_id}/chat/messages"
        if self._last_cursor:
            url += f"?since={self._last_cursor}"
        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
        except Exception:
            self._poll_interval = min(_POLL_INTERVAL_MAX, self._poll_interval * _POLL_BACKOFF_FACTOR)
            return

        self._poll_interval = _POLL_INTERVAL_DEFAULT
        messages: list[dict[str, Any]] = data.get("messages") or []
        new_cursor = str(data.get("cursor") or "")
        if new_cursor:
            self._last_cursor = new_cursor

        new_msgs = []
        for m in messages:
            mid = str(m.get("id") or "")
            if mid and mid not in self._seen_ids:
                self._seen_ids.add(mid)
                new_msgs.append(m)
        # keep seen_ids bounded
        if len(self._seen_ids) > 2000:
            self._seen_ids = set(list(self._seen_ids)[-1000:])

        if new_msgs and self._on_messages:
            try:
                self._on_messages(new_msgs)
            except Exception:
                pass

    def _send_outbox(self) -> None:
        with self._lock:
            pending = [m for m in self._outbox if m.get("delivery_state") in {"queued", "failed"}]

        for msg in pending:
            success = self._send_one(msg)
            with self._lock:
                for m in self._outbox:
                    if m.get("id") == msg.get("id"):
                        m["delivery_state"] = "sent" if success else "failed"
                        break

        # prune sent messages from outbox (keep last 50)
        with self._lock:
            self._outbox = [m for m in self._outbox if m.get("delivery_state") != "sent"]
            self._outbox = self._outbox[-50:]

    def _send_one(self, msg: dict[str, Any]) -> bool:
        # ShareSession-Routing hat Priorität
        share_session_id = str(msg.get("share_session_id") or "").strip()
        if share_session_id:
            return self._send_share_session_message(msg, share_session_id=share_session_id)

        ch_type = str(msg.get("channel_type") or "")
        if ch_type == "room":
            url = f"{self._hub_url}/snakes/{self._snake_id}/chat/messages"
        elif ch_type == "direct":
            target_ids = msg.get("target_ids") or []
            if not target_ids:
                return False
            url = f"{self._hub_url}/snakes/{target_ids[0]}/chat/messages"
        else:
            return False

        body = json.dumps({
            "id": str(msg.get("id") or str(uuid.uuid4())),
            "from_id": str(msg.get("sender_id") or self._snake_id),
            "channel_type": ch_type,
            "text": str(msg.get("text") or ""),
            "visibility": str(msg.get("visibility") or "room"),
        }).encode()

        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                return resp.status in {200, 201, 202}
        except Exception:
            return False

    def _send_share_session_message(self, msg: dict[str, Any], *, share_session_id: str) -> bool:
        url = f"{self._hub_url}/share-sessions/{share_session_id}/chat/messages"
        payload: dict[str, Any] = {
            "id": str(msg.get("id") or str(uuid.uuid4())),
            "from_id": str(msg.get("sender_id") or self._snake_id),
            "channel_type": str(msg.get("channel_type") or "room"),
            "visibility": str(msg.get("visibility") or "room"),
        }
        if msg.get("_is_encrypted") and msg.get("encrypted_payload"):
            payload["encrypted_payload"] = msg["encrypted_payload"]
        else:
            payload["text"] = str(msg.get("text") or "")
        body = json.dumps(payload).encode()
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read())
                if data.get("blocked"):
                    return False  # Policy-Block
                return resp.status in {200, 201, 202}
        except Exception:
            return False

    def retry_failed(self) -> int:
        with self._lock:
            count = 0
            for m in self._outbox:
                if m.get("delivery_state") == "failed":
                    m["delivery_state"] = "queued"
                    count += 1
        return count

    def outbox_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._outbox)
