"""Bounded replaceable utterance boundaries, no provider or browser ownership."""

import struct
from typing import Protocol


class SegmentBoundary(Protocol):
    def push(self, pcm: bytes) -> bool:
        """Consume one canonical 100-ms chunk; true seals the original window."""
        ...


class FixedSegment:
    def __init__(self, maximum_chunks):
        if type(maximum_chunks) is not int or not 10 <= maximum_chunks <= 100 or maximum_chunks % 10:
            raise ValueError("meet_audio_segment_budget_invalid")
        self.maximum = maximum_chunks
        self.chunks = 0
        self.closed = False

    def push(self, pcm):
        if self.closed or not isinstance(pcm, bytes) or len(pcm) != 3200:
            raise ValueError("meet_audio_segment_input_invalid")
        self.chunks += 1
        self.closed = self.chunks == self.maximum
        return self.closed


class EnergySegment:
    """Fixed mean-absolute PCM threshold, 300-ms onset and 500-ms trailing silence.

    This conservative local endpoint detector is not a speaker identity or
    transcription-confidence classifier. Continuous noise still hits the cap.
    """

    def __init__(self, maximum_chunks):
        self.window = FixedSegment(maximum_chunks)
        self.voiced_chunks = 0
        self.silent_chunks = 0
        self.speech_seen = False

    def push(self, pcm):
        maximum = self.window.push(pcm)
        voiced = sum(abs(sample[0]) for sample in struct.iter_unpack("<h", pcm)) >= 1600 * 600
        if voiced:
            self.voiced_chunks += 1
            self.silent_chunks = 0
            self.speech_seen |= self.voiced_chunks >= 3
        else:
            self.voiced_chunks = 0
            self.silent_chunks += 1
        finished = maximum or self.window.chunks >= 10 and self.speech_seen and self.silent_chunks >= 5
        self.window.closed = finished
        return finished


def segment_boundary(profile) -> SegmentBoundary:
    factory = {"fixed": FixedSegment, "energy-v1": EnergySegment}[profile.segmentation]
    return factory(profile.segment_seconds * 10)
