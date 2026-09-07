"""Closed browser-source receipts; these contain no Hub publication authority."""

from dataclasses import dataclass

SAMPLE_RATE = 22050
FRAME_SAMPLES = 441
QUEUE_SAMPLES = 4410


@dataclass(frozen=True)
class SpeechSourceReceipt:
    source_id: str
    generation: int
    total_samples: int
    expires_at_ms: int


def source_receipt(value, *, source_id, total_samples, now_ms):
    fields = {
        "schema",
        "sourceId",
        "generation",
        "sampleRate",
        "channels",
        "format",
        "totalSamples",
        "queueSamples",
        "expiresAt",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("meet_speech_source_receipt_invalid")
    expected = {
        "schema": "ananta.meet-speech-source.v1",
        "sourceId": source_id,
        "sampleRate": SAMPLE_RATE,
        "channels": 1,
        "format": "pcm_s16le",
        "totalSamples": total_samples,
        "queueSamples": QUEUE_SAMPLES,
    }
    if (
        any(type(value[k]) is not type(v) or value[k] != v for k, v in expected.items())
        or type(value["generation"]) is not int
        or not 1 <= value["generation"] <= 4096
        or type(value["expiresAt"]) is not int
        or not now_ms < value["expiresAt"] <= now_ms + 50000
    ):
        raise ValueError("meet_speech_source_receipt_invalid")
    return SpeechSourceReceipt(source_id, value["generation"], total_samples, value["expiresAt"])


def source_progress(value, receipt, *, sent, played):
    fields = {"state", "generation", "receivedSamples", "playedSamples", "bufferedSamples"}
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value["state"] not in ("open", "completed")
        or any(type(value[k]) is not int for k in fields - {"state"})
    ):
        raise ValueError("meet_speech_source_progress_invalid")
    completed = value["state"] == "completed"
    if (
        value["generation"] != receipt.generation + int(completed)
        or value["receivedSamples"] != sent
        or not played <= value["playedSamples"] <= sent <= receipt.total_samples
        or value["bufferedSamples"] != (0 if completed else sent - value["playedSamples"])
        or value["bufferedSamples"] > QUEUE_SAMPLES
        or completed
        and value["playedSamples"] != receipt.total_samples
    ):
        raise ValueError("meet_speech_source_progress_invalid")
    return value["playedSamples"], completed
