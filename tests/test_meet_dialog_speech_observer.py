"""Source-acceptance diagnostics stay bounded, passive and content-free."""

from types import SimpleNamespace
from unittest.mock import Mock

from tests.meet_dialog_speech_observer import DialogSpeechObserver
from worker.meet_media.dialog_speech_output import DialogSpeechOutput


def test_source_acceptance_observer_records_metadata_without_extra_calls(monkeypatch):
    accept = Mock(return_value=True)
    monkeypatch.setattr(DialogSpeechOutput, "accept", accept)
    observer = DialogSpeechObserver(True, monkeypatch)
    output = SimpleNamespace(monotonic=lambda: 100.0, fresh_until=101.0)
    for _ in range(12):
        assert DialogSpeechOutput.accept(output, {"private": "audio-content"}, {"private": "binding"}) is True
    assert accept.call_count == 12 and len(observer.acceptances) == 8
    for row in observer.acceptances:
        assert set(row) == {"accepted", "fresh_before", "fresh_after", "elapsed_ms"}
        assert row["accepted"] and row["fresh_before"] and row["fresh_after"] and row["elapsed_ms"] >= 0
    assert "private" not in str(observer.acceptances)
