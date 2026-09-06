"""One delegated publication in an ephemeral browser; no orchestration loop."""

import base64
import time

from worker.meet_media.av_quality import MAX_VIDEO_BYTES
from worker.meet_media.publication_session import PublicationSession


def publish(meeting, text, video_path, deadline, lease):
    from playwright.sync_api import sync_playwright

    lease.require()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            chromium_sandbox=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        try:
            context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
            # No persisted human profile, certificates bypass, file input or device grant.
            context.add_init_script("""for (const name of ['getUserMedia', 'getDisplayMedia']) {
              navigator.mediaDevices[name] = () => Promise.reject(new Error('human_capture_forbidden'));
            }""")
            page = context.new_page()
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
            session.call("join", [meeting["room_id"], meeting["grant"]])
            session.call("publish", [text, base64.b64encode(video).decode()])
            session.call("leave", [])
        finally:
            browser.close()
    return {"status": "published", "room_id": meeting["room_id"], "delivery_verified": False}
