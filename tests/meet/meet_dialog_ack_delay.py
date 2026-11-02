"""One owned delayed acknowledgement followed by a bounded delayed idle."""

import threading

PROFILE = "paired-ack-350-v1"


class DialogAckDelay:
    def __init__(self, monkeypatch, *, url):
        from playwright.sync_api import Page

        from worker.meet_media.screen_frame_delivery import BrowserScreenFrames

        self.lock = threading.Lock()
        self.attempted = self.ack_completed = self.idles_completed = self.idle_delayed = 0
        self.armed = False
        self.phase = "unarmed"
        begin, wait = BrowserScreenFrames.begin, Page.wait_for_timeout

        def acknowledged(frames, generation, sequence, jpeg):
            with self.lock:
                selected = (
                    self.armed
                    and not self.attempted
                    and frames.page.url == url
                    and type(sequence) is int
                    and sequence >= 3
                )
                if selected:
                    self.attempted = 1
            result = begin(frames, generation, sequence, jpeg)
            if selected:
                wait(frames.page, 350)
                with self.lock:
                    self.ack_completed = 1
                    self.phase = "first_idle"
            return result

        def idle(page, timeout):
            with self.lock:
                selected = page.url == url and type(timeout) is int and timeout == 100
                phase = self.phase if selected else None
                if phase in {"first_idle", "second_idle"}:
                    self.phase = "inflight"
            result = wait(page, 350 if phase == "second_idle" else timeout)
            if phase in {"first_idle", "second_idle"}:
                with self.lock:
                    self.idles_completed += 1
                    self.idle_delayed += int(phase == "second_idle")
                    self.phase = "second_idle" if phase == "first_idle" else "done"
            return result

        monkeypatch.setattr(BrowserScreenFrames, "begin", acknowledged)
        monkeypatch.setattr(Page, "wait_for_timeout", idle)

    def start_observation(self):
        with self.lock:
            self.armed = True

    def report(self):
        with self.lock:
            return {
                "profile": PROFILE,
                "attempted_ack_calls": self.attempted,
                "completed_ack_delays": self.ack_completed,
                "completed_idle_steps": self.idles_completed,
                "completed_idle_delays": self.idle_delayed,
                "production_release_evidence": False,
            }

    def require_complete(self):
        with self.lock:
            if (self.attempted, self.ack_completed, self.idles_completed, self.idle_delayed) != (1, 1, 2, 1):
                raise AssertionError("test_ack_delay_not_fully_exercised")
