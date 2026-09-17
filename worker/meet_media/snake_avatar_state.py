"""Avatar body-language state machine (idle / listening / thinking / speaking).

Pure, clock-free logic: the state machine only records *when* a state was
entered on the shared media timeline and blends body language across short
transitions. TTS start and end drive ``speaking`` deterministically; the
mouth itself is never driven from here but from the speech envelope
(see ``snake_avatar_timeline``), so state changes cannot desynchronize lips.
"""

from dataclasses import dataclass

IDLE = "idle"
LISTENING = "listening"
THINKING = "thinking"
SPEAKING = "speaking"
STATES = frozenset({IDLE, LISTENING, THINKING, SPEAKING})

# Short, soft transitions so a state change never looks like a jump cut.
TRANSITION_SECONDS = 0.25


@dataclass(frozen=True)
class BodyLanguage:
    """Continuous, per-state motion parameters (all bounded and gentle)."""

    breath_amplitude: float  # px of vertical body breathing
    breath_hz: float
    head_sway_amplitude: float  # px of horizontal head sway
    head_sway_hz: float
    head_tilt_degrees: float  # static tilt bias (thinking tilts the head)
    gaze_dx: float  # pupil offset towards the active speaker
    gaze_dy: float
    blink_interval_seconds: float
    speech_nod_gain: float  # how much the speech envelope nods the head

    def blend(self, other, weight):
        weight = min(1.0, max(0.0, float(weight)))

        def mix(a, b):
            return a + (b - a) * weight

        return BodyLanguage(
            breath_amplitude=mix(self.breath_amplitude, other.breath_amplitude),
            breath_hz=mix(self.breath_hz, other.breath_hz),
            head_sway_amplitude=mix(self.head_sway_amplitude, other.head_sway_amplitude),
            head_sway_hz=mix(self.head_sway_hz, other.head_sway_hz),
            head_tilt_degrees=mix(self.head_tilt_degrees, other.head_tilt_degrees),
            gaze_dx=mix(self.gaze_dx, other.gaze_dx),
            gaze_dy=mix(self.gaze_dy, other.gaze_dy),
            blink_interval_seconds=mix(self.blink_interval_seconds, other.blink_interval_seconds),
            speech_nod_gain=mix(self.speech_nod_gain, other.speech_nod_gain),
        )


BODY_LANGUAGE = {
    # Gentle breathing, occasional blink, straight gaze, closed mouth.
    IDLE: BodyLanguage(2.0, 0.22, 1.0, 0.10, 0.0, 0.0, 0.0, 4.0, 0.0),
    # Attentive: leans in (less sway), looks towards the speaker, blinks less.
    LISTENING: BodyLanguage(1.2, 0.30, 0.5, 0.08, 4.0, 3.0, -1.0, 5.5, 0.0),
    # Calm recognizable "thinking": head tilted, eyes up, slow sway, no lips.
    THINKING: BodyLanguage(1.5, 0.25, 3.0, 0.20, -9.0, -2.0, -3.0, 3.0, 0.0),
    # Speaking: lip-sync plus subtle envelope-driven nods and head sway.
    SPEAKING: BodyLanguage(1.5, 0.28, 2.0, 0.35, 2.0, 0.0, 0.0, 3.5, 1.0),
}


class AvatarStateMachine:
    """Tracks the current state on the media timeline and blends transitions."""

    def __init__(self, *, initial=IDLE, at=0.0):
        if initial not in STATES:
            raise ValueError("meet_avatar_state_invalid")
        self._state = initial
        self._previous = initial
        self._entered_at = float(at)

    @property
    def state(self):
        return self._state

    def transition(self, state, *, at):
        if state not in STATES:
            raise ValueError("meet_avatar_state_invalid")
        at = float(at)
        if at < self._entered_at:
            raise ValueError("meet_avatar_timeline_not_monotonic")
        if state == self._state:
            return
        self._previous = self._state
        self._state = state
        self._entered_at = at

    # TTS start/end are the only deterministic drivers of ``speaking``.
    def begin_speech(self, *, at):
        self.transition(SPEAKING, at=at)

    def end_speech(self, *, at, resume=IDLE):
        if resume == SPEAKING:
            raise ValueError("meet_avatar_state_invalid")
        self.transition(resume, at=at)

    def body_language(self, at):
        """Blended body language at timeline second ``at``."""
        at = float(at)
        if at < self._entered_at:
            raise ValueError("meet_avatar_timeline_not_monotonic")
        target = BODY_LANGUAGE[self._state]
        if self._previous == self._state or TRANSITION_SECONDS <= 0:
            return target
        weight = (at - self._entered_at) / TRANSITION_SECONDS
        if weight >= 1.0:
            return target
        return BODY_LANGUAGE[self._previous].blend(target, weight)

    def transition_progress(self, at):
        """0..1 progress of the current transition (1 when settled)."""
        if self._previous == self._state:
            return 1.0
        return min(1.0, max(0.0, (float(at) - self._entered_at) / TRANSITION_SECONDS))
