"""Offline, task-owned CDP view. This adapter cannot attach to arbitrary tabs."""

import time


class OwnedDialogScreen:
    def __init__(self, browser, hub_session_id):
        self.source_id = "screen:" + hub_session_id
        self.context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block",
                                           viewport={"width": 640, "height": 360})
        self.pending = None; self.closed = False; self.revoked = False
        self.cdp = None
        try:
            self._start()
        except Exception:
            self.close()
            raise

    def _start(self):
        self.context.route("**/*", self._deny)
        self.page = self.context.new_page()
        self.page.set_default_timeout(1000)
        self.context.on("page", lambda _page: self._revoke())
        self.page.on("download", lambda _download: self._revoke())
        self.page.set_content("""<!doctype html><html><head>
          <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'">
          <style>body{margin:0;background:#101828;color:white;font:24px sans-serif;padding:30px}
          #pulse{height:100px;width:100px;background:#00bd85;border-radius:20px;position:relative}</style></head>
          <body><h1>Ananta · KI-Arbeitsansicht</h1><p>Isolierter Hub-Auftrag aktiv</p><div id="pulse"></div>
          <p id="tick">0</p><script>let n=0;setInterval(()=>{n++;document.getElementById('tick').textContent='Laufzeit: '+(n/5).toFixed(1)+' s';
          document.getElementById('pulse').style.left=(n*9%470)+'px'},200)</script></body></html>""")
        self.page.on("framenavigated", lambda _frame: self._revoke())
        self.cdp = self.context.new_cdp_session(self.page)
        self.cdp.on("Page.screencastFrame", self._receive)
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 70, "maxWidth": 640, "maxHeight": 360, "everyNthFrame": 1})

    def _deny(self, route):
        self._revoke(); route.abort()

    def _revoke(self):
        self.revoked = True; self.pending = None

    def _receive(self, event):
        try:
            data = event.get("data")
            if self.closed or self.revoked:
                return
            if not isinstance(data, str) or not 0 < len(data) <= 350_000:
                self._revoke(); return
            # Latest frame wins: never accumulate browser content or a screenshot history.
            self.pending = (time.monotonic(), data)
        finally:
            if not self.closed:
                self.cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    def take(self):
        if self.closed or self.revoked or self.page.url != "about:blank" or len(self.context.pages) != 1:
            self.close(); raise ValueError("meet_screen_workspace_revoked")
        # Unknown/interactive embedded content is never masked heuristically then shared.
        if self.page.evaluate("Boolean(document.querySelector('input,textarea,form,iframe,canvas,video,audio,img,object,embed'))"):
            self.close(); raise ValueError("meet_screen_content_denied")
        pending, self.pending = self.pending, None
        return pending[1] if pending and time.monotonic() - pending[0] <= 1 else None

    def close(self):
        if self.closed:
            return
        self.closed = True; self.pending = None
        try:
            if self.cdp is not None:
                self.cdp.send("Page.stopScreencast")
        except Exception:
            pass  # A dead CDP target must not prevent its workspace teardown.
        finally:
            try:
                self.context.close()
            except Exception:
                pass  # Browser/context shutdown may already have completed.
