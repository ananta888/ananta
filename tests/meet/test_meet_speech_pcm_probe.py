"""Probe export is fixed, bounded and never classified as production evidence."""

import base64

import pytest

from worker.meet_media import speech_pcm_probe
from worker.meet_media.audio_output import SpeechFrame


def test_probe_exports_only_the_fixed_synthetic_phrase(monkeypatch):
    source = object()
    observed = []
    monkeypatch.setattr(speech_pcm_probe, "PiperSpeechSource", lambda: source)

    def frames(text, provider, *, max_seconds, require_current):
        observed.append((text, provider, max_seconds))
        require_current()
        yield SpeechFrame(0, b"\1\2" * 441)
        yield SpeechFrame(441, b"\3\4" * 17)

    monkeypatch.setattr(speech_pcm_probe, "speech_frames", frames)
    report = speech_pcm_probe.run()
    assert observed == [("Hallo, Ananta spricht lokal.", source, 10)]
    assert base64.b64decode(report.pop("pcm_base64"), validate=True) == b"\1\2" * 441 + b"\3\4" * 17
    assert report["samples"] == 458 and report["human_capture_used"] is False
    assert report["production_release_evidence"] is False
    assert report["classification"] == "synthetic_local_technical_observation"


@pytest.mark.parametrize("frames", [[], [SpeechFrame(0, b"\0\0" * 441)], [SpeechFrame(1, b"\0\0" * 500)]])
def test_empty_short_or_discontinuous_probe_fails_without_export(monkeypatch, frames):
    closed = []

    def provider(*args, **kwargs):
        try:
            yield from frames
        finally:
            closed.append(True)

    monkeypatch.setattr(speech_pcm_probe, "PiperSpeechSource", object)
    monkeypatch.setattr(speech_pcm_probe, "speech_frames", provider)
    with pytest.raises(ValueError):
        speech_pcm_probe.run()
    assert closed == [True]
