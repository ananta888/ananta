"""Synthetic media and local-source observations for the real dialog fixture."""

import hashlib
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
        self.worker = None
        self.answers = []
        self.remote = []
        self.sender = []
        tick = DialogSpeechOutput.tick

        def observe(output):
            publication = output.publication
            started = time.monotonic()
            tick(output)
            if publication is not None and publication.completed and output.publication is None:
                if self.worker is not None:
                    self.sender.append(
                        output.page.evaluate("""async () => {
                      const reports = await Promise.all(window.__testPcs.map(pc => pc.getStats()));
                      const stats = reports.flatMap(r => [...r.values()]);
                      return {errors: window.__testTransformErrors, captures: window.__testCaptures,
                        codecs: stats.filter(s => s.type === 'codec').map(s => s.mimeType),
                        audio: stats.filter(s => s.type === 'outbound-rtp' && s.kind === 'audio')
                          .map(s => ({packets: s.packetsSent, bytes: s.bytesSent})),
                        sources: stats.filter(s => s.type === 'media-source' && s.kind === 'audio')
                          .map(s => ({energy: s.totalAudioEnergy, duration: s.totalSamplesDuration}))};
                    }""")
                    )
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
        started = time.monotonic()
        if self.worker is not None:
            media = self.worker.execute(turn)
            self.answers.append(
                {
                    "samples": media["speech"]["samples"],
                    "usage": media["usage"],
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "text_sha256": hashlib.sha256(media["text"].encode()).hexdigest(),
                }
            )
            return media
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
        deadline = time.monotonic() + (25 if self.worker is not None else 5)
        with self.condition:
            while len(self.samples) < count and not completed.is_set() and time.monotonic() < deadline:
                self.condition.wait(min(0.1, max(0, deadline - time.monotonic())))
            expected = (
                [item["samples"] for item in self.answers[:count]] if self.worker is not None else [22050] * count
            )
            assert len(expected) == count and self.samples == expected, {
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
        assert len(self.samples) == 2 and media.execute.call_count == 3

    def before_question(self, command):
        if self.worker is not None:
            assert command("audio_reset") == {"reset": True}

    def receive_answer(self, command):
        if self.worker is None:
            return command("answer")
        value = command("answer_correlated")
        assert value == {"correlated": True, "text_sha256": self.answers[-1]["text_sha256"]}
        return {"received": True}

    def require_remote(self, command):
        if self.worker is None:
            return
        value = command("audio_probe")
        assert set(value) == {
            "correlated",
            "failed",
            "peak",
            "active_windows",
            "windows",
            "audio_tracks",
            "running_contexts",
            "muted_audio_tracks",
            "captures",
            "transform_errors",
            "received_packets",
            "received_samples",
            "decrypt_contexts",
            "keyed_contexts",
            "matched_contexts",
        }, value
        assert value["failed"] is False and value["peak"] > 0.02 and value["active_windows"] > 10, {
            "receiver": value,
            "sender": self.sender,
        }
        assert value["captures"] == value["transform_errors"] == 0, value
        assert all(item["captures"] == 0 and item["errors"] == [] for item in self.sender), self.sender
        self.remote.append(value)
