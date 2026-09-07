"""One controlled Worker-poll stall; browser and Hub clocks remain real."""

import threading
import time

from ananta_contracts.meet_speech import speech_profile
from tests.meet_dialog_interruption import SyntheticToneWorker
from worker.meet_media.browser_pcm_feeder import BrowserPcmFeeder


class PlaybackStallScenario:
    profiles = None
    start_options = {"duration_seconds": 90}

    def __init__(self, speech, monkeypatch):
        if not speech.enabled or speech.worker is not None:
            raise ValueError("test_playback_stall_requires_synthetic_audio")
        speech.worker, speech.profile = SyntheticToneWorker(), speech_profile(max_seconds=10)
        self.lock = threading.Lock()
        self.armed = self.ready = False
        self.started = threading.Event()
        self.returned = threading.Event()
        self.at = None
        self.source_state = None
        pulse, status = BrowserPcmFeeder.pulse, BrowserPcmFeeder.status

        def fresh(feeder, authority):
            result = pulse(feeder, authority)
            with self.lock:
                if self.armed and result is True:
                    self.ready = True
                    self.armed = False
            return result

        def stalled(feeder):
            with self.lock:
                stall = self.ready
                self.ready = False
            if not stall:
                return status(feeder)
            self.at = time.monotonic()
            self.started.set()
            try:
                feeder.page.wait_for_timeout(4500)
                result = status(feeder)
                self.source_state = result.get("state") if isinstance(result, dict) else None
                return result
            finally:
                self.returned.set()

        monkeypatch.setattr(BrowserPcmFeeder, "pulse", fresh)
        monkeypatch.setattr(BrowserPcmFeeder, "status", stalled)

    def finish(self, app, service, principal, started, speech, command, completed, failures, record_property):
        speech.before_question(command)
        assert command("ask") == {"sent": True}
        assert speech.receive_answer(command) == {"received": True}
        speech.require_remote(command)
        with self.lock:
            self.armed = True
        assert self.started.wait(3), "fresh Hub pulse did not arm the one bounded test stall"
        # The old 200-ms Python-fed queue cannot sustain this. Require new remote
        # audio after half a second without a Python poll, not previous windows.
        assert not completed.wait(0.5), failures
        speech.before_question(command)
        speech.require_remote(command)
        assert not self.returned.is_set()
        assert command("audio_absent") == {"audio_absent": True}
        remote_ms = (time.monotonic() - self.at) * 1000
        assert remote_ms <= 3500 and not self.returned.is_set(), remote_ms
        assert completed.wait(10), "stalled Worker did not finish its bounded failure"
        assert failures == ["meet_dialog_control_state_stale"], failures
        assert self.source_state == "failed" and speech.samples == []
        assert command("alone") == {"alone": True}
        with app.app_context():
            assert service.inspect(principal, "synthetic", started["task_id"])["status"] == "failed"
        record_property(
            "browser_pcm_controller_stall",
            {
                "synthetic_pcm": True,
                "actual_gpu": False,
                "production_release_evidence": False,
                "worker_stall_ms": 4500,
                "remote_stop_ms": round(remote_ms, 2),
                "stopped_before_worker_returned": True,
                "completed_replies": 0,
            },
        )
        return True
