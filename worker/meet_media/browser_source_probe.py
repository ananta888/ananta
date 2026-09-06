"""Bounded local CDP capability/cost probe, never a production media source."""

import base64
import io
import json
import os
import resource
import time


class ProbeFrames:
    """Keep counters/colors only, not screenshots or an accumulating frame queue."""

    def __init__(self):
        self.count = 0
        self.bytes = 0
        self.colors = set()

    def observe(self, data):
        from PIL import Image

        if not isinstance(data, str) or len(data) > 700_000:
            raise ValueError("browser_probe_frame_too_large")
        content = base64.b64decode(data, validate=True)
        with Image.open(io.BytesIO(content)) as frame:
            if frame.format != "JPEG" or frame.size != (640, 360):
                raise ValueError("browser_probe_frame_format_invalid")
            red, green, blue = frame.convert("RGB").getpixel((320, 180))
        self.count += 1
        self.bytes += len(content)
        if red > 200 and green < 40 and blue < 40:
            self.colors.add("red")
        if green > 200 and red < 40 and blue < 40:
            self.colors.add("green")


def probe():
    from playwright.sync_api import sync_playwright

    if os.geteuid() == 0 or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        raise ValueError("browser_probe_requires_nonroot_headless_workspace")
    started = time.monotonic()
    frames = ProbeFrames()
    requests = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            browser_version = browser.version
            context = browser.new_context(
                permissions=[],
                accept_downloads=False,
                service_workers="block",
                viewport={"width": 640, "height": 360},
            )

            def deny_network(route):
                nonlocal requests
                requests += 1
                route.abort()

            context.route("**/*", deny_network)
            page = context.new_page()
            page.set_default_timeout(3000)
            page.set_content('<html><body style="margin:0;background:rgb(255,0,0)"></body></html>')
            # Trusted probe-owned DOM only: no URLs, login, external resources,
            # canvas/video/cross-origin input or human device grants.
            page.evaluate("""() => {
              let tick = 0;
              window.probeTimer = setInterval(() => {
                document.body.style.backgroundColor = (++tick % 2) ? 'rgb(0,255,0)' : 'rgb(255,0,0)';
              }, 120);
            }""")
            cdp = context.new_cdp_session(page)
            command = cdp.send("Browser.getBrowserCommandLine")["arguments"]
            if "--no-sandbox" in command or "--disable-setuid-sandbox" in command:
                raise ValueError("browser_probe_sandbox_disabled")

            def receive(event):
                # Always release CDP backpressure; no retained pixel buffer.
                try:
                    frames.observe(event["data"])
                finally:
                    cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

            cdp.on("Page.screencastFrame", receive)
            capture_started = time.monotonic()
            cdp.send(
                "Page.startScreencast",
                {"format": "jpeg", "quality": 70, "maxWidth": 640, "maxHeight": 360, "everyNthFrame": 1},
            )
            try:
                page.wait_for_timeout(2000)
            finally:
                cdp.send("Page.stopScreencast")
                page.evaluate("clearInterval(window.probeTimer)")
                cdp.detach()
            capture_ms = round((time.monotonic() - capture_started) * 1000)
            if len(context.pages) != 1 or page.url != "about:blank" or requests:
                raise ValueError("browser_probe_workspace_changed")
        finally:
            browser.close()
    if frames.count < 5 or frames.colors != {"red", "green"}:
        raise ValueError("browser_probe_moving_frames_missing")
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "status": "passed",
        "classification": "synthetic_local_technical_observation",
        "browser": "chromium",
        "browser_version": browser_version,
        "sandbox": True,
        "source": "owned_page_cdp_screencast",
        "viewport": [640, 360],
        "frames_decoded": frames.count,
        "observed_colors": sorted(frames.colors),
        "encoded_bytes": frames.bytes,
        "capture_ms": capture_ms,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "child_cpu_ms": round((usage.ru_utime + usage.ru_stime) * 1000),
        "peak_child_rss_kib": usage.ru_maxrss,
        "page_network_requests": requests,
        "human_capture_used": False,
        "meet_delivery_verified": False,
        "production_release_evidence": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(probe(), sort_keys=True))
    except Exception:
        raise SystemExit("meet_owned_browser_probe_failed") from None
