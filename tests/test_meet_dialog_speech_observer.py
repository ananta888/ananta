"""Source-acceptance diagnostics stay bounded, passive and content-free."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
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


def test_missing_generated_answer_has_a_bounded_snapshot_instead_of_index_error(monkeypatch):
    observer = DialogSpeechObserver(True, monkeypatch)
    observer.worker = Mock()
    command = Mock(return_value={"correlated": False})
    failures = ["meet_dialog_hub_revoked_or_unavailable"]
    with pytest.raises(AssertionError) as error:
        observer.receive_answer(command, failures=failures)
    snapshot = error.value.args[0]
    assert snapshot["generated_answers"] == 0 and snapshot["inference_failures"] == []
    assert snapshot["runtime_errors"] == failures
    failures.append("later_teardown_error")
    assert "later_teardown_error" not in str(snapshot)
    command.assert_called_once_with("answer_correlated")


@pytest.mark.parametrize("error", [MeetError("meet_worker_unavailable", 503), ValueError("private media details")])
def test_inference_failure_observation_is_bounded_redacted_and_never_retried(monkeypatch, error):
    observer = DialogSpeechObserver(True, monkeypatch)
    observer.worker = Mock()
    observer.worker.execute.side_effect = error
    for _ in range(12):
        with pytest.raises(type(error)) as caught:
            observer.execute({"private": "turn-content"})
        assert caught.value is error
    assert observer.worker.execute.call_count == 12 and observer.answers == []
    assert len(observer.inference_failures) == 8
    for row in observer.inference_failures:
        assert set(row) == {"code", "elapsed_seconds"} and row["elapsed_seconds"] >= 0
        assert row["code"] == ("meet_worker_unavailable" if isinstance(error, MeetError) else "worker_execution_failed")
    assert "private" not in str(observer.inference_failures)


def test_successful_generated_answer_still_requires_exact_received_digest(monkeypatch):
    observer = DialogSpeechObserver(True, monkeypatch)
    observer.worker = Mock()
    observer.answers.append({"text_sha256": "a" * 64})
    command = Mock(return_value={"correlated": True, "text_sha256": "b" * 64})
    with pytest.raises(AssertionError):
        observer.receive_answer(command)
    command.return_value = {"correlated": True, "text_sha256": "a" * 64}
    assert observer.receive_answer(command) == {"received": True}
