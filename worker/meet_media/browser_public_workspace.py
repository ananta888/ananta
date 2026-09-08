"""One delegated public-document page plus separate sanitized presentation surface."""

import math
import time

from ananta_contracts.browser_public_fetch import MAX_DOCUMENT_BYTES
from ananta_contracts.browser_view_generation import BrowserViewGeneration
from worker.meet_media.browser_public_renderer import SanitizedDocumentView
from worker.meet_media.browser_public_snapshot import PublicDocumentSnapshot

_SHAPE = "window.innerWidth === 640 && window.innerHeight === 360"


class PublicDocumentWorkspace:
    """No networking or task creation. Caller supplies a still-current Hub assignment."""

    def __init__(
        self,
        browser,
        generation,
        *,
        deadline,
        require_current,
        view_factory=SanitizedDocumentView,
        snapshot_factory=PublicDocumentSnapshot,
        clock=time.monotonic,
    ):
        now = clock()
        if type(generation) is not BrowserViewGeneration:
            raise ValueError("browser_workspace_generation_invalid")
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or not now < deadline <= now + 30:
            raise ValueError("browser_workspace_deadline_invalid")
        self.generation, self.deadline, self.require_current, self.clock = generation, deadline, require_current, clock
        self.browser, self.view_factory, self.snapshot_factory = browser, view_factory, snapshot_factory
        self.context = self.page = self.view = self.snapshot = self.last_snapshot = None
        self.closed = self.loaded = self.revoked = False

    def _current(self, generation):
        try:
            now = self.clock()
            if (
                self.closed
                or self.revoked
                or type(generation) is not BrowserViewGeneration
                or generation != self.generation
            ):
                raise ValueError()
            if not math.isfinite(now) or now >= self.deadline:
                raise ValueError()
            self.require_current()
            if self.loaded and (
                self.page.is_closed()
                or self.page.url != "about:blank"
                or self.context.pages != [self.page]
                or self.page.evaluate(_SHAPE) is not True
            ):
                raise ValueError()
        except Exception:
            self.close()
            raise ValueError("browser_workspace_inactive") from None

    def load(self, content, generation):
        self._current(generation)
        try:
            if (
                self.loaded
                or not isinstance(content, str)
                or not 0 < len(content.encode("utf-8")) <= MAX_DOCUMENT_BYTES
            ):
                raise ValueError()
            self.context = self.browser.new_context(
                java_script_enabled=False,
                permissions=[],
                accept_downloads=False,
                service_workers="block",
                viewport={"width": 640, "height": 360},
                device_scale_factor=1,
            )
            self.context.route("**/*", self._deny_request)
            self.context.route_web_socket("**/*", self._deny_socket)
            self.page = self.context.new_page()
            self.page.set_default_timeout(1000)
            self.context.on("page", lambda _: self._revoke())
            self.page.on("download", lambda _: self._revoke())
            self.page.on("crash", self._revoke)
            self.page.on("framenavigated", lambda _: self._revoke())
            # No original URL/base URL, cookies, response headers or origin storage.
            # Resource routes are aborted; JS is disabled at context creation.
            self.page.set_content(content, wait_until="domcontentloaded")
            self.loaded = True
            self._current(generation)
            self.snapshot = self.snapshot_factory(self.page)
            self.view = self.view_factory(self.browser, generation, deadline=self.deadline, clock=self.clock)
            self._refresh(generation)
        except Exception:
            self.close()
            raise ValueError("browser_workspace_load_denied") from None

    def _deny_request(self, route):
        try:
            if route.request.is_navigation_request():
                self._revoke()
        finally:
            route.abort()

    def _deny_socket(self, socket):
        try:
            socket.close()
        finally:
            self._revoke()

    def _revoke(self):
        # Event callbacks invalidate immediately without re-entering synchronous
        # Playwright teardown during new_page/goto. The next foreground check
        # closes both owned contexts before consuming any frame.
        self.revoked = True
        self.last_snapshot = None
        if self.view is not None:
            self.view.pending = None
            self.view.ready = False

    def _refresh(self, generation):
        self._current(generation)
        value = self.snapshot.read()
        self._current(generation)
        if value != self.last_snapshot:
            self.view.render(value, generation)
            self.last_snapshot = value

    def take(self, generation):
        self._current(generation)
        try:
            if not self.loaded or self.view is None:
                return None
            self._refresh(generation)
            frame = self.view.take(generation)
            self._current(generation)
            return frame
        except Exception:
            self.close()
            raise ValueError("browser_workspace_view_denied") from None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.last_snapshot = self.snapshot = None
        for resource in (self.view, self.context):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass  # One dead browser target cannot retain the other surface.
