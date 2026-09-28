"""One delegated publication in an ephemeral browser; no orchestration loop."""

import base64
import time

from worker.meet_media.av_quality import MAX_VIDEO_BYTES
from worker.meet_media.browser_network import restrict_meet_browser_network
from worker.meet_media.publication_session import PublicationSession
from worker.meet_media.publisher_diagnostics import PublisherDiagnostics, enabled as diagnostics_enabled

# No persisted human profile, certificates bypass, file input or device grant.
CAPTURE_FORBIDDEN_SCRIPT = """for (const name of ['getUserMedia', 'getDisplayMedia']) {
  navigator.mediaDevices[name] = () => Promise.reject(new Error('human_capture_forbidden'));
}"""
# The E2EE handshake checks timestamps against the room server; a skewed container clock
# fails it. Date.now follows the server's Date header (initially +1 s, then server + 3 s).
SERVER_CLOCK_SCRIPT = """(() => {
  const originalNow = Date.now.bind(Date);
  window.__clockOffset = 1000;
  Date.now = () => originalNow() + window.__clockOffset;
  const orig = window.fetch;
  window.fetch = async (input, init) => {
    const response = await orig(input, init);
    try {
      const serverDate = Date.parse(response.headers.get('date') || '');
      if (Number.isFinite(serverDate)) window.__clockOffset = serverDate - originalNow() + 3000;
    } catch (error) {}
    return response;
  };
})();"""
PUBLISH_ATTEMPTS = 2  # membership changes on join can race the E2EE overlay once


def publish(meeting, text, video_path, deadline, lease):
    from playwright.sync_api import sync_playwright

    diagnostics = PublisherDiagnostics(diagnostics_enabled())
    lease.require()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            chromium_sandbox=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        try:
            context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
            restrict_meet_browser_network(context, meeting["origin"])
            context.add_init_script(CAPTURE_FORBIDDEN_SCRIPT)
            context.add_init_script(SERVER_CLOCK_SCRIPT)
            diagnostics.install_context(context)
            page = context.new_page()
            diagnostics.install_page(page)
            page.set_default_timeout(min(5000, max(1, int((deadline - time.time()) * 1000))))
            url = meeting["origin"] + "/machine"
            session = PublicationSession(page, lease, url=url, deadline=deadline)
            lease.require()
            page.goto(url, wait_until="domcontentloaded")
            session.ready()
            with video_path.open("rb") as source:
                video = source.read(MAX_VIDEO_BYTES + 1)
            if not 16 <= len(video) <= MAX_VIDEO_BYTES or video[4:8] != b"ftyp":
                raise ValueError("meet_publication_media_invalid")
            diagnostics.hook_errors(page)
            try:
                session.call("join", [meeting["room_id"], meeting["grant"]])
            except Exception:
                diagnostics.note_failure(page, "join")
                raise
            _publish_after_join(session, page, text, video, diagnostics)
            session.call("leave", [])
        finally:
            browser.close()
    return {"status": "published", "room_id": meeting["room_id"], "delivery_verified": False}


def _publish_after_join(session, page, text, video, diagnostics):
    """Let the membership/route epoch settle, then publish; one bounded retry for the overlay race."""
    last_error = None
    for attempt in range(PUBLISH_ATTEMPTS):
        page.wait_for_timeout(8000 if attempt == 0 else 5000)
        try:
            session.call("publish", [text, base64.b64encode(video).decode()])
            return
        except Exception as error:  # noqa: BLE001 - retried once, then re-raised
            last_error = error
            diagnostics.note_failure(page, "publish", attempt)
    diagnostics.note_failure(page, "crypto")
    raise last_error
