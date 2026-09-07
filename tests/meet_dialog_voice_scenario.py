"""Live voice CAS/delivery with explicit test-only profile policy, never release proof."""

import copy
import threading
import time

from agent.services.meet_chat_policy import ChatReplyPolicy
from ananta_contracts.meet_speech import speech_profile
from ananta_contracts.persona_voice import inspect_voice_descriptor, voice_descriptor
from tests.meet_dialog_interruption import SpeechInterruption, SyntheticToneWorker
from worker.meet_media.dialog_speech_output import DialogSpeechOutput


class SyntheticVoiceProfiles:
    """Deterministic metadata/policy double; issues no SRC/RUN or production grant."""

    def __init__(self):
        self.lock = threading.RLock()
        self.revoked = False
        self.catalog = {}
        for name in ("neutral", "whisper"):
            voice_id = "piper.de_DE.thorsten_emotional.medium." + name
            descriptor = inspect_voice_descriptor(voice_descriptor(voice_id))
            reference = {
                "tenant_id": "synthetic",
                "project_id": "synthetic",
                "artifact_id": "voice-" + name,
                "revision": 1,
                "sha256": descriptor.source_sha256,
                "kind": "voice",
                "classification": "test_only",
            }
            pin = {
                "organization_id": "synthetic",
                "owner_kind": "organization",
                "owner_id": "synthetic",
                "selection_digest": descriptor.source_sha256,
            }
            self.catalog[name] = (reference, pin, voice_id)

    def prepare(self, principal, project, selection, purpose, *, max_seconds=20):
        with self.lock:
            if self.revoked or (principal.subject_id, principal.tenant_id, principal.project_id, project, purpose) != (
                "owner",
                "synthetic",
                "synthetic",
                "synthetic",
                "publish",
            ):
                raise PermissionError("synthetic_voice_denied")
            for reference, pin, voice_id in self.catalog.values():
                if selection == pin:
                    return {
                        "reference": copy.deepcopy(reference),
                        "speech_profile": speech_profile(voice_id=voice_id, max_seconds=max_seconds),
                    }, dict(pin)
            raise PermissionError("synthetic_voice_pin_unknown")

    def select(self, principal, project, selection, purpose):
        value, pin = self.prepare(principal, project, selection, purpose)
        return value["reference"], pin

    def revoke(self):
        with self.lock:
            self.revoked = True


class NoVoiceScenario:
    profiles = None
    start_options = {}

    def finish(self, *_args):
        return False


