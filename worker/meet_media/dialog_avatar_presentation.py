"""Compose freshly authorized avatar artwork with the bounded source pump."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

from ananta_contracts.meet_avatar_image import validate_avatar_projection
from ananta_contracts.meet_avatar_video import validate_video_projection
from worker.meet_media.avatar_browser import AvatarBrowserPort
from worker.meet_media.dialog_avatar_pump import DialogAvatarPump
from worker.meet_media.dialog_avatar_video_browser import VideoAvatarBrowser


class ImageAvatarBrowser:
    """Narrow source-start adapter; current Hub policy remains outside the browser."""

    def __init__(self, browser, assignment, image):
        self.browser, self.assignment, self.image = browser, assignment, image

    def start(self, source_id):
        self.browser.start_image(
            source_id, self.image, tenant_id=self.assignment["tenant_id"], project_id=self.assignment["project_id"]
        )

    def status(self):
        return self.browser.status()

    def pulse(self):
        return self.browser.pulse()

    def close(self):
        return self.browser.close()


class DialogAvatarPresentation:
    def __init__(self, page, hub, assignment, *, browser=None, pool=None, clock=time.time, monotonic=time.monotonic):
        if assignment.get("avatar_images") is not True or "avatar.publish" not in assignment["capabilities"]:
            raise ValueError("meet_avatar_images_not_negotiated")
        self.page, self.hub, self.assignment = page, hub, assignment
        self.clock, self.monotonic = clock, monotonic
        self.browser = (
            browser if browser is not None else AvatarBrowserPort(page, assignment["meeting"]["origin"] + "/machine")
        )
        self.pool = (
            pool if pool is not None else ThreadPoolExecutor(max_workers=1, thread_name_prefix="meet-avatar-artwork")
        )
        self.pending = self.pump = self.key = self.failed_key = None
        self.closed = False

    def update(self, receipt, controls, projection):
        if self.closed:
            raise ValueError("meet_avatar_presentation_closed")
        if (
            self.page.url != self.assignment["meeting"]["origin"] + "/machine"
            or receipt["roomId"] != self.assignment["meeting"]["room_id"]
        ):
            self.invalidate()
            raise ValueError("meet_avatar_scope_changed")
        validator = (
            validate_video_projection if self.assignment.get("avatar_videos") is True else validate_avatar_projection
        )
        projection = validator(projection)
        control = controls.get("avatar")
        if not control or not control["enabled"] or projection["state"] != "ready":
            self.invalidate()
            return
        if projection["mode"] != "neutral-ai-v1":
            self._check_binding(receipt, control, projection["binding"])
        key = (json.dumps(projection, sort_keys=True, separators=(",", ":")), control["revision"], control["since"])
        if key != self.key:
            self._stop()
            self.key, self.failed_key = key, None
        if self.failed_key == key:
            return
        if projection["mode"] == "neutral-ai-v1":
            if self.pending is not None and self.pending[0].done():
                self.pending = None
            if self.pump is None:
                self._activate("neutral-ai-v1")
        else:
            self._hydrate(key, projection)
        if self.pump is not None:
            # The only activation/pulse entry point is a new authenticated Hub
            # update, including completion of asynchronous artwork hydration.
            self.pump.update(receipt, controls)

    def _hydrate(self, key, projection):
        if self.pending is not None and self.pending[0].done():
            future, previous = self.pending
            self.pending = None
            if previous == key:
                try:
                    self._activate(projection["mode"], future.result())
                except Exception:
                    self.failed_key = key
                    return
        if self.pump is None and self.pending is None:
            fetch, arguments = self.hub.avatar_image, [dict(projection["binding"]), dict(projection["reference"])]
            if projection["mode"] == "persona-video-v1":
                fetch = self.hub.avatar_video
                arguments.append(projection["repeat_mode"])
            self.pending = (
                self.pool.submit(fetch, *arguments),
                key,
            )
        # A stale in-flight fetch retains its sole slot until it actually ends.
        # Repeated selection changes never enqueue another request behind it.

    def _activate(self, profile, image=None):
        browser = self.browser if image is None else ImageAvatarBrowser(self.browser, self.assignment, image)
        if profile == "persona-video-v1":
            browser = VideoAvatarBrowser(self.browser, self.assignment, image)
        if profile != "neutral-ai-v1" and image is None:
            raise ValueError("meet_avatar_image_missing")
        self.pump = DialogAvatarPump(
            self.page, self.assignment, browser=browser, clock=self.clock, monotonic=self.monotonic, profile=profile
        )

    def _check_binding(self, receipt, control, binding):
        lease = receipt["lease"]
        expected = {
            name: self.assignment[name]
            for name in ("tenant_id", "project_id", "task_id", "lease_id", "runtime_id", "session_id")
        }
        expected |= {
            "room_id": self.assignment["meeting"]["room_id"],
            "meet_session_id": lease["sessionId"],
            "own_peer_id": receipt["peerId"],
            "generation": lease["generation"],
            "membership_epoch": receipt["membershipEpoch"],
            "avatar_revision": control["revision"],
            "deadline_ms": min(self.assignment["deadline"] * 1000, lease["expiresAt"]),
        }
        if (
            any(binding[name] != value for name, value in expected.items())
            or self.clock() * 1000 >= binding["deadline_ms"]
        ):
            self.invalidate()
            raise ValueError("meet_avatar_image_authority_changed")

    def tick(self):
        if self.pump is not None:
            self.pump.tick()  # No fetch, result consumption, activation or pulse.

    def _stop(self):
        pump, self.pump = self.pump, None
        if pump is not None:
            pump.close()

    def invalidate(self):
        self._stop()
        self.key = self.failed_key = None
        if self.pending is not None and self.pending[0].done():
            self.pending = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.invalidate()
        if self.pending is not None:
            self.pending[0].cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)
