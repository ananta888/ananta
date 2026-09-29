"""Actual offline Chromium DOM, synthetic markers only; no screenshots saved."""

import json

from playwright.sync_api import sync_playwright

from worker.meet_media.browser_public_snapshot import PublicDocumentSnapshot


def run():
    checks = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(
                java_script_enabled=False,
                accept_downloads=False,
                service_workers="block",
                permissions=[],
                viewport={"width": 640, "height": 360},
            )
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(2000)
            reader = PublicDocumentSnapshot(page)

            def read(html):
                page.set_content(html)
                return reader.read()

            result = read("<h1>Öffentliche Seite</h1><p>Begrenzte <b>Ansicht</b></p>")
            assert result["state"] == "ready"
            assert [b["text"] for b in result["blocks"]] == ["Öffentliche Seite", "Begrenzte", "Ansicht"]
            checks += 1
            for html in (
                '<input value="SYNTHETIC_PRIVATE_MARKER">',
                '<input type="hidden" value="SYNTHETIC_PRIVATE_MARKER">',
                "<textarea>SYNTHETIC_PRIVATE_MARKER</textarea>",
                "<form><p>SYNTHETIC_PRIVATE_MARKER</p></form>",
                "<div data-confidential>SYNTHETIC_PRIVATE_MARKER</div>",
                "<div contenteditable>SYNTHETIC_PRIVATE_MARKER</div>",
                '<div class="login-panel">SYNTHETIC_PRIVATE_MARKER</div>',
                '<div aria-label="private">marker</div>',
            ):
                result = read("<p>Public prefix must not leak on denial</p>" + html)
                assert (
                    result["state"] == "blocked" and result["reason"] == "sensitive_content" and result["blocks"] == []
                )
                checks += 1
            for tag in ("canvas", "video", "audio", "img", "iframe", "svg", "object", "embed", "math", "custom-panel"):
                result = read("<p>Public prefix</p><" + tag + ">SYNTHETIC_PRIVATE_MARKER</" + tag + ">")
                assert (
                    result["state"] == "blocked" and result["reason"] == "active_content" and result["blocks"] == []
                ), (tag, result["state"], result["reason"])
                checks += 1
            for hidden in (
                "<span hidden>SYNTHETIC_PRIVATE_MARKER</span>",
                '<div aria-hidden="true"><span>SYNTHETIC_PRIVATE_MARKER</span></div>',
                '<div style="opacity:0"><span>SYNTHETIC_PRIVATE_MARKER</span></div>',
                '<div style="display:none"><span>SYNTHETIC_PRIVATE_MARKER</span></div>',
                '<div style="visibility:hidden"><span>SYNTHETIC_PRIVATE_MARKER</span></div>',
                '<p style="position:absolute;top:9999px">SYNTHETIC_PRIVATE_MARKER</p>',
                '<script>document.body.textContent="SYNTHETIC_PRIVATE_MARKER"</script>',
                '<style>body:after{content:"SYNTHETIC_PRIVATE_MARKER"}</style>',
            ):
                result = read("<p>Public text</p>" + hidden)
                assert result["state"] == "ready" and result["blocks"] == [{"kind": "text", "text": "Public text"}]
                checks += 1
            for excessive in ("<div>" * 50 + "x" + "</div>" * 50, "<span></span>" * 4097, "<p>" + "x" * 501 + "</p>"):
                result = read(excessive)
                assert (
                    result["state"] == "blocked" and result["reason"] == "snapshot_too_large" and not result["blocks"]
                )
                checks += 1
            old = read("<p>Old public text</p>")
            assert page.evaluate("document.body.textContent") == "Old public text"
            # Native automation can inspect a script-disabled page. Source-page
            # script execution is disabled; no user action or security bypass.
            page.set_content('<input value="SYNTHETIC_PRIVATE_MARKER">')
            assert reader.read()["state"] == "blocked"
            assert old["blocks"] == [{"kind": "text", "text": "Old public text"}]
            checks += 1
            assert read("<p hidden>hidden</p>")["reason"] == "no_visible_text"
            checks += 1
            page.set_content("<p>Public text</p>")
            cdp = context.new_cdp_session(page)
            cdp.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 700,
                    "height": 400,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            assert page.viewport_size == {"width": 640, "height": 360}
            assert reader.read()["reason"] == "source_unavailable"
            checks += 1  # Real DOM shape remains fenced even if cached metadata is unchanged.
            context.close()
        finally:
            browser.close()
    return {
        "checks": checks,
        "sandbox": True,
        "page_scripts": False,
        "network": False,
        "synthetic_markers": True,
        "production_evidence": False,
    }


if __name__ == "__main__":
    print(json.dumps(run()))
