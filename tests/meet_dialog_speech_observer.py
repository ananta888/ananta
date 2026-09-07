"""Synthetic media and local-source observations for the real dialog fixture."""

import threading
import time

from ananta_contracts.meet_speech import speech_profile
from tests.test_meet_media import result
from tests.test_meet_speech_binding import speech_result
from worker.meet_media.dialog_speech_output import DialogSpeechOutput


class DialogSpeechObserver:
    def __init__(self, enabled, monkeypatch):
        self.enabled = enabled
        self.capabilities = ["chat.read", "chat.send", "screen.publish"] + (["speech.publish"] if enabled else [])
        self.profile = speech_profile(max_seconds=5) if enabled else None
        self.samples = []
        self.closed = []
        self.control = None
        self.condition = threading.Condition()
        tick = DialogSpeechOutput.tick

        def observe(output):
            publication = output.publication
            started = time.monotonic()
            tick(output)
            if publication is not None and publication.completed and output.publication is None:
                with self.condition:
                    self.samples.append(publication.played)
                    self.condition.notify_all()
            elif publication is not None and output.publication is None:
                self.closed.append(
                    {
                        "sent": publication.sent,
                        "played": publication.played,
                        "tick_ms": round((time.monotonic() - started) * 1000),
                        "browser": output.page.evaluate("window.anantaMachine.speech.status()"),
                    }
                )

        monkeypatch.setattr(DialogSpeechOutput, "tick", observe)
        update = DialogSpeechOutput.update

        def observe_update(output, receipt, controls):
            update(output, receipt, controls)
            with self.condition:
                self.control = controls.get("speech")
                self.condition.notify_all()

        monkeypatch.setattr(DialogSpeechOutput, "update", observe_update)

    def execute(self, turn):
        media = speech_result(profile=turn["speech_profile"], samples=22050) if self.enabled else result()
        return media | {
            "task_id": turn["task_id"],
            "lease_id": turn["lease_id"],
            "text": "Synthetic Hub answer",
            "usage": {"input_tokens": 20, "output_tokens": 8},
        }

    def require_completed(self, count, completed, failures):
        if not self.enabled:
            return
        deadline = time.monotonic() + 5
        with self.condition:
            while len(self.samples) < count and not completed.is_set() and time.monotonic() < deadline:
                self.condition.wait(min(0.1, max(0, deadline - time.monotonic())))
            assert self.samples == [22050] * count, {
                "spoken_samples": self.samples,
                "runtime_errors": failures,
                "closed_sources": self.closed,
            }
        # This is exact local source completion, never remote sample accounting.

    def require_paused(self):
        with self.condition:
            assert self.condition.wait_for(lambda: self.control is not None and not self.control["enabled"], timeout=5)

    def pause_and_require_text(self, pause, ask, media):
        if not self.enabled:
            return
        pause()
        self.require_paused()
        ask()
        assert self.samples == [22050, 22050] and media.execute.call_count == 3
