"""Explicit bounded test-only idle delays, never a production timing policy."""

import threading

from tests.meet_dialog_ack_delay import PROFILE as ACK_PROFILE

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

    def start_observation(self):
        # This older closed profile deliberately retains its startup injection.
        pass

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
    if profile not in (PROFILE, ACK_PROFILE) or soak_seconds != 300 or spoken or gpu:
        raise ValueError("test_cadence_delay_profile_invalid")
    return True


def install_cadence_delay(enabled, monkeypatch, *, url, profile=PROFILE):
    if not enabled:
        return None
    if profile == PROFILE:
        return DialogCadenceDelay(monkeypatch, url=url)
    if profile == ACK_PROFILE:
        from tests.meet_dialog_ack_delay import DialogAckDelay

        return DialogAckDelay(monkeypatch, url=url)
    raise ValueError("test_cadence_delay_profile_invalid")


def require_cadence_complete(delay):
    if delay is not None:
        delay.require_complete()


def start_cadence_observation(delay):
    if delay is not None:
        delay.start_observation()


def record_cadence_delay(delay, record_property):
    if delay is not None:
        record_property("dialog_cadence_delay", delay.report())
