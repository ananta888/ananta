"""Hydrate one Hub-selected image, without source activation or policy decisions."""

import hmac
import secrets
import time
import urllib.request
from urllib.parse import urlsplit

from ananta_contracts.meet_avatar_image import (
    MAX_AVATAR_IMAGE_BYTES,
    REQUEST_SCHEMA,
    decode_image_response,
    image_request_signature,
    image_response_signature,
    parse_image_message,
    validate_image_binding,
    validate_image_request,
)
from ananta_contracts.meet_dialog import MAX_DIALOG_BYTES
from ananta_contracts.meet_persona_image import validate_reference
from worker.meet_media.contract import encode
from worker.meet_media.persona_http import NoRedirect, read_bounded


class HubAvatarImageClient:
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
        self.url, self.key = dialog_url + "/avatar-image", key
        self.ids = {name: identifiers[name] for name in ("task_id", "lease_id", "runtime_id")}
        self.deadline = deadline

    def fetch(self, binding, reference):
        try:
            binding = dict(validate_image_binding(binding))
            reference = dict(validate_reference(reference))
            if any(binding[name] != value for name, value in self.ids.items()):
                raise ValueError()
            if any(reference[name] != binding[name] for name in ("tenant_id", "project_id")):
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
            validate_image_request(payload, time.time())
            raw = encode(payload)
            if len(raw) > MAX_DIALOG_BYTES:
                raise ValueError()
            request = urllib.request.Request(
                self.url,
                raw,
                {
                    "Content-Type": "application/json",
                    "X-Ananta-Avatar-Signature": image_request_signature(self.key, raw),
                },
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=remaining) as response:
                if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ValueError()
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdecimal() or not 0 < int(length) <= MAX_AVATAR_IMAGE_BYTES):
                    raise ValueError()
                body = read_bounded(
                    response,
                    maximum=MAX_AVATAR_IMAGE_BYTES,
                    deadline=deadline,
                    length=int(length) if length is not None else None,
                )
                signed = response.headers.get("X-Ananta-Avatar-Signature", "")
            if not hmac.compare_digest(image_response_signature(self.key, raw, body), signed):
                raise ValueError()
            image = decode_image_response(
                parse_image_message(body, response=True), payload, reference, int(time.time() * 1000)
            )
            if time.monotonic() >= deadline:
                raise ValueError()
            return image
        except Exception:
            raise ValueError("meet_avatar_image_hub_revoked_or_unavailable") from None
