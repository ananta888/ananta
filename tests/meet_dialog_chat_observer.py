"""Passive, bounded chat-path diagnostics; never retain event contents or IDs."""

import threading
from collections import deque

from worker.meet_media.dialog_chat_browser import DialogChatBrowser
from worker.meet_media.dialog_client import HubDialogClient
from worker.meet_media.dialog_speech_output import DialogSpeechOutput


class DialogChatObserver:
    def __init__(self, monkeypatch):
        self.lock = threading.Lock()
        self.counts = dict(polls=0, events=0, acked=0, prepared=0, spoken=0, replied=0)
        self.failures = deque(maxlen=8)
        poll, ack = DialogChatBrowser.poll, DialogChatBrowser.ack
        prepare, spoken = DialogSpeechOutput.prepare, HubDialogClient.spoken

        def observed_poll(browser):
            result = poll(browser)
            with self.lock:
                self.counts["polls"] += 1
                if isinstance(result, dict) and isinstance(result.get("events"), list):
                    self.counts["events"] += len(result["events"])
            return result

        def observed_ack(browser, cursor):
            result = ack(browser, cursor)
            if result is True:
                with self.lock:
                    self.counts["acked"] += 1
            return result

        def observed_prepare(output, event):
            try:
                result = prepare(output, event)
            except ValueError as error:
                allowed = {
                    "meet_dialog_speech_state_stale",
                    "meet_dialog_speech_input_stale",
                    "meet_dialog_speech_disabled",
                    "meet_dialog_speech_input_revoked",
                    "meet_dialog_voice_revision_changed",
                }
                with self.lock:
                    self.failures.append(str(error) if str(error) in allowed else "speech_prepare_rejected")
                raise
            if result is not None:
                with self.lock:
                    self.counts["prepared"] += 1
            return result

        def observed_spoken(client, event, binding):
            with self.lock:
                self.counts["spoken"] += 1
            try:
                result = spoken(client, event, binding)
            except ValueError:
                with self.lock:
                    self.failures.append("spoken_callback_rejected")
                raise
            with self.lock:
                if result is not None:
                    self.counts["replied"] += 1
                else:
                    self.failures.append("spoken_callback_empty")
            return result

        monkeypatch.setattr(DialogChatBrowser, "poll", observed_poll)
        monkeypatch.setattr(DialogChatBrowser, "ack", observed_ack)
        monkeypatch.setattr(DialogSpeechOutput, "prepare", observed_prepare)
        monkeypatch.setattr(HubDialogClient, "spoken", observed_spoken)

    def report(self):
        with self.lock:
            return {**self.counts, "failures": list(self.failures)}
