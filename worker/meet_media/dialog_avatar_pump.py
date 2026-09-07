"""Execute only current Hub avatar controls; no self-authorization or inference."""

import time

from ananta_contracts.meet_avatar_source import validate_avatar_snapshot
from worker.meet_media.avatar_browser import AvatarBrowserPort


class DialogAvatarPump:
    def __init__(
        self, page, assignment, *, browser=None, clock=time.time, monotonic=time.monotonic, profile="neutral-ai-v1"
    ):
        if profile not in ("neutral-ai-v1", "persona-image-v1"):
            raise ValueError("meet_avatar_profile_invalid")
        self.profile = profile
        self.assignment, self.clock, self.monotonic = assignment, clock, monotonic
        self.url = assignment["meeting"]["origin"] + "/machine"
        self.page = page
        self.browser = browser if browser is not None else AvatarBrowserPort(page, self.url)
        self.binding = None
        self.active = self.failed = False
        self.fresh_until = self.started_at = self.expires_at = 0

    def _binding(self, receipt, control):
        lease = receipt["lease"]
        if receipt["roomId"] != self.assignment["meeting"]["room_id"] or self.page.url != self.url:
            raise ValueError("meet_avatar_scope_changed")
        deadline = min(self.assignment["deadline"] * 1000, lease["expiresAt"])
        if self.clock() * 1000 >= deadline:
            raise ValueError("meet_avatar_expired")
        return (
            lease["sessionId"],
            lease["generation"],
            receipt["peerId"],
            receipt["membershipEpoch"],
            deadline,
            control["revision"],
            control["since"],
        )

    def update(self, receipt, controls):
        control = controls.get("avatar")
        if not control or not control["enabled"] or "avatar.publish" not in self.assignment["capabilities"]:
            self.close()
            return
        try:
            binding = self._binding(receipt, control)
            if binding != self.binding:
                self._stop()
                self.binding, self.failed = binding, False
            elif self.monotonic() >= self.fresh_until:
                # A delayed Hub exchange cannot revive the expired browser
                # generation. Only this NEW authenticated update may start anew.
                self._stop()
            self.fresh_until = self.monotonic() + 2.5
            if self.failed:
                return
            if self.active and self.clock() * 1000 >= self.expires_at:
                self._stop()
            if not self.active:
                self.active = True
                self.started_at = self.monotonic()
                self.expires_at = min(self.clock() * 1000 + 30000, binding[4])
                self.browser.start("avatar:" + self.assignment["session_id"])
            self.browser.pulse()  # Never called by tick or an autonomous browser timer.
            self.tick()
        except Exception:
            self._fail()

    def tick(self):
        if not self.active:
            return
        try:
            if self.page.url != self.url or self.monotonic() >= self.fresh_until:
                raise ValueError("meet_avatar_hub_state_stale")
            if self.clock() * 1000 >= self.expires_at:
                self._stop()  # Wait for a new Hub update; tick cannot reopen it.
                return
            state = validate_avatar_snapshot(
                self.browser.status(), self.clock() * 1000, self.binding[4], profile=self.profile
            )
            if state["phase"] == "pending" and self.monotonic() - self.started_at >= 10:
                raise ValueError("meet_avatar_setup_timeout")
            self.expires_at = state["source"]["expiresAt"]
        except Exception:
            self._fail()

    def _stop(self):
        self.active = False
        self.expires_at = 0
        self.browser.close()

    def _fail(self):
        self.failed = True
        try:
            self._stop()
        except Exception:
            pass  # The browser's independent 2.5-s pulse watchdog remains armed.

    def close(self):
        self.binding = None
        self.fresh_until = 0
        try:
            self._stop()
        except Exception:
            self.failed = True

    def invalidate(self):
        self.close()
