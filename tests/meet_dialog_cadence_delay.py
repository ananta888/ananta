"""Explicit bounded test-only idle delays, never a production timing policy."""

import threading

PROFILE = "paired-idle-450-v1"


class DialogCadenceDelay:
    def __init__(self, monkeypatch, *, url):
        from playwright.sync_api import Page

        self.lock = threading.Lock()
        self.attempted = self.applied = 0
        wait = Page.wait_for_timeout

        def delayed(page, timeout):
            delay = False
            if page.url == url and type(timeout) is int and timeout == 100:
                with self.lock:
                    if self.attempted < 30:
                        delay = self.attempted % 3 != 2
                        self.attempted += 1
            result = wait(page, 450 if delay else timeout)
            if delay:
                with self.lock:
                    self.applied += 1
            return result

        monkeypatch.setattr(Page, "wait_for_timeout", delayed)

    def report(self):
        with self.lock:
            return {
                "profile": PROFILE,
                "attempted_idle_calls": self.attempted,
                "completed_delays": self.applied,
                "production_release_evidence": False,
            }

    def require_complete(self):
        with self.lock:
            if self.attempted != 30 or self.applied != 20:
                raise AssertionError("test_cadence_delay_not_fully_exercised")


def require_cadence_profile(profile, *, soak_seconds, spoken, gpu):
    if profile == "off":
        return False
    if profile != PROFILE or soak_seconds != 300 or spoken or gpu:
        raise ValueError("test_cadence_delay_profile_invalid")
    return True


def install_cadence_delay(enabled, monkeypatch, *, url):
    return DialogCadenceDelay(monkeypatch, url=url) if enabled else None


def require_cadence_complete(delay):
    if delay is not None:
        delay.require_complete()


def record_cadence_delay(delay, record_property):
    if delay is not None:
        record_property("dialog_cadence_delay", delay.report())
