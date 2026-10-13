"""Source-fenced speech/clip publication; no task routing or policy issuance."""

from dataclasses import replace

from worker.meet_media.companion_media import SpeechAvatarPublisher
from worker.meet_media.companion_task_controls import CompanionSourceInterrupted


class AssignedCompanionSpeech:
    """Bind one utterance to current independent Hub speech/avatar checkpoints.

    Video retirement is sticky for this utterance. An avatar resume requires a
    fresh utterance checkpoint; it cannot promote old prepared clips.
    """

    def __init__(self, controls, media, ports, *, cooperate, sequence=None):
        self.controls, self.media, self.ports, self.cooperate = controls, media, ports, cooperate
        self.speech = controls.checkpoint("speech")
        self.avatar = None
        if controls.state["controls"].get("avatar", {}).get("enabled"):
            self.avatar = controls.checkpoint("avatar")
        self.retired = self.avatar is None
        self.sequence = sequence

    def _guard(self):
        self.cooperate()
        self.controls.require_current(self.speech)
        if not self.retired:
            try:
                self.controls.require_current(self.avatar)
            except CompanionSourceInterrupted:
                self.retired = True

    def _avatar_active(self):
        self._guard()
        return not self.retired

    def _open_avatar(self, payload):
        return self.ports.avatar_open(payload) if self._avatar_active() else None

    def _close_avatar(self, generation):
        self._guard()
        self.ports.avatar_close(generation)

    def _open_speech(self, total):
        self._guard()
        return self.ports.speech_open(total)

    def _push_speech(self, generation, offset, chunk):
        self._guard()
        return self.ports.speech_push(generation, offset, chunk)

    def _sleep(self, seconds):
        deadline = self.ports.clock() + seconds
        while True:
            self._guard()
            remaining = deadline - self.ports.clock()
            if remaining <= 0:
                return
            self.ports.sleep(min(remaining, .05))

    def publish(self, pcm, *, current_generation=None):
        ports = replace(self.ports, avatar_open=self._open_avatar, avatar_close=self._close_avatar,
                        speech_open=self._open_speech, speech_push=self._push_speech,
                        sleep=self._sleep, avatar_active=self._avatar_active)
        if self.sequence is not None:
            from worker.meet_media.companion_sequence_speech import publish_sequence

            return publish_sequence(self, pcm, ports, self.sequence)
        publisher = SpeechAvatarPublisher(ports, clip_preparer=lambda value: self.media.speaking(
            value, cooperate=self._guard))
        return publisher.speak(pcm, current_generation=current_generation)
