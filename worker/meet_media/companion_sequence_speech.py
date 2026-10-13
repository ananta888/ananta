"""Native sample-clock video with separately fenced, bounded PCM publication."""

import time
from dataclasses import replace

import numpy as np

from worker.meet_media.companion_media import SPEECH_RATE, SpeechAvatarPublisher
from worker.meet_media.snake_avatar_timeline import SpeechMediaTimeline


def publish_sequence(publication, pcm, ports, sequence):
    timeline = SpeechMediaTimeline(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768, SPEECH_RATE)
    identity = None
    try:
        if publication._avatar_active():
            clips = publication.media.speaking(pcm, cooperate=publication._guard)
            segments = timeline.segments()
            if len(clips) != len(segments):
                raise ValueError("meet_companion_media_segments_invalid")
            if publication._avatar_active():
                try:
                    identity = sequence.prepare({"schema": "ananta.meet-avatar-sequence.v1",
                        "totalSamples": len(pcm) // 2, "clips": [
                            {"startSample": segment.start_sample, "endSample": segment.end_sample, "content": clip}
                            for segment, clip in zip(segments, clips)]}, cooperate=publication._guard)
                except Exception:
                    if not publication.retired:
                        raise
                    sequence.cancel()  # Only explicit avatar retirement permits audio-only continuity.
        def open_speech(total):
            generation = ports.speech_open(total)
            if identity is not None and publication._avatar_active():
                sequence.activate(identity, generation)
            return generation
        # PCM remains queue-bounded and source-fenced. Video never uses this
        # host scheduling clock; native played-sample observations own it.
        audio_ports = replace(ports, speech_open=open_speech, avatar_active=lambda: False)
        _generation, audio = SpeechAvatarPublisher(audio_ports).speak(pcm)
        if identity is None or publication.retired:
            return None, audio
        deadline = time.monotonic() + 2
        while True:
            publication._guard()
            if publication.retired:
                return None, audio
            report = sequence.quality(identity)
            fields = {"schema", "id", "segments", "max_drift_us", "sync_complete"}
            if (not isinstance(report, dict) or set(report) != fields
                    or report["schema"] != "ananta.meet-avatar-sequence-quality.v1"
                    or type(report["id"]) is not int or report["id"] != identity
                    or type(report["sync_complete"]) is not bool or type(report["max_drift_us"]) is not int
                    or not 0 <= report["max_drift_us"] <= 41667 or not isinstance(report["segments"], list)
                    or len(report["segments"]) > 5):
                raise ValueError("meet_companion_native_sequence_quality_invalid")
            if report["sync_complete"]:
                if len(report["segments"]) != len(timeline.segments()):
                    raise ValueError("meet_companion_native_sequence_segments_invalid")
                for index, (row, segment) in enumerate(zip(report["segments"], timeline.segments())):
                    if (not isinstance(row, dict) or set(row) != {
                        "segment", "expected_start_sample", "observed_played_sample", "drift_us"
                    } or any(type(value) is not int for value in row.values())
                            or row["segment"] != index or row["expected_start_sample"] != segment.start_sample
                            or not segment.start_sample <= row["observed_played_sample"] < segment.end_sample
                            or row["drift_us"] != (row["observed_played_sample"] - segment.start_sample)
                            * 1_000_000 // SPEECH_RATE):
                        raise ValueError("meet_companion_native_sequence_segments_invalid")
                if report["max_drift_us"] != max(row["drift_us"] for row in report["segments"]):
                    raise ValueError("meet_companion_native_sequence_quality_invalid")
                return None, {"profile": "ananta.meet-avatar-sync.v1", "segments": report["segments"],
                              "max_drift_us": report["max_drift_us"], "sync_complete": True}
            if time.monotonic() >= deadline:
                raise ValueError("meet_companion_native_sequence_completion_missing")
            ports.sleep(.02)
    finally:
        sequence.cancel()
