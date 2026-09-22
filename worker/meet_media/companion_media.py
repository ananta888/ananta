"""Publishes TTS audio and the rendered snake avatar from one timeline.

The reply PCM is synthesized once. The same buffer feeds the speech track
(pushed in 20 ms frames) and the avatar clips (rendered per ``ClipSegment`` of
the shared ``SpeechMediaTimeline``). Clip swaps are scheduled by the audio
offset, so audio and video are two projections of one clock and drift is
measured instead of assumed.

Browser access is behind small callables (ports) so the coordination logic is
testable without Playwright or FFmpeg. An optional ``lipsync`` port (see
``avatar_service.LipSyncClient``) replaces the locally rendered speaking clip
of a segment with a service-rendered one; any failure falls back per segment
to the local renderer, so the reply is never blocked by the service.
"""

import tempfile
import time
from dataclasses import dataclass

import numpy as np

from worker.meet_media.snake_avatar import build_video, video_payload
from worker.meet_media.snake_avatar_state import IDLE, LISTENING, SPEAKING, THINKING
from worker.meet_media.snake_avatar_timeline import MICROSECONDS, SpeechMediaTimeline

SPEECH_RATE = 22050
FRAME_SAMPLES = 441
# Keep ~120 ms of lead over real-time playback: enough to survive jitter,
# within the bounded queue (never "meet_speech_buffer_exceeded").
PUSH_LEAD_SECONDS = 0.12


@dataclass
class AvatarPorts:
    """Narrow browser-side capabilities the publisher depends on (ISP)."""

    avatar_open: object  # (payload) -> generation
    avatar_close: object  # (generation) -> None
    speech_open: object  # (total_samples) -> generation
    speech_push: object  # (generation, offset, chunk_bytes) -> None
    clock: object = time.monotonic
    sleep: object = time.sleep


def _clip(samples, seconds, *, state, repeat, start_frame=0, frames=None, encoder=None):
    with tempfile.TemporaryDirectory(prefix="snake-") as temporary:
        data, count = build_video(
            samples, SPEECH_RATE, seconds, temporary,
            state=state, start_frame=start_frame, frames=frames, encoder=encoder,
        )
    return video_payload(data, count, repeat=repeat)


class IdleClips:
    """Looping, silent clips for the non-speaking states (cached per state)."""

    def __init__(self, *, encoder=None, seconds=2.0):
        self._encoder = encoder
        self._seconds = float(seconds)
        self._cache = {}

    def payload(self, state=IDLE):
        if state not in (IDLE, LISTENING, THINKING):
            raise ValueError("meet_avatar_state_invalid")
        if state not in self._cache:
            silent = np.zeros(int(SPEECH_RATE * self._seconds), dtype=np.float32)
            self._cache[state] = _clip(silent, self._seconds, state=state, repeat="loop", encoder=self._encoder)
        return self._cache[state]


class SpeechAvatarPublisher:
    """Speaks ``pcm`` while swapping avatar clips on the same timeline."""

    def __init__(self, ports, *, encoder=None, log=lambda message: None, lipsync=None):
        self.ports = ports
        self._encoder = encoder
        self._log = log
        # (segment_pcm_bytes) -> persona-video-v1 payload or None (fallback).
        self._lipsync = lipsync
        self.generation = None

    def prepare(self, pcm):
        """Render every clip segment of the reply before any media starts.

        With a ``lipsync`` port each segment (<= 10 s, the service limit) is
        rendered from the segment's own PCM window, so the service clip covers
        exactly the audio the timeline schedules it for. ``None`` from the
        port selects the local renderer for that segment.
        """
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        timeline = SpeechMediaTimeline(samples, SPEECH_RATE)
        clips = []
        for segment in timeline.segments():
            clip = self._lipsync_clip(pcm, segment)
            if clip is None:
                clip = _clip(
                    samples, segment.frames / timeline.fps,
                    state=SPEAKING, repeat="hold_last",
                    start_frame=segment.first_frame, frames=segment.frames, encoder=self._encoder,
                )
            clips.append(clip)
        return timeline, clips

    def _lipsync_clip(self, pcm, segment):
        if self._lipsync is None:
            return None
        try:
            clip = self._lipsync(pcm[segment.start_sample * 2:segment.end_sample * 2])
        except Exception as error:  # noqa: BLE001
            # The port is expected to return None on failure; a raise is still
            # only a fallback, never a dropped reply.
            self._log("lipsync_err segment=%s %r" % (segment.index, error))
            return None
        if clip is None:
            return None
        if clip["frames"] != segment.frames:
            # hold_last keeps the tile stable when the service rounds the
            # frame count differently; the audio schedule is unaffected.
            self._log("lipsync frames segment=%s expected=%s got=%s" % (segment.index, segment.frames, clip["frames"]))
        return clip

    def speak(self, pcm, *, current_generation=None):
        """Publish audio and clips together; returns (avatar_generation, drift_report).

        PCM pushes run ``PUSH_LEAD_SECONDS`` ahead of playback; clip swaps are
        scheduled at the *playback* time of their first sample so the visible
        mouth matches the audible audio, not the transport buffer.
        """
        timeline, clips = self.prepare(pcm)
        segments = timeline.segments()
        total = len(pcm) // 2
        ports = self.ports
        generation = current_generation
        video_started, audio_pushed = [], []
        pending = list(zip(segments, clips))
        speech_generation = ports.speech_open(total)
        # Playback of sample 0 starts with the first push; pushes then run
        # ahead of playback by the lead, clip swaps run on the playback clock.
        anchor = ports.clock()
        offset = 0
        while offset < total or pending:
            now = ports.clock()
            if pending and now >= anchor + pending[0][0].start_sample / SPEECH_RATE:
                segment, clip = pending.pop(0)
                generation = self._swap_clip(generation, clip)
                video_started.append(int(ports.clock() * MICROSECONDS))
            if offset < total:
                if segments and len(audio_pushed) < len(segments) and offset >= segments[len(audio_pushed)].start_sample:
                    audio_pushed.append(int(now * MICROSECONDS))
                chunk = pcm[offset * 2:(offset + FRAME_SAMPLES) * 2]
                ports.speech_push(speech_generation, offset, chunk)
                offset += FRAME_SAMPLES
                target = anchor + (offset / SPEECH_RATE) - PUSH_LEAD_SECONDS
            else:
                target = anchor + pending[0][0].start_sample / SPEECH_RATE
            if pending:
                target = min(target, anchor + pending[0][0].start_sample / SPEECH_RATE)
            delay = target - ports.clock()
            if delay > 0:
                ports.sleep(delay)
        # Playback trails the last push by the lead; wait it out so callers
        # switch back to idle only after the audible speech has ended.
        remaining = anchor + total / SPEECH_RATE - ports.clock()
        if remaining > 0:
            ports.sleep(remaining)
        self.generation = generation
        report = timeline.measure_drift(
            anchor_us=int(anchor * MICROSECONDS),
            video_started_us=video_started,
            audio_pushed_us=audio_pushed,
            segments=segments,
            push_lead_us=int(PUSH_LEAD_SECONDS * MICROSECONDS),
        )
        return generation, report

    def _swap_clip(self, generation, clip):
        if generation is not None:
            try:
                self.ports.avatar_close(generation)
            except Exception as error:  # noqa: BLE001
                self._log("avatar_close_err %r" % (error,))
        return self.ports.avatar_open(clip)
