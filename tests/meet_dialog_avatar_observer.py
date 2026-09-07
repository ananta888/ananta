"""Real Hub-controlled avatar scenario with explicitly selected synthetic/GPU voice."""

import threading
import time

from ananta_contracts.meet_speech import speech_profile
from tests.meet_dialog_interruption import SyntheticToneWorker
from worker.meet_media.avatar_browser import AvatarBrowserPort


def configure_avatar_speech(speech, actual_gpu):
    if actual_gpu:
        if speech.worker is None or speech.profile != speech_profile(max_seconds=20):
            raise ValueError("test_avatar_gpu_not_configured")
        return  # Preserve the independently provisioned real GPU transport.
    if speech.worker is not None:
        raise ValueError("test_avatar_voice_classification_conflict")
    speech.worker = SyntheticToneWorker()
    speech.profile = speech_profile(max_seconds=10)


class DialogAvatarObserver:
    def __init__(self, enabled, speech, monkeypatch, *, actual_gpu=False):
        self.enabled = enabled
        self.profiles = None
        self.start_options = {}
        self.actual_gpu = actual_gpu
        self.condition = threading.Condition()
        self.state, self.generation = "closed", 0
        if not enabled:
            return
        speech.capabilities.append("avatar.publish")
        configure_avatar_speech(speech, actual_gpu)
        status, close = AvatarBrowserPort.status, AvatarBrowserPort.close

        def observe(port):
            value = status(port)
            with self.condition:
                self.state = value["source"]["state"]
                self.generation = value["source"]["generation"]
                self.condition.notify_all()
            return value

        def observe_close(port):
            owned = port.token is not None
            close(port)
            if owned:
                with self.condition:
                    self.state = "closed"
                    self.condition.notify_all()

        monkeypatch.setattr(AvatarBrowserPort, "status", observe)
        monkeypatch.setattr(AvatarBrowserPort, "close", observe_close)

    def wait(self, state, timeout):
        with self.condition:
            assert self.condition.wait_for(lambda: self.state == state, timeout=timeout), {
                "avatar_state": self.state,
                "generation": self.generation,
                "expected": state,
            }
            return self.generation

    def finish(self, app, service, principal, started, speech, command, completed, failures, record_property):
        if not self.enabled:
            return False
        task_id = started["task_id"]

        def moving():
            observed = command("avatar")
            assert observed == {"moving_avatar": True}, {
                "remote": observed,
                "local": self.state,
                "generation": self.generation,
                "runtime_errors": failures,
            }

        def control(enabled):
            with app.app_context():
                current = service.inspect(principal, "synthetic", task_id)["controls"]
                body = {name: row["enabled"] for name, row in current.items() if name != "revision"}
                return service.control(
                    principal,
                    "synthetic",
                    task_id,
                    body | {"expected_revision": current["revision"], "avatar": enabled},
                )

        with app.app_context():
            initial = service.inspect(principal, "synthetic", task_id)
        assert initial["controls"]["avatar"]["enabled"] is False
        assert command("avatar_absent") == {"avatar_absent": True}
        control(True)
        self.wait("open", 12)
        moving()
        speech.before_question(command)
        assert command("ask") == {"sent": True}
        assert speech.receive_answer(command) == {"received": True}
        speech.require_completed(1, completed, failures)
        speech.require_remote(command)
        moving()
        before = self.wait("open", 3)
        control(False)
        stopped_at = time.monotonic()
        self.wait("closed", 3)
        local_stop_ms = (time.monotonic() - stopped_at) * 1000
        assert command("avatar_absent") == {"avatar_absent": True}
        remote_stop_ms = (time.monotonic() - stopped_at) * 1000
        assert remote_stop_ms <= 4000
        assert command("screen") == {"moving_screen": True}
        control(True)
        assert self.wait("open", 12) > before
        moving()
        with app.app_context():
            assert service.inspect(principal, "synthetic", task_id, stop=True)["status"] == "cancelled"
        assert completed.wait(10), failures
        assert command("alone") == {"alone": True}
        record_property(
            "dialog_avatar",
            {
                "synthetic_policy": True,
                "synthetic_audio": not self.actual_gpu,
                "actual_gpu": self.actual_gpu,
                "production_release_evidence": False,
                "local_pause_ms": round(local_stop_ms, 2),
                "remote_pause_ms": round(remote_stop_ms, 2),
                "generations": self.generation,
                "speech_samples": speech.samples,
                "answers": speech.answers,
                "remote_audio": speech.remote,
            },
        )
        return True


def make_avatar_observer(mode, speech, monkeypatch, *, actual_gpu=False):
    if mode in ("image", "image-renewal"):
        if actual_gpu:
            raise ValueError("test_image_avatar_gpu_not_configured")
        from tests.meet_dialog_image_avatar_scenario import ImageAvatarScenario

        return ImageAvatarScenario(speech, monkeypatch, renewal=mode == "image-renewal")
    return DialogAvatarObserver(mode, speech, monkeypatch, actual_gpu=actual_gpu)
