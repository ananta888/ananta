"""Validate one bounded browser batch without owning a browser or ASR lifecycle."""

import base64
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AudioChunk:
    sequence: int
    start_sample: int
    pcm: bytes = field(repr=False)


class AudioBatchCursor:
    def __init__(self, profile):
        self.sequence = 0
        self.maximum = profile.segment_seconds * 10

    def validate(self, batch):
        if (
            not isinstance(batch, dict)
            or set(batch) != {"schema", "completed", "acknowledged", "chunks"}
            or batch["schema"] != "ananta.meet-audio-batch.draft1"
            or type(batch["completed"]) is not bool
            or type(batch["acknowledged"]) is not int
            or batch["acknowledged"] != self.sequence
            or not isinstance(batch["chunks"], list)
            or len(batch["chunks"]) > 5
            or self.sequence + len(batch["chunks"]) > self.maximum
        ):
            raise ValueError("meet_audio_batch_invalid")
        chunks = []
        for offset, row in enumerate(batch["chunks"]):
            expected = self.sequence + offset + 1
            if (
                not isinstance(row, dict)
                or set(row) != {"sequence", "startSample", "pcmBase64"}
                or type(row["sequence"]) is not int
                or row["sequence"] != expected
                or type(row["startSample"]) is not int
                or row["startSample"] != (expected - 1) * 1600
                or not isinstance(row["pcmBase64"], str)
                or len(row["pcmBase64"]) > 4268
            ):
                raise ValueError("meet_audio_chunk_invalid")
            pcm = base64.b64decode(row["pcmBase64"], validate=True)
            if len(pcm) != 3200:
                raise ValueError("meet_audio_chunk_invalid")
            chunks.append(AudioChunk(expected, row["startSample"], pcm))
        if batch["completed"] and not chunks and self.sequence != self.maximum:
            raise ValueError("meet_audio_segment_truncated")
        return chunks

    def acknowledge(self, sequence):
        if type(sequence) is not int or sequence != self.sequence + 1 or sequence > self.maximum:
            raise ValueError("meet_audio_cursor_invalid")
        self.sequence = sequence

    def complete(self, batch):
        return batch["completed"] and self.sequence == self.maximum
