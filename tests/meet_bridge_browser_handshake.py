"""Optional private peer-browser provisioning, separate from dialog/test assertions."""

import re

from tests.meet_dialog_browser_fixture import DialogBrowserFixture


class BridgeBrowserHandshake:
    def __init__(self, lifetime, *, factory=DialogBrowserFixture):
        self.lifetime, self.factory = lifetime, factory
        self.browser = None

    def start(self, request):
        if (
            self.browser is not None
            or not isinstance(request, dict)
            or set(request) != {"schema", "test_network", "certificate", "spki"}
            or request["schema"] != "ananta.meet-test-browser-request.v1"
            or not isinstance(request["certificate"], str)
            or not 1 <= len(request["certificate"]) <= 512
            or not isinstance(request["spki"], str)
            or not re.fullmatch(r"[A-Za-z0-9+/]{43}=", request["spki"])
        ):
            raise ValueError("test_bridge_browser_request_invalid")
        self.browser = self.factory(request["test_network"], self.lifetime)
        # Keep ownership before start: partial Docker setup must still be reaped.
        self.browser.start(request["spki"], certificate=request["certificate"])
        return {"schema": "ananta.meet-test-browser-response.v1", "endpoint": self.browser.endpoint}

    @property
    def process_id(self):
        return None if self.browser is None else self.browser.process_id

    def close(self):
        if self.browser is not None:
            self.browser.close()
