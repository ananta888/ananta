"""Private real-browser unresolved-Promise probe, without any Meet connection."""

import json
import os
import time

from worker.meet_media.publication_session import PublicationSession


class ExpiringProbeLease:
    def __init__(self):
        self.expires = time.monotonic() + 5

    def require(self):
        if time.monotonic() >= self.expires:
            raise ValueError("synthetic_probe_lease_revoked")


def probe():
    from playwright.sync_api import sync_playwright

    if os.geteuid() == 0 or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        raise ValueError("meet_publication_probe_requires_private_workspace")
    started = time.monotonic()
    url = "https://publication.invalid/machine"
    observations = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            for operation in ("join", "publish", "leave"):
                context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
                try:
                    # Fully fulfilled in-process: no DNS, external Meet, trust,
                    # room, grant, device or human interaction is involved.
                    context.route("**/*", lambda route: route.fulfill(status=200, body="<html></html>"))
                    page = context.new_page()
                    page.set_default_timeout(3000)
                    page.goto(url)
                    page.evaluate("""() => {
                      window.probeCalls = 0;
                      const pending = () => { ++window.probeCalls; return new Promise(() => {}); };
                      window.anantaMachine = { join: pending, publish: pending, leave: pending };
                    }""")
                    lease = ExpiringProbeLease()
                    session = PublicationSession(page, lease, url=url, deadline=time.time() + 10)
                    session.ready()
                    lease.expires = time.monotonic() + 0.4
                    called = time.monotonic()
                    try:
                        session.call(operation, [])
                    except ValueError as exc:
                        if str(exc) != "synthetic_probe_lease_revoked":
                            raise
                    else:
                        raise ValueError("meet_publication_probe_did_not_stop")
                    elapsed = round((time.monotonic() - called) * 1000)
                    if elapsed > 1500 or page.evaluate("window.probeCalls") != 1:
                        raise ValueError("meet_publication_probe_budget_or_duplicate")
                    observations[operation] = elapsed
                finally:
                    context.close()
        finally:
            browser.close()
    return {
        "status": "passed",
        "classification": "synthetic_local_technical_observation",
        "pending_operation_stop_ms": observations,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "human_capture_used": False,
        "meet_delivery_verified": False,
        "production_release_evidence": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(probe(), sort_keys=True))
    except Exception:
        raise SystemExit("meet_publication_probe_failed") from None
