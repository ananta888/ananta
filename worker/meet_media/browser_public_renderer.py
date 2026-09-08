"""One generation of sanitized continuous frames; no source browser or Hub policy."""

import math
import time

from ananta_contracts.browser_public_view import validate_public_view
from ananta_contracts.browser_view_generation import BrowserViewGeneration
from worker.meet_media.browser_public_view_page import CHECK, DOCUMENT, RENDER


class SanitizedDocumentView:
    def __init__(self, browser, generation: BrowserViewGeneration, *, deadline, clock=time.monotonic):
        now = clock()
        if type(generation) is not BrowserViewGeneration:
            raise ValueError("browser_view_generation_invalid")
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or not now < deadline <= now + 30:
            raise ValueError("browser_view_deadline_invalid")
        self.generation, self.deadline, self.clock = generation, deadline, clock
        self.pending, self.cdp, self.context = None, None, None
        self.closed, self.ready = False, False
        try:
            self.context = browser.new_context(
                permissions=[],
                accept_downloads=False,
                service_workers="block",
                viewport={"width": 640, "height": 360},
                device_scale_factor=1,
            )
            self.context.route("**/*", self._deny)
            self.context.route_web_socket("**/*", self._socket_denied)
            self.page = self.context.new_page()
            self.page.set_default_timeout(1000)
            self.context.on("page", lambda _: self.close())
            self.page.on("download", lambda _: self.close())
            self.page.on("crash", lambda: self.close())
            self.page.set_content(DOCUMENT)
            self.page.on("framenavigated", lambda _: self.close())
            self.cdp = self.context.new_cdp_session(self.page)
            self.cdp.on("Page.screencastFrame", self._receive)
            self.cdp.send(
                "Page.startScreencast",
                {"format": "jpeg", "quality": 70, "maxWidth": 640, "maxHeight": 360, "everyNthFrame": 1},
            )
        except Exception:
            self.close()
            raise ValueError("browser_view_start_failed") from None

    def _deny(self, route):
        try:
            route.abort()
        finally:
            self.close()

    def _socket_denied(self, socket):
        try:
            socket.close()
        finally:
            self.close()

    def _current(self, generation):
        now = self.clock()
        if (
            self.closed
            or type(generation) is not BrowserViewGeneration
            or generation != self.generation
            or not math.isfinite(now)
            or now >= self.deadline
        ):
            self.close()
            raise ValueError("browser_view_generation_inactive")

    def render(self, value, generation):
        self._current(generation)
        try:
            view = validate_public_view(value)
            if view["state"] != "ready":
                raise ValueError()
            self.pending = None
            self.page.evaluate(RENDER, view["blocks"])
            self._current(generation)
            self.ready = True
        except Exception:
            self.close()
            raise ValueError("browser_view_content_blocked") from None

    def _receive(self, event):
        try:
            if self.closed:
                return
            self._current(self.generation)
            if not self.ready:
                return
            if type(event) is not dict:
                raise ValueError()
            data = event.get("data")
            metadata = event.get("metadata", {})
            if (
                type(data) is not str
                or not 0 < len(data) <= 350_000
                or type(metadata) is not dict
                or metadata.get("deviceWidth") != 640
                or metadata.get("deviceHeight") != 360
            ):
                raise ValueError()
            self.pending = self.clock(), data
        except Exception:
            self.close()
        finally:
            if not self.closed:
                try:
                    self.cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})
                except Exception:
                    self.close()

    def take(self, generation):
        self._current(generation)
        try:
            if self.page.url != "about:blank" or len(self.context.pages) != 1 or self.page.evaluate(CHECK) is not True:
                raise ValueError()
            self._current(generation)
            pending, self.pending = self.pending, None
            return pending[1] if pending and self.clock() - pending[0] <= 1 else None
        except Exception:
            self.close()
            raise ValueError("browser_view_generation_inactive") from None

    def close(self):
        if self.closed:
            return
        self.closed, self.ready, self.pending = True, False, None
        try:
            if self.cdp is not None:
                self.cdp.send("Page.stopScreencast")
        except Exception:
            pass  # An already dead target cannot prevent owned-context teardown.
        finally:
            if self.context is not None:
                try:
                    self.context.close()
                except Exception:
                    pass  # Browser teardown may have already closed this context.
