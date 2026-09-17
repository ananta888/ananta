"""One monotonic media timeline shared by TTS audio and the rendered avatar.

Every video frame is derived from a sample index of the *same* PCM buffer
that is published as the speech track, so the frame clock and the audio clock
are two projections of one timebase rather than two independent starts.

Responsibilities are split deliberately (SRP):

* ``SpeechMediaTimeline``  – sample/frame arithmetic, envelope, silence gate,
  clip segmentation and drift measurement.
* ``BlinkSchedule``        – deterministic blinks independent of the mouth.
* ``AnimationController``  – combines timeline, state machine and blinks into
  per-frame ``FramePose`` values the renderer consumes.
"""

import hashlib
import math
from dataclasses import dataclass

import numpy as np

from worker.meet_media.snake_avatar_state import SPEAKING, AvatarStateMachine

FPS = 12
MICROSECONDS = 1_000_000
# Frames the persona-video-v1 contract allows per clip (10 s at 12 fps).
MAX_CLIP_FRAMES = 120
# A frame is "late" when its start deviates from its audio window by more
# than half a frame; long answers must stay below this bound end to end.
MAX_FRAME_DRIFT_US = MICROSECONDS // FPS // 2
# RMS below this is treated as silence: the mouth closes in pauses.
SILENCE_GATE = 0.012
# Envelope smoothing per frame (fast attack, slower release, both < 1 frame
# of latency so the lips never lag the audible audio).
ATTACK = 0.85
RELEASE = 0.65
BLINK_FRAMES = 2


