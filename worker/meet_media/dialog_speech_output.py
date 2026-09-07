"""One independently stoppable spoken reply; no inference, queue or delegation."""

import time

from ananta_contracts.meet_speech_source import FRAME_SAMPLES
from ananta_contracts.meet_spoken_reply import SpokenReply, validate_spoken_binding
from worker.meet_media.audio_output import SpeechFrame
from worker.meet_media.speech_browser import BrowserSpeechPort
from worker.meet_media.speech_publication import SpeechPublication


def speech_binding(assignment, receipt, controls, sender):
    speech = controls.get("speech")
    chat = controls["chat"]
    if (
        not speech
        or not speech["enabled"]
        or not chat["enabled"]
        or not {"chat.read", "chat.send", "speech.publish"} <= set(assignment["capabilities"])
    ):
        raise ValueError("meet_dialog_speech_disabled")
    grants = [grant for grant in receipt["grants"] if grant["chatRead"]]
    if not any(grant["publisherPeerId"] == sender for grant in grants):
        raise ValueError("meet_dialog_speech_input_revoked")
    lease = receipt["lease"]
    return validate_spoken_binding(
        {
            **{
                name: assignment[name]
                for name in ("tenant_id", "project_id", "task_id", "lease_id", "runtime_id", "session_id")
            },
            "meet_session_id": lease["sessionId"],
            "generation": lease["generation"],
            "room_id": receipt["roomId"],
            "own_peer_id": receipt["peerId"],
            "sender_peer_id": sender,
            "membership_epoch": receipt["membershipEpoch"],
            "receive_revision": receipt["receiveRevision"],
            "chat_revision": chat["revision"],
            "speech_revision": speech["revision"],
            "deadline_ms": min(
                assignment["deadline"] * 1000, lease["expiresAt"], *(grant["expiresAt"] for grant in grants)
            ),
        }
    )


class DialogSpeechOutput:
    def __init__(self, page, assignment, *, clock=time.time, monotonic=time.monotonic, browser=None):
        self.page, self.assignment, self.clock, self.monotonic = page, assignment, clock, monotonic
        self.browser = (
            browser
            if browser is not None
            else BrowserSpeechPort(page, self.require_current, clock=monotonic, lease=lambda: self.receipt["lease"])
        )
        self.url = assignment["meeting"]["origin"] + "/machine"
        self.receipt = self.controls = self.binding = self.publication = None
        self.pcm = b""
        self.version = 0
        self.fresh_until = 0

    @property
    def busy(self):
        return self.publication is not None

    def update(self, receipt, controls):
        self.receipt, self.controls = receipt, controls
        self.version += 1
        # Existing control exchange cadence is two seconds. A stalled controller
        # may not keep pushing using an indefinitely cached Hub receipt.
        self.fresh_until = self.monotonic() + 2.5
        if self.busy:
            try:
                self.require_current()
            except Exception:
                self.close()

    def prepare(self, event):
        if self.busy or self.controls is None or not self.controls.get("speech", {}).get("enabled"):
            return None
        if self.monotonic() >= self.fresh_until:
            raise ValueError("meet_dialog_speech_state_stale")
        if event["sent_at_ms"] < max(self.controls["chat"]["since"], self.controls["speech"]["since"]):
            raise ValueError("meet_dialog_speech_input_stale")
        return speech_binding(self.assignment, self.receipt, self.controls, event["sender_peer_id"])

    def require_current(self):
        if (
            self.binding is None
            or self.monotonic() >= self.fresh_until
            or self.clock() * 1000 >= self.binding["deadline_ms"]
            or self.page.url != self.url
            or speech_binding(self.assignment, self.receipt, self.controls, self.binding["sender_peer_id"])
            != self.binding
        ):
            raise ValueError("meet_dialog_speech_authority_changed")
        # This pure checkpoint owns Hub policy. BrowserSpeechPort checks actual
        # local membership, exact lease and chat on both sides of each source
        # operation, without separate cached browser-authority RPCs.

    def accept(self, result, binding):
        if self.busy or not isinstance(result, SpokenReply):
            return False
        self.binding = binding
        try:
            self.require_current()
            self.publication = SpeechPublication(
                self.browser,
                self.assignment["session_id"],
                len(result.pcm) // 2,
                self.require_current,
                clock=self.clock,
                monotonic=self.monotonic,
            )
            self.pcm = result.pcm
            return True
        except Exception:
            self.close()
            return False

    def tick(self):
        if not self.busy:
            return
        try:
            available = self.publication.writable_samples()
            if self.publication.completed:
                self.close()
                return
            # At most the fixed ten-frame browser queue per tick. Keep only the
            # already bounded authenticated PCM result and the next frame.
            frames = []
            start = self.publication.sent
            for _ in range(10):
                frame = SpeechFrame(start, self.pcm[start * 2 : (start + FRAME_SAMPLES) * 2])
                if not frame.samples or available < frame.samples:
                    break
                frames.append(frame)
                available -= frame.samples
                start += frame.samples
            if frames:
                self.publication.push_frames(tuple(frames))
        except Exception:
            self.close()  # Never reopen/retry a consumed reply after any failure.

    def close(self):
        publication, self.publication = self.publication, None
        self.pcm, self.binding = b"", None
        if publication is not None:
            publication.close()

    def invalidate(self):
        self.fresh_until = 0
        self.close()
