"""One bounded, request-bound fetch of the Hub-selected silent clip."""

import hmac
import re
import secrets
import time
import urllib.request
from urllib.parse import urlsplit

from ananta_contracts.meet_avatar_image import parse_image_message, validate_image_binding
from ananta_contracts.meet_avatar_video import (
    MAX_AVATAR_VIDEO_BYTES,
    REQUEST_SCHEMA,
    decode_video_response,
    validate_video_request,
    video_request_signature,
    video_response_signature,
)
from ananta_contracts.meet_persona_video import validate_reference
from worker.meet_media.contract import encode
from worker.meet_media.persona_http import NoRedirect, read_bounded


class HubAvatarVideoClient:
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
        self.url, self.key = dialog_url + "/avatar-video", key
        self.ids = {name: identifiers[name] for name in ("task_id", "lease_id", "runtime_id")}
        self.deadline = deadline

    def fetch(self, binding, reference, repeat_mode):
        try:
            binding, reference = dict(validate_image_binding(binding)), dict(validate_reference(reference))
            if (
                any(binding[name] != value for name, value in self.ids.items())
                or any(reference[name] != binding[name] for name in ("tenant_id", "project_id"))
                or repeat_mode not in ("loop", "hold_last")
            ):
                raise ValueError()
            remaining = min(6, self.deadline - time.monotonic(), binding["deadline_ms"] / 1000 - time.time())
            if remaining <= 0:
                raise ValueError()
            deadline = time.monotonic() + remaining
            payload = {
                "schema": REQUEST_SCHEMA,
                "nonce": secrets.token_hex(16),
                "sent_at": int(time.time()),
                "binding": binding,
            }
            validate_video_request(payload, time.time())
            raw = encode(payload)
            request = urllib.request.Request(
                self.url,
                raw,
                {
                    "Content-Type": "application/json",
                    "X-Ananta-Avatar-Video-Signature": video_request_signature(self.key, raw),
                },
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=remaining) as response:
                length = response.headers.get("Content-Length")
                if (
                    response.status != 200
                    or response.headers.get("Content-Encoding", "identity") != "identity"
                    or length is None
                    or not length.isdecimal()
                    or not 0 < int(length) <= MAX_AVATAR_VIDEO_BYTES
                ):
                    raise ValueError()
                body = read_bounded(response, maximum=MAX_AVATAR_VIDEO_BYTES, deadline=deadline, length=int(length))
                signed = response.headers.get("X-Ananta-Avatar-Video-Signature", "")
            if not re.fullmatch(r"[a-f0-9]{64}", signed) or not hmac.compare_digest(
                video_response_signature(self.key, raw, body), signed
            ):
                raise ValueError()
            video = decode_video_response(
                parse_image_message(body, response=True), payload, reference, repeat_mode, int(time.time() * 1000)
            )
            if time.monotonic() >= deadline:
                raise ValueError()
            return video
        except Exception:
            raise ValueError("meet_avatar_video_hub_revoked_or_unavailable") from None