class VoiceSelectionScenario:
    def __init__(self, speech, monkeypatch, *, actual_gpu=False):
        if not speech.enabled or actual_gpu != (speech.worker is not None):
            raise ValueError("test_voice_gpu_classification_conflict")
        self.actual_gpu = actual_gpu
        if not actual_gpu:
            speech.worker, speech.profile = SyntheticToneWorker(), speech_profile(max_seconds=10)
        self.profiles = SyntheticVoiceProfiles()
        self.start_options = {"voice_profiles": True, "duration_seconds": 180}
        self.playback = SpeechInterruption("pause", monkeypatch)
        self.condition = threading.Condition()
        self.projection = None
        update = DialogSpeechOutput.update

        def observe(output, receipt, controls, voice=None):
            update(output, receipt, controls, voice)
            with self.condition:
                self.projection = copy.deepcopy(voice)
                self.condition.notify_all()

        monkeypatch.setattr(DialogSpeechOutput, "update", observe)

    def wait(self, state, revision, timeout=5):
        with self.condition:
            assert self.condition.wait_for(
                lambda: self.projection is not None
                and self.projection["state"] == state
                and self.projection["speech_revision"] == revision,
                timeout=timeout,
            ), {"voice_projection": self.projection, "expected": [state, revision]}

    def finish(self, app, service, principal, started, speech, command, completed, failures, record_property):
        task_id = started["task_id"]

        def complete(count):
            try:
                speech.require_completed(count, completed, failures)
            except AssertionError as error:
                raise AssertionError(
                    {
                        "speech": str(error),
                        "callbacks": speech.callbacks.report(),
                        "answers": speech.answers,
                        "closed": self.playback.closed,
                        "slow_rpc": speech.rpc.report(),
                    }
                ) from error

        def select(name):
            with app.app_context():
                current = service.inspect(principal, "synthetic", task_id)
                changed = service.select_voice(
                    principal,
                    "synthetic",
                    task_id,
                    {
                        "expected_revision": current["controls"]["revision"],
                        "profile": self.profiles.catalog[name][1],
                    },
                )
                for source in ("chat", "screen", "audio"):
                    assert changed["controls"][source] == current["controls"][source]
                return changed["controls"]["speech"]["revision"]

        revision = select("neutral")
        self.wait("ready", revision)
        speech.before_question(command)
        assert command("ask") == {"sent": True}
        assert speech.receive_answer(command) == {"received": True}
        complete(1)
        speech.require_remote(command)
        assert speech.answers[-1]["voice_id"] == self.profiles.catalog["neutral"][2]
        # Keep the actual Hub cooldown; never turn policy denial into a retry.
        assert not completed.wait(ChatReplyPolicy().cooldown_ms / 1000 + 0.1), failures
        revision = select("whisper")
        self.wait("ready", revision)
        speech.before_question(command)
        assert command("ask") == {"sent": True}
        assert speech.receive_answer(command) == {"received": True}
        assert speech.answers[-1]["voice_id"] == self.profiles.catalog["whisper"][2]
        if self.actual_gpu:
            complete(2)
        else:
            with self.playback.condition:
                assert self.playback.condition.wait_for(
                    lambda: self.playback.live is not None and self.playback.live["played"] >= 4410, timeout=8
                ), {"playback": self.playback.live, "errors": failures}
        speech.require_remote(command)
        revoked_at = time.monotonic()
        self.profiles.revoke()
        self.wait("blocked", revision)
        local_ms = (time.monotonic() - revoked_at) * 1000
        if not self.actual_gpu:
            with self.playback.condition:
                assert self.playback.condition.wait_for(lambda: bool(self.playback.closed), timeout=3)
                closed = self.playback.closed[-1]
                assert closed["cleared"] and 4410 <= closed["played"] < closed["total"], closed
        assert command("audio_absent") == {"audio_absent": True}
        remote_ms = (time.monotonic() - revoked_at) * 1000
        assert remote_ms <= 4000
        assert command("screen") == {"moving_screen": True}
        with app.app_context():
            state = service.inspect(principal, "synthetic", task_id)
            assert state["status"] == "in_progress" and state["controls"]["chat"]["enabled"]
            assert state["controls"]["speech"]["enabled"]  # Revocation is independent of operator controls.
            assert service.inspect(principal, "synthetic", task_id, stop=True)["status"] == "cancelled"
        assert completed.wait(10), failures
        assert command("alone") == {"alone": True}
        record_property(
            "dialog_voice_selection",
            {
                "synthetic_profile_policy": True,
                "synthetic_audio": not self.actual_gpu,
                "actual_gpu": self.actual_gpu,
                "production_release_evidence": False,
                "human_capture_used": False,
                "local_revocation_ms": round(local_ms, 2),
                "remote_revocation_ms": round(remote_ms, 2),
                "speech_samples": speech.samples,
                "answers": speech.answers,
                "remote_audio": speech.remote,
                "interrupted_playback": self.playback.closed,
            },
        )
        return True


def make_voice_scenario(enabled, speech, monkeypatch, *, actual_gpu=False):
    if enabled == "latency":
        if actual_gpu:
            raise ValueError("test_voice_latency_requires_synthetic_audio")
        from tests.meet_dialog_exchange_latency import inject_exchange_latency

        inject_exchange_latency(monkeypatch)
    return VoiceSelectionScenario(speech, monkeypatch, actual_gpu=actual_gpu) if enabled else NoVoiceScenario()
