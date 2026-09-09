"""Fixed operator-configured Hub callback; no Worker-selected policy endpoint."""

import hmac
import os
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from ananta_contracts.meet_avatar_image import validate_avatar_projection
from ananta_contracts.meet_avatar_video import validate_video_projection
from ananta_contracts.meet_browser_workspace import require_browser_assignment, validate_browser_source
from ananta_contracts.meet_dialog import (
    MAX_DIALOG_BYTES,
    MAX_VISUAL_CONTROL_BYTES,
    parse,
    parse_visual_control,
    request_signature,
    response_signature,
    validate_callback,
    validate_controls,
)
from ananta_contracts.meet_dialog_voice import validate_voice_projection
from ananta_contracts.meet_speaker_floor import validate_speaker_permit
from worker.meet_media.contract import encode, load_key
from worker.meet_media.dialog_avatar_image_client import HubAvatarImageClient
from worker.meet_media.dialog_avatar_video_client import HubAvatarVideoClient
from worker.meet_media.dialog_control_transport import ControlReadUnavailable, transient_control_read
from worker.meet_media.dialog_speech_client import HubSpeechClient
from worker.meet_media.persona_http import read_bounded
from worker.meet_media.reconnect_receipt import ReconnectReceiptGate


class HubDialogClient:
    def __init__(self, assignment):
        self.recovery_receipts = ReconnectReceiptGate(assignment)
        self.speaker_floor = assignment.get("speaker_floor", False)
        self.speech_finished = None
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
        self.avatar_videos = assignment.get("avatar_videos") is True
        self.voice_profiles = assignment.get("voice_profiles") is True
        self.browser_workspace = assignment.get("browser_workspace") is True
        self.visual_receive = "video.receive" in assignment["capabilities"]
        self.browser_assignment = {
            name: assignment[name]
            for name in (
                "task_id",
                "lease_id",
                "runtime_id",
                "tenant_id",
                "project_id",
                "session_id",
                "deadline",
                "capabilities",
            )
        } | {"browser_workspace": self.browser_workspace}
        self.deadline = time.monotonic() + min(7200, assignment["deadline"] - time.time())

    def spoken(self, event, binding):
        return HubSpeechClient(self.url, self.key, self.ids, self.deadline, floor_required=self.speaker_floor).reply(
            event, binding
        )

    def report_speech_finished(self, permit):
        if not self.speaker_floor:
            raise ValueError("meet_speaker_not_negotiated")
        # One immutable local reference, not another task queue. Repeating this
        # exact completion in control reads is safe and does not extend a permit.
        self.speech_finished = validate_speaker_permit(permit)

    def report_terminal(self, observation):
        from worker.meet_media.dialog_diagnostics_client import report_terminal

        return report_terminal(self.url, self.key, self.ids, observation, self.deadline)

    def avatar_image(self, binding, reference):
        if not self.avatar_images:
            raise ValueError("meet_avatar_images_not_negotiated")
        return HubAvatarImageClient(self.url, self.key, self.ids, self.deadline).fetch(binding, reference)

    def avatar_video(self, binding, reference, repeat_mode):
        if not self.avatar_videos:
            raise ValueError("meet_avatar_videos_not_negotiated")
        return HubAvatarVideoClient(self.url, self.key, self.ids, self.deadline).fetch(binding, reference, repeat_mode)

    def call(self, action, **fields):
        if action == "reconnect":
            self.recovery_receipts.require_enabled()
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                raise ValueError("meet_dialog_redirect_denied")

        budget = min(25 if action in {"chat", "transcript"} else 6, self.deadline - time.monotonic())
        # Terminal cleanup cannot create authority and remains possible after expiry.
        if action in {"finish", "browser_finish"}:
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
        if action == "exchange" and self.speaker_floor and self.speech_finished is not None:
            payload["speech_finished"] = self.speech_finished
        validate_callback(payload, time.time())
        body = encode(payload)
        request = urllib.request.Request(
            self.url,
            body,
            {"Content-Type": "application/json", "X-Ananta-Dialog-Signature": request_signature(self.key, body)},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        deadline = time.monotonic() + budget
        visual_control = action == "exchange" and self.visual_receive
        try:
            with opener.open(request, timeout=budget) as response:
                raw = read_bounded(
                    response,
                    maximum=MAX_VISUAL_CONTROL_BYTES if visual_control else MAX_DIALOG_BYTES,
                    deadline=deadline,
                )
                signed = response.headers.get("X-Ananta-Dialog-Signature", "")
            if not hmac.compare_digest(response_signature(self.key, body, raw), signed):
                raise ValueError()
            value = (parse_visual_control if visual_control else parse)(raw)
            schema = {
                "exchange": "ananta.meet-dialog-state.v1",
                "chat": "ananta.meet-dialog-answer.v1",
                "finish": "ananta.meet-dialog-finished.v1",
                "audio": "ananta.meet-audio-assignment.v1",
                "transcript": "ananta.meet-audio-result.v1",
                "visual": "ananta.meet-visual-assignment.v1",
                "visual_result": "ananta.meet-visual-accepted.v1",
                "browser_finish": "ananta.meet-browser-finished.v1",
                "reconnect": "ananta.meet-reconnect-state.v1",
            }[action]
            fields = {
                "exchange": {"authorization", "renewal", "audio_job", "controls"},
                "chat": {"code", "reply"},
                "finish": set(),
                "audio": {"job"},
                "transcript": {"reply"},
                "visual": {"job"},
                "visual_result": set(),
                "browser_finish": set(),
                "reconnect": {"attempt", "state", "deadline_ms", "ready_ms", "meeting"},
            }[action]
            if action == "exchange" and self.avatar_images:
                fields = fields | {"avatar"}
            if action == "exchange" and self.voice_profiles:
                fields = fields | {"voice"}
            if action == "exchange" and self.browser_workspace:
                fields = fields | {"browser"}
            if action == "exchange" and self.visual_receive:
                fields = fields | {"visual_job"}
            if action == "exchange" and self.speaker_floor:
                fields = fields | {"speaker_floor"}
            if (
                not isinstance(value, dict)
                or set(value) != {"schema", "nonce"} | fields
                or value["schema"] != schema
                or value["nonce"] != payload["nonce"]
            ):
                raise ValueError()
            if action == "reconnect":
                value = self.recovery_receipts.accept(value, payload)
            if action == "exchange":
                validate_controls(value["controls"])
                if self.speaker_floor and value["speaker_floor"] is not None:
                    validate_speaker_permit(value["speaker_floor"])
                if self.avatar_images:
                    (validate_video_projection if self.avatar_videos else validate_avatar_projection)(value["avatar"])
                if self.voice_profiles:
                    voice = validate_voice_projection(value["voice"])
                    if voice["speech_revision"] != value["controls"].get("speech", {}).get("revision"):
                        raise ValueError("meet_dialog_voice_revision_changed")
                if self.browser_workspace:
                    source = validate_browser_source(value["browser"])
                    if source["job"] is not None:
                        require_browser_assignment(source["job"], self.browser_assignment)
                        if source["binding"]["screen_revision"] != value["controls"]["screen"]["revision"]:
                            raise ValueError("meet_dialog_browser_revision_changed")
            return value
        except Exception as error:
            if action == "reconnect":
                self.recovery_receipts.close()
            if isinstance(error, urllib.error.HTTPError):
                try:
                    error.close()  # Do not retain an unread error body/socket across a retry.
                except OSError:
                    pass
            if transient_control_read(action, error):
                raise ControlReadUnavailable() from None
            raise ValueError("meet_dialog_hub_revoked_or_unavailable") from None
