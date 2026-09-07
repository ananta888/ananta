"""Bounded speech result transport; never schedules generation or grants authority."""

import hmac
import secrets
import time
import urllib.request
from urllib.parse import urlsplit

from ananta_contracts.meet_dialog import MAX_DIALOG_BYTES
from ananta_contracts.meet_spoken_reply import (
    MAX_SPOKEN_BYTES,
    REQUEST_SCHEMA,
    decode_spoken_response,
    parse_spoken,
    spoken_request_signature,
    spoken_response_signature,
    validate_spoken_binding,
    validate_spoken_request,
)
from worker.meet_media.contract import encode
from worker.meet_media.persona_http import NoRedirect, read_bounded


class HubSpeechClient:
    def __init__(self, dialog_url, key, identifiers, deadline):
        parsed = urlsplit(dialog_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path != "/api/meet/v1/internal/dialog"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("meet_dialog_hub_endpoint_required")
        self.url, self.key = dialog_url + "/speech", key
        self.ids = {name: identifiers[name] for name in ("task_id", "lease_id", "runtime_id")}
        self.deadline = deadline

    def reply(self, event, binding):
        """Return authenticated PCM for exactly the caller's current projection."""
        try:
            binding = dict(validate_spoken_binding(binding))
            remaining = min(25, self.deadline - time.monotonic(), binding["deadline_ms"] / 1000 - time.time())
            if remaining <= 0:
                raise ValueError()
            deadline = time.monotonic() + remaining
            payload = {
                "schema": REQUEST_SCHEMA,
                **self.ids,
                "nonce": secrets.token_hex(16),
                "sent_at": int(time.time()),
                "meet_session_id": binding["meet_session_id"],
                "event": event,
            }
            validate_spoken_request(payload, time.time())
            raw = encode(payload)
            if len(raw) > MAX_DIALOG_BYTES:
                raise ValueError()
            request = urllib.request.Request(
                self.url,
                raw,
                {
                    "Content-Type": "application/json",
                    "X-Ananta-Speech-Signature": spoken_request_signature(self.key, raw),
                },
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=remaining) as response:
                if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ValueError()
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdecimal() or not 0 < int(length) <= MAX_SPOKEN_BYTES):
                    raise ValueError()
                body = read_bounded(
                    response,
                    maximum=MAX_SPOKEN_BYTES,
                    deadline=deadline,
                    length=int(length) if length is not None else None,
                )
                signature = response.headers.get("X-Ananta-Speech-Signature", "")
            # Authenticate exact bytes before JSON parsing, WAV decoding or playback.
            if not hmac.compare_digest(spoken_response_signature(self.key, raw, body), signature):
                raise ValueError()
            result = decode_spoken_response(
                parse_spoken(body, response=True), payload, binding, int(time.time() * 1000)
            )
            if time.monotonic() >= deadline:
                raise ValueError()
            return result
        except Exception:
            # No response body, text, PCM, endpoint, grant or upstream error in logs.
            raise ValueError("meet_spoken_hub_revoked_or_unavailable") from None