@dataclass(frozen=True)
class ClipSegment:
    """One bounded video clip covering a contiguous audio window."""

    index: int
    start_sample: int
    end_sample: int
    first_frame: int
    frames: int

    def start_us(self, rate):
        return (self.start_sample * MICROSECONDS + rate // 2) // rate

    def duration_us(self, rate):
        return ((self.end_sample - self.start_sample) * MICROSECONDS + rate // 2) // rate


@dataclass(frozen=True)
class FramePose:
    """Renderer input for one frame; every value is bounded and unit-less/px."""

    index: int
    time_us: int
    state: str
    mouth: float  # 0..1 continuous opening
    blink: float  # 0 open .. 1 closed
    head_dx: float
    head_dy: float
    head_tilt: float
    body_dy: float
    gaze_dx: float
    gaze_dy: float
    voiced: bool


class SpeechMediaTimeline:
    """Frame clock derived from the published PCM samples."""

    def __init__(self, samples, rate, *, fps=FPS):
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if type(rate) is not int or not 8_000 <= rate <= 48_000 or type(fps) is not int or not 1 <= fps <= 60:
            raise ValueError("meet_avatar_timeline_invalid")
        if not np.all(np.isfinite(samples)):
            raise ValueError("meet_avatar_timeline_samples_invalid")
        self.samples, self.rate, self.fps = samples, rate, fps
        self.total_samples = int(samples.size)
        self.frames = max(1, -(-self.total_samples * fps // rate))

    def frame_window(self, index):
        if type(index) is not int or index < 0:
            raise ValueError("meet_avatar_frame_invalid")
        start = index * self.rate // self.fps
        end = (index + 1) * self.rate // self.fps
        return start, min(end, self.total_samples)

    def frame_time_us(self, index):
        start, _ = self.frame_window(index)
        return self.audio_time_us(start)

    def audio_time_us(self, sample):
        return (int(sample) * MICROSECONDS + self.rate // 2) // self.rate

    def rms(self, index):
        start, end = self.frame_window(index)
        chunk = self.samples[start:end]
        if chunk.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))

    def envelope(self):
        """Gated, smoothed 0..1 mouth envelope per frame (closed in silence)."""
        values = []
        level = 0.0
        for index in range(self.frames):
            raw = self.rms(index)
            target = 0.0 if raw < SILENCE_GATE else min(1.0, raw * 6.0)
            gain = ATTACK if target > level else RELEASE
            level = level + (target - level) * gain
            if target == 0.0 and level < 0.08:
                level = 0.0
            values.append(level)
        return values

    def segments(self, *, max_frames=MAX_CLIP_FRAMES):
        """Split the timeline into clip-sized segments on frame boundaries."""
        if type(max_frames) is not int or not 2 <= max_frames <= MAX_CLIP_FRAMES:
            raise ValueError("meet_avatar_segment_invalid")
        result = []
        first = 0
        while first < self.frames:
            count = min(max_frames, self.frames - first)
            if count < 2:
                # The contract needs at least two frames; fold the tail into
                # the previous segment instead of publishing a 1-frame clip.
                if result:
                    last = result.pop()
                    count += last.frames
                    first = last.first_frame
                else:
                    count = 2
            start, _ = self.frame_window(first)
            end = min(self.total_samples, (first + count) * self.rate // self.fps)
            result.append(ClipSegment(len(result), start, end, first, count))
            first += count
        return result

    def measure_drift(self, *, anchor_us, video_started_us, audio_pushed_us, segments=None, push_lead_us=0):
        """Drift of each published clip against the audio window it renders.

        ``anchor_us`` is the monotonic time at which sample 0 starts playing.
        ``video_started_us[i]`` is when segment ``i``'s clip became visible and
        ``audio_pushed_us[i]`` when its first PCM frame was pushed (pushes may
        run ahead of playback by at most ``push_lead_us``). Returns a bounded
        report; raises when any clip drifts beyond the half-frame bound so a
        test fails instead of silently desynchronizing.
        """
        segments = self.segments() if segments is None else list(segments)
        if len(video_started_us) != len(segments) or len(audio_pushed_us) != len(segments):
            raise ValueError("meet_avatar_drift_observation_invalid")
        for value in (anchor_us, push_lead_us, *video_started_us, *audio_pushed_us):
            if type(value) is not int or value < 0:
                raise ValueError("meet_avatar_drift_observation_invalid")
        report = []
        worst = 0
        for segment, video_at, audio_at in zip(segments, video_started_us, audio_pushed_us):
            playback_at = anchor_us + segment.start_us(self.rate)
            drift = abs(video_at - playback_at)
            lead = playback_at - audio_at
            if not 0 <= lead <= push_lead_us + MICROSECONDS // self.fps:
                raise ValueError("meet_avatar_audio_schedule_invalid")
            worst = max(worst, drift)
            report.append({"segment": segment.index, "video_drift_us": drift, "audio_push_lead_us": lead})
        if worst > MAX_FRAME_DRIFT_US:
            raise ValueError("meet_avatar_drift_exceeded")
        return {"profile": "ananta.meet-avatar-sync.v1", "segments": report, "max_drift_us": worst}


class BlinkSchedule:
    """Deterministic blink onsets, independent of speech, seeded per clip."""

    def __init__(self, seed, *, fps=FPS):
        digest = hashlib.sha256(str(seed).encode()).digest()
        self._rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
        self.fps = fps
        self._next_at = 0.0
        self._onsets = []

    def blink_amount(self, time_seconds, interval_seconds):
        """0..1 eyelid closure at ``time_seconds`` for a given blink interval."""
        while self._next_at <= time_seconds:
            onset = self._next_at + max(0.5, interval_seconds * float(self._rng.uniform(0.6, 1.4)))
            self._onsets.append(onset)
            self._next_at = onset
        closed_for = BLINK_FRAMES / self.fps
        for onset in reversed(self._onsets):
            if onset <= time_seconds < onset + closed_for:
                return 1.0
            if onset < time_seconds:
                break
        return 0.0


class AnimationController:
    """Combines speech timing with avatar state into per-frame poses."""

    def __init__(self, timeline, state_machine=None, *, seed="ai-snake"):
        self.timeline = timeline
        self.state_machine = state_machine if state_machine is not None else AvatarStateMachine()
        self.blinks = BlinkSchedule(seed, fps=timeline.fps)

    def poses(self, *, start_frame=0, count=None):
        count = self.timeline.frames - start_frame if count is None else int(count)
        envelope = self.timeline.envelope()
        poses = []
        nod = 0.0
        for index in range(start_frame, start_frame + count):
            time_us = self.timeline.frame_time_us(index)
            seconds = time_us / MICROSECONDS
            language = self.state_machine.body_language(seconds)
            state = self.state_machine.state
            mouth = envelope[index] if state == SPEAKING and index < len(envelope) else 0.0
            # Head nods follow a low-passed envelope so gestures are calm and
            # never fight the per-frame lip motion.
            nod = nod + (mouth - nod) * 0.25
            phase = 2 * math.pi * seconds
            poses.append(
                FramePose(
                    index=index,
                    time_us=time_us,
                    state=state,
                    mouth=mouth,
                    blink=self.blinks.blink_amount(seconds, language.blink_interval_seconds),
                    head_dx=language.head_sway_amplitude * math.sin(phase * language.head_sway_hz),
                    head_dy=-3.0 * nod * language.speech_nod_gain,
                    head_tilt=language.head_tilt_degrees + 2.0 * nod * language.speech_nod_gain,
                    body_dy=language.breath_amplitude * math.sin(phase * language.breath_hz),
                    gaze_dx=language.gaze_dx,
                    gaze_dy=language.gaze_dy,
                    voiced=mouth > 0.0,
                )
            )
        return poses
