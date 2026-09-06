"""Pull-based mono PCM output: bounded blocks, exact sample clock, no publisher."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

SAMPLE_RATE = 22_050
FRAME_SAMPLES = 441  # Exactly 20 ms; no rounding drift between sentences.


class SpeechSourcePort(Protocol):
    def synthesize(self, text: str, *, max_samples: int, require_current: Callable[[], None]) -> Iterator[bytes]: ...


@dataclass(frozen=True)
class SpeechFrame:
    start_sample: int
    pcm_s16le: bytes = field(repr=False)

    @property
    def samples(self):
        return len(self.pcm_s16le) // 2

    @property
    def timestamp_us(self):
        return self.start_sample * 1_000_000 // SAMPLE_RATE


def speech_frames(text, source: SpeechSourcePort, *, max_seconds=40, require_current):
    """Yield at most one 20-ms frame per pull; the last frame is never padded.

    The provider may hold one bounded sentence, not an unbounded output queue.
    A consumer must recheck its own publication authority before sending; these
    frames carry neither a session identity nor a publication permission.
    """
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 450 or "\x00" in text:
        raise ValueError("meet_speech_text_invalid")
    if type(max_seconds) is not int or not 1 <= max_seconds <= 40:
        raise ValueError("meet_speech_budget_invalid")
    require_current()
    maximum = max_seconds * SAMPLE_RATE
    blocks = iter(source.synthesize(text, max_samples=maximum, require_current=require_current))
    pending = bytearray()
    received = emitted = 0
    try:
        while True:
            require_current()
            try:
                block = next(blocks)
            except StopIteration:
                break
            require_current()
            if type(block) is not bytes or not block or len(block) % 2:
                raise ValueError("meet_speech_pcm_invalid")
            received += len(block) // 2
            if received > maximum:
                raise ValueError("meet_audio_duration_exceeded")
            for offset in range(0, len(block), 2 * FRAME_SAMPLES):
                pending.extend(block[offset : offset + 2 * FRAME_SAMPLES])
                if len(pending) >= 2 * FRAME_SAMPLES:
                    require_current()
                    frame = SpeechFrame(emitted, bytes(pending[: 2 * FRAME_SAMPLES]))
                    del pending[: 2 * FRAME_SAMPLES]
                    emitted += frame.samples
                    yield frame
            block = None
        if not received:
            raise ValueError("meet_speech_empty")
        if pending:
            require_current()
            yield SpeechFrame(emitted, bytes(pending))
        require_current()
    finally:
        pending.clear()
        close = getattr(blocks, "close", None)
        if close is not None:
            close()
