"""Fixed operator-configured Hub callback; no Worker-selected policy endpoint."""

import hmac
import os
import secrets
import time
import urllib.request
from urllib.parse import urlsplit

from ananta_contracts.meet_avatar_image import validate_avatar_projection
from ananta_contracts.meet_dialog import (
    parse,
    request_signature,
    response_signature,
    validate_callback,
    validate_controls,
)
from ananta_contracts.meet_dialog_voice import validate_voice_projection
from worker.meet_media.contract import encode, load_key
from worker.meet_media.dialog_avatar_image_client import HubAvatarImageClient
from worker.meet_media.dialog_speech_client import HubSpeechClient
from worker.meet_media.persona_http import read_bounded


class HubDialogClient:
    def __init__(self, assignment):
        self.url = os.environ.get("MEET_HUB_DIALOG_URL", "")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path != "/api/meet/v1/internal/dialog"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("meet_dialog_hub_endpoint_required")
        self.key = load_key(os.environ["MEET_WORKER_KEY_FILE"])
        self.ids = {k: assignment[k] for k in ("task_id", "lease_id", "runtime_id")}
        self.avatar_images = assignment.get("avatar_images") is True
        self.voice_profiles = assignment.get("voice_profiles") is True
        self.deadline = time.monotonic() + min(7200, assignment["deadline"] - time.time())

    def spoken(self, event, binding):
        return HubSpeechClient(self.url, self.key, self.ids, self.deadline).reply(event, binding)

    def report_terminal(self, observation):
        from worker.meet_media.dialog_diagnostics_client import report_terminal

        return report_terminal(self.url, self.key, self.ids, observation, self.deadline)

    def avatar_image(self, binding, reference):
        if not self.avatar_images:
            raise ValueError("meet_avatar_images_not_negotiated")
        return HubAvatarImageClient(self.url, self.key, self.ids, self.deadline).fetch(binding, reference)

    def call(self, action, **fields):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                raise ValueError("meet_dialog_redirect_denied")

        budget = min(25 if action in {"chat", "transcript"} else 6, self.deadline - time.monotonic())
        # Terminal cleanup cannot create authority and remains possible after expiry.
        if action == "finish":
            budget = 3
        if budget <= 0:
            raise ValueError("meet_dialog_expired")
        payload = {
            "schema": "ananta.meet-dialog-callback.v1",
            "action": action,
            **self.ids,
            "nonce": secrets.token_hex(16),
            "sent_at": int(time.time()),
            **fields,
        }
        validate_callback(payload, time.time())
        body = encode(payload)
        request = urllib.request.Request(
            self.url,
            body,
            {"Content-Type": "application/json", "X-Ananta-Dialog-Signature": request_signature(self.key, body)},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        deadline = time.monotonic() + budget
        try:
            with opener.open(request, timeout=budget) as response:
                raw = read_bounded(response, maximum=16384, deadline=deadline)
                signed = response.headers.get("X-Ananta-Dialog-Signature", "")
            if not hmac.compare_digest(response_signature(self.key, body, raw), signed):
                raise ValueError()
            value = parse(raw)
            schema = {
                "exchange": "ananta.meet-dialog-state.v1",
                "chat": "ananta.meet-dialog-answer.v1",
                "finish": "ananta.meet-dialog-finished.v1",
                "audio": "ananta.meet-audio-assignment.v1",
                "transcript": "ananta.meet-audio-result.v1",
            }[action]
            fields = {
                "exchange": {"authorization", "renewal", "audio_job", "controls"},
                "chat": {"code", "reply"},
                "finish": set(),
                "audio": {"job"},
                "transcript": {"reply"},
            }[action]
            if action == "exchange" and self.avatar_images:
                fields = fields | {"avatar"}
            if action == "exchange" and self.voice_profiles:
                fields = fields | {"voice"}
            if (
                not isinstance(value, dict)
                or set(value) != {"schema", "nonce"} | fields
                or value["schema"] != schema
                or value["nonce"] != payload["nonce"]
            ):
                raise ValueError()
            if action == "exchange":
                validate_controls(value["controls"])
                if self.avatar_images:
                    validate_avatar_projection(value["avatar"])
                if self.voice_profiles:
                    voice = validate_voice_projection(value["voice"])
                    if voice["speech_revision"] != value["controls"].get("speech", {}).get("revision"):
                        raise ValueError("meet_dialog_voice_revision_changed")
            return value
        except Exception:
            raise ValueError("meet_dialog_hub_revoked_or_unavailable") from None
