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
The production companion injects a cancellable ``clip_preparer`` instead;
HTTP/FFmpeg preparation stays outside the browser owner thread. Legacy callers
can still use the synchronous renderer for isolated preparation/tests.
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
# The client accepts at most 4410 queued samples (200 ms). Keep a much smaller
# steady lead, never push past the client queue, and swap avatar clips slightly
# early so the (blocking) browser call falls inside the buffered audio instead
# of stalling the audio loop.
PUSH_LEAD_SECONDS = 0.12
PRE_SWAP_SECONDS = 0.18
QUEUE_LIMIT_SAMPLES = 4410
QUEUE_MARGIN_SAMPLES = 2 * FRAME_SAMPLES
# If a swap unavoidably stalls us, re-anchor instead of bursting to catch up.
REANCHOR_AFTER_SECONDS = 0.35
PUBLICATION_STALL_BUDGET_SECONDS = 30


@dataclass
class AvatarPorts:
    """Narrow browser-side capabilities the publisher depends on (ISP)."""

    avatar_open: object  # (payload) -> generation
    avatar_close: object  # (generation) -> None
    speech_open: object  # (total_samples) -> generation
    speech_push: object  # (generation, offset, chunk_bytes) -> None
    clock: object = time.monotonic
    sleep: object = time.sleep
    speech_status: object = None  # () -> {bufferedSamples, ...}
    avatar_active: object = None  # optional operation-bound predicate; False retires video only


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

    def __init__(self, ports, *, encoder=None, log=lambda message: None, lipsync=None, clip_preparer=None):
        self.ports = ports
        self._encoder = encoder
        self._log = log
        # (segment_pcm_bytes) -> persona-video-v1 payload or None (fallback).
        self._lipsync = lipsync
        self._clip_preparer = clip_preparer
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
        if self._clip_preparer is not None:
            clips = self._clip_preparer(pcm)
            if not isinstance(clips, list) or len(clips) != len(timeline.segments()):
                raise ValueError("meet_companion_media_segments_invalid")
            return timeline, clips
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
        if callable(self.ports.avatar_active) and not self.ports.avatar_active():
            timeline = SpeechMediaTimeline(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0, SPEECH_RATE)
            clips = [None] * len(timeline.segments())
        else:
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
        playback_anchor = None
        deadline = anchor + total / SPEECH_RATE + PUBLICATION_STALL_BUDGET_SECONDS
        offset = 0
        swap_latency = 0.0
        while offset < total or pending:
            # Complete the blocking queue observation before opening a clip.
            # In particular, sample zero must follow initial video setup
            # without an unrelated browser status wait between the two.
            buffered = self._buffered_samples()
            now = ports.clock()
            if now >= deadline:
                raise ValueError("meet_avatar_publication_timeout")
            if buffered is not None and buffered + FRAME_SAMPLES > QUEUE_LIMIT_SAMPLES - QUEUE_MARGIN_SAMPLES:
                ports.sleep(FRAME_SAMPLES / SPEECH_RATE)
                continue
            # Swap the avatar *before* the segment boundary so the blocking
            # browser call is covered by already buffered audio.
            if pending and now >= anchor + pending[0][0].start_sample / SPEECH_RATE - PRE_SWAP_SECONDS - swap_latency:
                _segment, clip = pending.pop(0)
                if not callable(ports.avatar_active) or ports.avatar_active():
                    swap_started = ports.clock()
                    generation = self._swap_clip(generation, clip)
                    swap_finished = ports.clock()
                    # Predict only the bounded, already observed operation
                    # latency. Unexpected later stalls still fail drift checks.
                    swap_latency = min(PUSH_LEAD_SECONDS, max(0, swap_finished - swap_started))
                    video_started.append(int(swap_finished * MICROSECONDS) if generation is not None else None)
                else:
                    generation = None
                    video_started.append(None)
            # Never push past the client queue, and never burst after a stall.
            if offset < total:
                now = ports.clock()  # The preceding clip operation may have blocked.
                if offset == 0:
                    # The first PCM push starts playback. Initial video setup
                    # may block before it; that wait is not audible A/V drift.
                    anchor = ports.clock()
                    playback_anchor = anchor
                    now = anchor
                if (segments and len(audio_pushed) < len(segments)
                        and offset >= segments[len(audio_pushed)].start_sample):
                    audio_pushed.append(int(now * MICROSECONDS))
                chunk = pcm[offset * 2:(offset + FRAME_SAMPLES) * 2]
                ports.speech_push(speech_generation, offset, chunk)
                offset += FRAME_SAMPLES
                if (now - anchor) - offset / SPEECH_RATE > REANCHOR_AFTER_SECONDS:
                    # A stalled swap ran long: re-anchor instead of catching up.
                    anchor = now - offset / SPEECH_RATE
                target = anchor + (offset / SPEECH_RATE) - PUSH_LEAD_SECONDS
            else:
                target = anchor + pending[0][0].start_sample / SPEECH_RATE
            if pending:
                target = min(target, anchor + pending[0][0].start_sample / SPEECH_RATE
                             - PRE_SWAP_SECONDS - swap_latency)
            delay = target - ports.clock()
            if delay > 0:
                ports.sleep(delay)
        # Playback trails the last push by the lead; wait it out so callers
        # switch back to idle only after the audible speech has ended.
        remaining = anchor + total / SPEECH_RATE - ports.clock()
        if remaining > 0:
            ports.sleep(remaining)
        self.generation = generation
        published = [index for index, value in enumerate(video_started) if value is not None]
        report = timeline.measure_drift(
            # Scheduling may re-anchor after a later audio-only stall. The
            # original sample-zero start remains the measured playback origin;
            # changing it would rewrite already published A/V observations.
            anchor_us=int(playback_anchor * MICROSECONDS),
            video_started_us=[video_started[index] for index in published],
            audio_pushed_us=[audio_pushed[index] for index in published],
            segments=[segments[index] for index in published],
            push_lead_us=int(PUSH_LEAD_SECONDS * MICROSECONDS),
            video_lead_us=int(PRE_SWAP_SECONDS * MICROSECONDS),
        )
        if len(published) != len(segments):
            # No invented drift observation for a clip which was not published.
            report.update(sync_complete=False, retired_segments=len(segments) - len(published))
        if callable(ports.avatar_active) and not ports.avatar_active():
            report.update(sync_complete=False, avatar_retired=True)
        return generation, report

    def _buffered_samples(self):
        """Client-side queued samples, or None when the port is unavailable."""
        status = self.ports.speech_status
        if not callable(status):
            return None
        try:
            report = status()
        except Exception:  # noqa: BLE001
            return None
        value = report.get("bufferedSamples") if isinstance(report, dict) else None
        return value if isinstance(value, int) and value >= 0 else None

    def _swap_clip(self, generation, clip):
        if generation is not None:
            try:
                self.ports.avatar_close(generation)
            except Exception as error:  # noqa: BLE001
                self._log("avatar_close_err %r" % (error,))
        return self.ports.avatar_open(clip)
