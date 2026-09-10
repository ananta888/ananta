"""Real loopback HTTP checks: Hub authority never follows a redirect."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from worker.runtime.workflow_hub_gateway import HttpWorkflowHubDecisionClient, WorkflowHubDecisionError

SYNTHETIC_TOKEN = "synthetic-test-only-hub-token-value"


@contextmanager
def server(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.timeout = 1
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_hub_redirect_never_contacts_target_or_accepts_its_decision(status):
    contacts, initial = [], []

    class Target(QuietHandler):
        def do_GET(self):
            contacts.append({"method": self.command, "has_authorization": bool(self.headers.get("Authorization"))})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"allowed":true}')

        do_POST = do_GET

    with server(Target) as target:

        class Redirect(QuietHandler):
            def do_POST(self):
                initial.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(status)
                self.send_header("Location", target + "/foreign-decision")
                self.end_headers()

        with server(Redirect) as origin:
            client = HttpWorkflowHubDecisionClient(hub_url=origin, bearer_token=SYNTHETIC_TOKEN, timeout_seconds=2)
            try:
                result = client.command("authorize_execution", binding={"synthetic": True})
            except WorkflowHubDecisionError as exc:
                reason, retryable = exc.reason_code, exc.retryable
            else:
                reason, retryable = "unexpected_decision", None
                assert result.get("allowed") is True
    assert contacts == [], "A redirected endpoint received a request (including possible Hub authorization)"
    assert len(initial) == 1 and reason == "workflow_hub_redirect_denied" and retryable is False


def test_hub_direct_post_keeps_body_credentials_and_decision():
    seen = []

    class Hub(QuietHandler):
        def do_POST(self):
            seen.append(
                (
                    self.path,
                    self.headers.get("Authorization"),
                    json.loads(
                        self.rfile.read(int(self.headers["Content-Length"])),
                    ),
                )
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":{"allowed":true,"reason_code":"synthetic-policy"}}')

    with server(Hub) as origin:
        client = HttpWorkflowHubDecisionClient(hub_url=origin, bearer_token=SYNTHETIC_TOKEN, timeout_seconds=2)
        assert client.command("authorize_execution", binding={"synthetic": True}) == {
            "allowed": True,
            "reason_code": "synthetic-policy",
        }
    assert len(seen) == 1 and seen[0][0] == "/api/internal/workflow-runtime/worker-commands"
    assert seen[0][1] == "Bearer " + SYNTHETIC_TOKEN
    assert seen[0][2]["binding"] == {"synthetic": True}
