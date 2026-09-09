"""One independently stoppable spoken reply; no inference, queue or delegation."""

import time

from ananta_contracts.meet_dialog_voice import validate_voice_projection, voice_binding_fields
from ananta_contracts.meet_speech_source import FRAME_SAMPLES
from ananta_contracts.meet_spoken_reply import SpokenReply, validate_spoken_binding
from worker.meet_media.audio_output import SpeechFrame
from worker.meet_media.browser_speech_playback import BrowserSpeechPlayback
from worker.meet_media.speaker_permit import SpeakerPermitGate
from worker.meet_media.speech_browser import BrowserSpeechPort
from worker.meet_media.speech_opening import BrowserSpeechOpening
from worker.meet_media.speech_publication import SpeechPublication, validate_publication_input

_DEFAULT_OPENING = object()
_DEFAULT_PLAYBACK = object()


def speech_binding(assignment, receipt, controls, sender, voice=None):
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
    voice_fields = {}
    if assignment.get("voice_profiles") is True:
        voice = validate_voice_projection(voice)
        if voice["speech_revision"] != speech["revision"]:
            raise ValueError("meet_dialog_voice_revision_changed")
        voice_fields = voice_binding_fields(voice)
    return validate_spoken_binding(
        {
            **voice_fields,
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
    def __init__(
        self,
        page,
        assignment,
        *,
        clock=time.time,
        monotonic=time.monotonic,
        browser=None,
        opening_factory=_DEFAULT_OPENING,
        playback_factory=_DEFAULT_PLAYBACK,
        finished=None,
    ):
        self.page, self.assignment, self.clock, self.monotonic = page, assignment, clock, monotonic
        self.speaker_gate = SpeakerPermitGate(assignment.get("speaker_floor", False), clock=clock, finished=finished)
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
        self.voice = None
        self.voice_epoch = 0
        self.prepared_voice_epoch = None
        self.opening = None
        # Existing injected synchronous ports remain substitutable. Production
        # dialog browsers opt into the narrow nonblocking setup capability.
        self.opening_factory = (
            (BrowserSpeechOpening if browser is None else None)
            if opening_factory is _DEFAULT_OPENING
            else opening_factory
        )
        self.playback_factory = (
            (BrowserSpeechPlayback if browser is None and self.opening_factory is not None else None)
            if playback_factory is _DEFAULT_PLAYBACK
            else playback_factory
        )
        if self.playback_factory is not None and self.opening_factory is None:
            raise ValueError("meet_speech_playback_requires_deferred_opening")

    @property
    def busy(self):
        return self.publication is not None or self.opening is not None

    def update(self, receipt, controls, voice=None, *, speaker_floor=None):
        try:
            self.speaker_gate.update(speaker_floor)
        except ValueError:
            self.invalidate()
            raise
        if self.assignment.get("voice_profiles") is True:
            try:
                voice = validate_voice_projection(voice)
                if voice["speech_revision"] != controls.get("speech", {}).get("revision"):
                    raise ValueError("meet_dialog_voice_revision_changed")
            except ValueError:
                self.invalidate()
                raise
            if voice != self.voice:
                self.voice_epoch += 1
            self.voice = voice
        elif voice is not None:
            self.invalidate()
            raise ValueError("meet_dialog_voice_not_negotiated")
        self.receipt, self.controls = receipt, controls
        self.version += 1
        # Normal control exchange cadence is one second. A stalled controller
        # may not keep pushing using an indefinitely cached Hub receipt.
        self.fresh_until = self.monotonic() + 2.5
        if self.busy:
            try:
                self.require_current()
                if self.publication is not None and self.playback_factory is not None:
                    self.publication.refresh()
            except Exception:
                self.close()

    def prepare(self, event):
        if self.busy or self.controls is None or not self.controls.get("speech", {}).get("enabled"):
            return None
        if self.monotonic() >= self.fresh_until:
            raise ValueError("meet_dialog_speech_state_stale")
        if event["sent_at_ms"] < max(self.controls["chat"]["since"], self.controls["speech"]["since"]):
            raise ValueError("meet_dialog_speech_input_stale")
        binding = speech_binding(self.assignment, self.receipt, self.controls, event["sender_peer_id"], self.voice)
        self.prepared_voice_epoch = self.voice_epoch
        return binding

    def require_current(self):
        if (
            self.binding is None
            or self.monotonic() >= self.fresh_until
            or self.clock() * 1000 >= self.binding["deadline_ms"]
            or self.page.url != self.url
            or self.assignment.get("voice_profiles") is True
            and self.prepared_voice_epoch != self.voice_epoch
            or speech_binding(self.assignment, self.receipt, self.controls, self.binding["sender_peer_id"], self.voice)
            != self.binding
        ):
            raise ValueError("meet_dialog_speech_authority_changed")
        self.speaker_gate.deadline(self.binding["deadline_ms"])
        # This pure checkpoint owns Hub policy. BrowserSpeechPort checks actual
        # local membership, exact lease and chat on both sides of each source
        # operation, without separate cached browser-authority RPCs.

    def _playback_authority(self):
        self.require_current()
        now = self.clock() * 1000
        deadline = self.speaker_gate.deadline(self.binding["deadline_ms"])
        return {
            "url": self.url,
            "lease": dict(self.receipt["lease"]),
            "deadline": deadline,
            "hubUntil": min(deadline, int(now + (self.fresh_until - self.monotonic()) * 1000)),
        }

    def accept(self, result, binding):
        if self.busy or not isinstance(result, SpokenReply):
            return False
        self.binding = binding
        try:
            self.speaker_gate.accept(result.speaker_floor, binding["deadline_ms"])
            self.require_current()
            if type(result.pcm) is not bytes or len(result.pcm) % 2:
                raise ValueError("meet_speech_publication_invalid")
            validate_publication_input(self.assignment["session_id"], len(result.pcm) // 2)
            if self.opening_factory is not None:
                self.pcm = result.pcm
                self.opening = self.opening_factory(
                    self.browser,
                    "speech:" + self.assignment["session_id"],
                    len(result.pcm) // 2,
                    self.require_current,
                    clock=self.monotonic,
                )
                return True  # Chat correlation is reserved before the first source poll/PCM write.
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
            if self.opening is not None:
                receipt = self.opening.poll()
                if receipt is None:
                    return
                if self.playback_factory is None:
                    self.publication = SpeechPublication(
                        self.browser,
                        self.assignment["session_id"],
                        len(self.pcm) // 2,
                        self.require_current,
                        clock=self.clock,
                        monotonic=self.monotonic,
                        opened_receipt=receipt,
                    )
                else:
                    self.publication = self.playback_factory(
                        self.page,
                        self.assignment["session_id"],
                        self.pcm,
                        self._playback_authority,
                        clock=self.clock,
                        monotonic=self.monotonic,
                        opened_receipt=receipt,
                    )
                    self.pcm = b""  # Ownership moved to the bounded browser-local asset store.
                self.opening.release()
                self.opening = None
            if self.playback_factory is not None:
                self.publication.tick()
                if self.publication.completed:
                    self.close()
                return
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
        opening, self.opening = self.opening, None
        self.pcm, self.binding = b"", None
        stopped = False
        try:
            try:
                if opening is not None:
                    opening.close()
            finally:
                if publication is not None:
                    publication.close()
            stopped = True
        finally:
            # A failing source close is not a successful completion report.
            # The Hub retains its hard deadline/quarantine if no report arrives.
            self.speaker_gate.close(report=stopped)

    def invalidate(self):
        self.voice_epoch += 1
        self.fresh_until = 0
        self.close()
