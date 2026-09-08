"""Diagnostic wrappers forward exact values/errors, never repeat IO or keep text."""

from unittest.mock import Mock

import pytest

from tests.meet_dialog_chat_observer import DialogChatObserver
from worker.meet_media.dialog_chat_browser import DialogChatBrowser
from worker.meet_media.dialog_client import HubDialogClient
from worker.meet_media.dialog_speech_output import DialogSpeechOutput


def install(monkeypatch, *, error=None, reply=True):
    values = [dict(events=[{"private": "message"}]), True, {"private": "binding"}, object() if reply else None]
    mocks = []
    for (owner, name), value in zip(
        [
            (DialogChatBrowser, "poll"),
            (DialogChatBrowser, "ack"),
            (DialogSpeechOutput, "prepare"),
            (HubDialogClient, "spoken"),
        ],
        values,
        strict=True,
    ):
        mocked = Mock(return_value=value, side_effect=error if name == "prepare" else None)
        monkeypatch.setattr(owner, name, mocked)
        mocks.append(mocked)
    return DialogChatObserver(monkeypatch), mocks, values


def test_exact_forwarding_and_detached_redacted_snapshot(monkeypatch):
    observer, mocks, values = install(monkeypatch)
    owner, event, binding = object(), {"private": "message"}, {"private": "binding"}
    assert DialogChatBrowser.poll(owner) is values[0]
    assert DialogChatBrowser.ack(owner, "private-cursor") is values[1]
    assert DialogSpeechOutput.prepare(owner, event) is values[2]
    assert HubDialogClient.spoken(owner, event, binding) is values[3]
    mocks[0].assert_called_once_with(owner)
    mocks[1].assert_called_once_with(owner, "private-cursor")
    mocks[2].assert_called_once_with(owner, event)
    mocks[3].assert_called_once_with(owner, event, binding)
    snapshot = observer.report()
    assert snapshot == dict(polls=1, events=1, acked=1, prepared=1, spoken=1, replied=1, failures=[])
    DialogChatBrowser.poll(owner)
    assert snapshot["polls"] == 1 and "private" not in str(snapshot)


@pytest.mark.parametrize("message", ["private-internal-text", "meet_dialog_speech_input_stale"])
def test_prepare_denial_preserves_exception_and_bounded_allowlist(monkeypatch, message):
    error = ValueError(message)
    observer, mocks, _ = install(monkeypatch, error=error)
    for _ in range(12):
        with pytest.raises(ValueError) as caught:
            DialogSpeechOutput.prepare(object(), {})
        assert caught.value is error
    snapshot = observer.report()
    assert mocks[2].call_count == 12 and snapshot["prepared"] == 0
    assert snapshot["failures"] == [message if message.startswith("meet_") else "speech_prepare_rejected"] * 8
    assert "private" not in str(snapshot)


@pytest.mark.parametrize("fails", [True, False])
def test_spoken_rejection_or_empty_response_is_not_inference_success(monkeypatch, fails):
    observer, mocks, _ = install(monkeypatch, reply=False)
    error = ValueError("private-callback-body")
    mocks[3].side_effect = error if fails else None
    if fails:
        with pytest.raises(ValueError) as caught:
            HubDialogClient.spoken(object(), {}, {})
        assert caught.value is error
    else:
        assert HubDialogClient.spoken(object(), {}, {}) is None
    snapshot = observer.report()
    assert snapshot["spoken"] == 1 and snapshot["replied"] == 0
    assert snapshot["failures"] == ["spoken_callback_rejected" if fails else "spoken_callback_empty"]
    mocks[3].assert_called_once()
    assert "private" not in str(snapshot)
