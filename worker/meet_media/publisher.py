"""One delegated publication in an ephemeral browser; no orchestration loop."""

import base64
import time


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
            page.set_default_timeout(min(20_000, max(1, int((deadline - time.time()) * 1000))))
            page.goto(meeting["origin"] + "/machine", wait_until="domcontentloaded")
            if page.url != meeting["origin"] + "/machine":
                raise ValueError("meet_machine_navigation_denied")
            page.wait_for_function("Boolean(window.anantaMachine)")
            page.evaluate(
                "([room, grant]) => window.anantaMachine.join(room, grant)", [meeting["room_id"], meeting["grant"]]
            )
            lease.require()
            page.evaluate(
                """([text, video]) => {
              window.publicationOutcome = 'pending';
              void window.anantaMachine.publish(text, video).then(
                () => { window.publicationOutcome = 'done'; },
                () => { window.publicationOutcome = 'failed'; });
            }""",
                [text, base64.b64encode(video_path.read_bytes()).decode()],
            )
            while page.evaluate("window.publicationOutcome") == "pending":
                lease.require()
                if time.time() >= deadline:
                    raise ValueError("meet_publication_expired")
                page.wait_for_timeout(500)
            if page.evaluate("window.publicationOutcome") != "done":
                raise ValueError("meet_publication_failed")
            page.evaluate("window.anantaMachine.leave()")
        finally:
            browser.close()
    return {"status": "published", "room_id": meeting["room_id"], "delivery_verified": False}
