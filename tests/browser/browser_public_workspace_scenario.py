"""Actual script-disabled workspace, safe continuous pixels and local revocation."""

import base64
import hashlib
import io
import json
import time

from PIL import Image
from playwright.sync_api import sync_playwright

from ananta_contracts.browser_view_generation import BrowserViewGeneration
from worker.meet_media.browser_public_workspace import PublicDocumentWorkspace


def run():
    frames, hashes, checks = 0, set(), 0
    generation = BrowserViewGeneration("synthetic-workspace", "synthetic-page", 1)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            current = {"allowed": True}

            def require_current():
                if not current["allowed"]:
                    raise ValueError("synthetic_revocation")

            def create():
                return PublicDocumentWorkspace(
                    browser, generation, deadline=time.monotonic() + 20, require_current=require_current
                )

            workspace = create()
            try:
                workspace.load(
                    '<html><head><link rel="stylesheet" href="https://denied.example/style.css"></head>'
                    '<body style="background:#ff00ff"><h1>Public workspace</h1><p>Only sanitized text</p>'
                    '<script>window.pageScriptExecuted=true;document.body.innerHTML="<input value=secret>"</script>'
                    "</body></html>",
                    generation,
                )
                assert workspace.page.evaluate("typeof window.pageScriptExecuted") == "undefined"
                assert workspace.page.url == "about:blank" and len(browser.contexts) == 2
                assert workspace.view.page.evaluate("document.getElementById('view').textContent") == (
                    "Public workspaceOnly sanitized text"
                )
                until = time.monotonic() + 1.2
                while time.monotonic() < until:
                    workspace.page.wait_for_timeout(40)
                    frame = workspace.take(generation)
                    if frame is not None:
                        raw = base64.b64decode(frame, validate=True)
                        with Image.open(io.BytesIO(raw)) as image:
                            assert image.size == (640, 360)
                            assert not any(r > 180 and b > 180 and g < 80 for r, g, b in image.convert("RGB").getdata())
                        frames += 1
                        hashes.add(hashlib.sha256(raw).hexdigest())
                assert frames >= 3 and len(hashes) >= 3
                workspace.page.set_content('<input value="SYNTHETIC_PRIVATE_MARKER">')
                try:
                    workspace.take(generation)
                    raise AssertionError("secret source must stop before pending frame")
                except ValueError:
                    pass
                assert workspace.closed and workspace.view.pending is None and not browser.contexts
                checks += 1
            finally:
                workspace.close()

            for mode in ("resize", "page_closed", "generation", "revocation", "extra_page", "navigation"):
                current["allowed"] = True
                workspace = create()
                try:
                    workspace.load("<p>Public document</p>", generation)
                    active = generation
                    if mode == "resize":
                        workspace.page.set_viewport_size({"width": 700, "height": 360})
                    elif mode == "page_closed":
                        workspace.page.close()
                    elif mode == "generation":
                        active = BrowserViewGeneration(generation.workspace_id, generation.page_id, 2)
                    elif mode == "revocation":
                        current["allowed"] = False
                    elif mode == "extra_page":
                        try:
                            workspace.context.new_page()
                        except Exception:
                            pass  # Creation event closes the entire owned context.
                    else:
                        try:
                            workspace.page.goto("data:text/html,<p>unassigned</p>")
                        except Exception:
                            pass  # No network; navigation event revokes this page.
                    try:
                        workspace.take(active)
                        raise AssertionError("changed source must not publish")
                    except ValueError:
                        pass
                    assert workspace.closed and workspace.view.pending is None and not browser.contexts, (
                        mode,
                        workspace.closed,
                        workspace.view.pending is None,
                        len(browser.contexts),
                    )
                    checks += 1
                finally:
                    workspace.close()
        finally:
            browser.close()
    return {
        "frames": frames,
        "distinct_frames": len(hashes),
        "bounded_stop_cases": checks,
        "sandbox": True,
        "page_scripts": False,
        "source_pixels_published": False,
        "network": False,
        "synthetic_markers": True,
        "production_evidence": False,
    }


if __name__ == "__main__":
    print(json.dumps(run()))
