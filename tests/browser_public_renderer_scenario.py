"""Continuous trusted-view pixels; source markup and markers never become images."""

import base64
import hashlib
import io
import json
import time

from PIL import Image
from playwright.sync_api import sync_playwright

from ananta_contracts.browser_view_generation import BrowserViewGeneration
from worker.meet_media.browser_public_renderer import SanitizedDocumentView
from worker.meet_media.browser_public_snapshot import PublicDocumentSnapshot


def run():
    frames, sizes, hashes = 0, [], set()
    generation = BrowserViewGeneration("synthetic-workspace", "synthetic-page", 1)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            source = browser.new_context(
                java_script_enabled=False,
                permissions=[],
                accept_downloads=False,
                service_workers="block",
                viewport={"width": 640, "height": 360},
            )
            source.route("**/*", lambda route: route.abort())
            page = source.new_page()
            page.set_content(
                '<body style="background:#ff00ff"><h1>Public document</h1>'
                "<p>&lt;script&gt;literal text&lt;/script&gt;</p></body>"
            )
            snapshot = PublicDocumentSnapshot(page)
            view = SanitizedDocumentView(browser, generation, deadline=time.monotonic() + 20)
            try:
                view.render(snapshot.read(), generation)
                assert view.context is not source and len(browser.contexts) == 2
                assert view.page.evaluate("document.getElementById('view').textContent") == (
                    "Public document<script>literal text</script>"
                )
                until = time.monotonic() + 1.5
                while time.monotonic() < until:
                    view.page.wait_for_timeout(40)
                    frame = view.take(generation)
                    if frame is None:
                        continue
                    raw = base64.b64decode(frame, validate=True)
                    with Image.open(io.BytesIO(raw)) as image:
                        assert image.size == (640, 360)
                        pixels = image.convert("RGB").getdata()
                        assert not any(red > 180 and blue > 180 and green < 80 for red, green, blue in pixels)
                    frames += 1
                    sizes.append(len(raw))
                    hashes.add(hashlib.sha256(raw).hexdigest())
                assert frames >= 4 and len(hashes) >= 3
                page.set_content('<input value="SYNTHETIC_PRIVATE_MARKER">')
                try:
                    view.render(snapshot.read(), generation)
                    raise AssertionError("blocked snapshot must close the stage")
                except ValueError as error:
                    assert str(error) == "browser_view_content_blocked"
                assert view.closed and view.pending is None
                assert page.evaluate("document.querySelectorAll('input').length") == 1
            finally:
                view.close()
            for mode in ("resize", "page_closed", "new_epoch"):
                view = SanitizedDocumentView(browser, generation, deadline=time.monotonic() + 10)
                try:
                    page.set_content("<p>Public document</p>")
                    view.render(snapshot.read(), generation)
                    current = generation
                    if mode == "resize":
                        view.page.set_viewport_size({"width": 640, "height": 400})
                    elif mode == "page_closed":
                        view.page.close()
                    else:
                        current = BrowserViewGeneration(generation.workspace_id, generation.page_id, 2)
                    try:
                        view.take(current)
                        raise AssertionError("invalid generation must not release a frame")
                    except ValueError as error:
                        assert str(error) == "browser_view_generation_inactive"
                    assert view.closed and view.pending is None
                finally:
                    view.close()
            source.close()
        finally:
            browser.close()
    return {
        "frames": frames,
        "distinct_frames": len(hashes),
        "maximum_bytes": max(sizes),
        "sandbox": True,
        "source_pixels_published": False,
        "synthetic_markers": True,
        "network": False,
        "production_evidence": False,
        "bounded_stop_cases": 4,
    }


if __name__ == "__main__":
    print(json.dumps(run()))
