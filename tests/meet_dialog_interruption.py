"""External headless Hub controls during real playback; explicitly synthetic PCM."""

import base64
import io
import math
import struct
import threading
import time
import wave

from ananta_contracts.meet_speech import speech_profile
from tests.test_meet_speech_binding import speech_result
from worker.meet_media.dialog_speech_output import DialogSpeechOutput

SAMPLES = 22050 * 10


class SyntheticToneWorker:
    """Contract test double: declared engine labels are not GPU evidence."""

    def __init__(self, seconds=10):
        if type(seconds) is not int or seconds not in {10, 20}:
            raise ValueError("test_tone_duration_invalid")
        self.seconds = seconds

    def execute(self, turn):
        frame = b"".join(struct.pack("<h", round(12000 * math.sin(2 * math.pi * 500 * n / 22050))) for n in range(441))
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setframerate(22050)
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.writeframes(frame * (50 * self.seconds))
        return speech_result(profile=turn["speech_profile"], samples=22050 * self.seconds) | {
            "task_id": turn["task_id"],
            "lease_id": turn["lease_id"],
            "audio": {"mime": "audio/wav", "base64": base64.b64encode(output.getvalue()).decode()},
            "text": "Synthetic Hub answer",
            "usage": {"input_tokens": 20, "output_tokens": 8},
        }


class HubInterruptionControl:
    """Test caller of actual Hub CAS; never modifies Worker or task storage."""

    def __init__(self, app, service, principal, task_id):
        self.app, self.service, self.principal, self.task_id = app, service, principal, task_id

    def pause(self):
        with self.app.app_context():
            controls = self.service.inspect(self.principal, "synthetic", self.task_id)["controls"]
            return self.service.control(
                self.principal,
                "synthetic",
                self.task_id,
                {
                    "expected_revision": controls["revision"],
                    **{name: controls[name]["enabled"] for name in ("chat", "audio", "screen")},
                    "speech": False,
                },
            )

    def stop(self):
        with self.app.app_context():
            return self.service.inspect(self.principal, "synthetic", self.task_id, stop=True)


class SpeechInterruption:
    def __init__(self, mode, monkeypatch, *, clock=time.monotonic):
        if mode not in {"pause", "stop"}:
            raise ValueError("test_interruption_mode_invalid")
        self.mode, self.clock = mode, clock
        self.condition = threading.Condition()
        self.live, self.closed = None, []
        tick, close = DialogSpeechOutput.tick, DialogSpeechOutput.close

        def observe_tick(output):
            tick(output)
            publication = output.publication
            with self.condition:
                self.live = (
                    None
                    if publication is None
                    else {
                        "played": publication.played,
                        "total": publication.receipt.total_samples,
                    }
                )
                self.condition.notify_all()

        def observe_close(output):
            publication = output.publication
            close(output)
            if publication is not None and not publication.completed:
                with self.condition:
                    self.closed.append(
                        {
                            "at": self.clock(),
                            "played": publication.played,
                            "sent": publication.sent,
                            "total": publication.receipt.total_samples,
                            "cleared": output.publication is None and output.pcm == b"" and output.binding is None,
                        }
                    )
                    self.live = None
                    self.condition.notify_all()

        monkeypatch.setattr(DialogSpeechOutput, "tick", observe_tick)
        monkeypatch.setattr(DialogSpeechOutput, "close", observe_close)

    def finish(self, control, observer, command, ask, completed, record_property):
        with self.condition:
            assert self.condition.wait_for(
                lambda: self.live is not None and 4410 <= self.live["played"] < SAMPLES - 22050,
                timeout=8,
            ), {"partial_playback_missing": self.live, "closed": self.closed}
        observer.require_remote(command)
        with self.condition:
            assert self.live is not None and not self.closed, {"live": self.live, "closed": self.closed}
        requested = self.clock()
        changed = control.pause() if self.mode == "pause" else control.stop()
        mutated = self.clock()
        with self.condition:
            assert self.condition.wait_for(lambda: bool(self.closed), timeout=3), "owned speech did not stop"
            closed = self.closed[0]
            assert requested <= closed["at"] <= mutated + 3, closed
            assert closed["cleared"] and 4410 <= closed["played"] < SAMPLES, closed
        assert command("audio_absent") == {"audio_absent": True}
        remote_seconds = self.clock() - mutated
        assert remote_seconds <= 4, {"remote_stop_seconds": remote_seconds}
        if self.mode == "pause":
            assert changed["controls"]["speech"]["enabled"] is False
            assert changed["controls"]["screen"]["enabled"] and changed["controls"]["chat"]["enabled"]
            observer.require_paused()
            assert command("screen") == {"moving_screen": True}
            ask()
            assert len(observer.answers) == 3 and observer.samples == [SAMPLES]
            assert self.live is None and len(self.closed) == 1
            assert control.stop()["status"] == "cancelled"
        else:
            assert changed["status"] == "cancelled"
        assert completed.wait(10), "parent cancellation did not stop the Worker"
        assert command("alone") == {"alone": True}
        record_property(
            "dialog_interruption",
            {
                "mode": self.mode,
                "synthetic_pcm": True,
                "actual_gpu": False,
                "local_stop_seconds": round(max(0, closed["at"] - mutated), 3),
                "remote_stop_seconds": round(remote_seconds, 3),
                "played": closed["played"],
                "sent": closed["sent"],
                "total": SAMPLES,
                "production_release_evidence": False,
            },
        )


def configure_interruption(observer, mode, monkeypatch):
    if mode is None:
        return None
    scenario = SpeechInterruption(mode, monkeypatch)
    observer.worker, observer.profile = SyntheticToneWorker(), speech_profile(max_seconds=10)
    return scenario


def finish_interruption(scenario, app, service, principal, started, observer, command, ask, completed, record_property):
    if scenario is None:
        return False
    scenario.finish(
        HubInterruptionControl(app, service, principal, started["task_id"]),
        observer,
        command,
        ask,
        completed,
        record_property,
    )
    return True
