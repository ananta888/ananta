"""Synthetic media and local-source observations for the real dialog fixture."""

import hashlib
import threading
import time
from collections import deque

from ananta_contracts.meet_media_failures import CODES as MEDIA_FAILURE_CODES
from ananta_contracts.meet_speech import speech_profile
from tests.meet_dialog_callback_observer import DialogCallbackObserver
from tests.meet_dialog_control_observer import DialogControlObserver
from tests.meet_dialog_rpc_observer import DialogRpcObserver
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
        self.inference_failures = deque(maxlen=8)
        self.remote = []
        self.sender = []
        self.rpc = DialogRpcObserver(monkeypatch)
        self.callbacks = DialogCallbackObserver(monkeypatch)
        self.control_reads = DialogControlObserver(monkeypatch)
        self.last_tick = None
        self.last_publication = None
        self.max_tick_gap_ms = 0
        self.acceptances = deque(maxlen=8)
        accept = DialogSpeechOutput.accept

        def observe_accept(output, result, binding):
            started = time.monotonic()
            fresh_before = output.monotonic() < output.fresh_until
            accepted = accept(output, result, binding)
            self.acceptances.append(
                {
                    "accepted": accepted,
                    "fresh_before": fresh_before,
                    "fresh_after": output.monotonic() < output.fresh_until,
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )
            return accepted

        monkeypatch.setattr(DialogSpeechOutput, "accept", observe_accept)
        tick = DialogSpeechOutput.tick

        def observe(output):
            publication = output.publication
            started = time.monotonic()
            if publication is not self.last_publication:
                self.last_publication = publication
                self.last_tick = None
                self.max_tick_gap_ms = 0
            if publication is not None and self.last_tick is not None:
                self.max_tick_gap_ms = max(self.max_tick_gap_ms, round((started - self.last_tick) * 1000))
            self.last_tick = started
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
                        "max_tick_gap_ms": self.max_tick_gap_ms,
                        "slow_rpc": self.rpc.report(),
                        "callbacks": self.callbacks.report(),
                        "browser": output.page.evaluate("window.anantaMachine.speech.status()"),
                        "worklet_errors": output.page.evaluate("window.__testSpeechErrors || []"),
                    }
                )

        monkeypatch.setattr(DialogSpeechOutput, "tick", observe)
        update = DialogSpeechOutput.update

        def observe_update(output, receipt, controls, *voice):
            update(output, receipt, controls, *voice)
            with self.condition:
                self.control = controls.get("speech")
                self.condition.notify_all()

        monkeypatch.setattr(DialogSpeechOutput, "update", observe_update)

    def execute(self, turn):
        started = time.monotonic()
        if self.worker is not None:
            try:
                media = self.worker.execute(turn)
            except Exception as error:
                code = getattr(error, "code", None)
                allowed = (*MEDIA_FAILURE_CODES, "meet_worker_unavailable", "meet_worker_result_unauthorized")
                self.inference_failures.append(
                    {
                        "code": code if isinstance(code, str) and code in allowed else "worker_execution_failed",
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                    }
                )
                raise
            self.answers.append(
                {
                    "samples": media["speech"]["samples"],
                    "usage": media["usage"],
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "text_sha256": hashlib.sha256(media["text"].encode()).hexdigest(),
                    "voice_id": media["speech"]["profile"]["voice_id"],
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
                "runtime_errors": list(failures),
                "control_timing": self.control_reads.report(),
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

    def receive_answer(self, command, *, failures=()):
        if self.worker is None:
            return command("answer")
        value = command("answer_correlated")
        assert self.answers and value == {"correlated": True, "text_sha256": self.answers[-1]["text_sha256"]}, {
            "received": value,
            "generated_answers": len(self.answers),
            "inference_failures": list(self.inference_failures),
            "runtime_errors": list(failures),
            "acceptances": list(self.acceptances),
            "control_timing": self.control_reads.report(),
            "callbacks": self.callbacks.report(),
            "slow_rpc": self.rpc.report(),
        }
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
