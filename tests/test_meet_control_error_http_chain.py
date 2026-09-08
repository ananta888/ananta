"""Real Worker -> Hub callback -> upstream HTTP errors, with synthetic scope only."""

import threading
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest
from flask import Flask
from werkzeug.serving import WSGIRequestHandler, make_server

from agent.routes.meet import meet_bp
from agent.services.meet_authorization_client import MeetAuthorizationClient
from tests.test_meet_dialog_transport import assignment
from tests.test_meet_session_observation import observation_fixture
from worker.meet_media.dialog_client import HubDialogClient
from worker.meet_media.dialog_control_transport import ControlReadUnavailable


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive(), "owned HTTP fixture failed to stop"


@pytest.mark.timeout(30)
@pytest.mark.parametrize("status", [200, 401, 403, 409, 429, 500, 502, 503, 504])
def test_actual_error_chain_preserves_terminal_vs_transport_semantics(tmp_path, monkeypatch, status):
    upstream_reads = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            assert 0 < size < 4096
            self.rfile.read(size)
            upstream_reads.append(self.path)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")  # Invalid successful schema, never a forged membership.

        def log_message(self, *_args):
            pass

    class Quiet(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    with ExitStack() as cleanup:
        upstream = cleanup.enter_context(serving(ThreadingHTTPServer(("127.0.0.1", 0), Upstream)))
        _, args = observation_fixture()
        scope = replace(args[0], origin=f"http://127.0.0.1:{upstream.server_port}")
        authority, issuer = Mock(), Mock(issuer=args[1])
        authority.current.return_value = scope
        issuer.issue_dialog.return_value = {"grant": "synthetic-only"}
        meet = MeetAuthorizationClient(authority, issuer, clock=lambda: args[4] / 1000)
        service = Mock()
        service.exchange.side_effect = lambda payload: meet.inspect(
            payload["task_id"], payload["lease_id"], payload["runtime_id"], payload["meet_session_id"]
        )
        app = Flask(__name__)
        app.config.update(TESTING=True, ROLE="hub")
        app.register_blueprint(meet_bp)
        key = b"synthetic-error-chain-key-material"
        app.extensions.update(meet_binding_service=Mock(), meet_dialog_service=service, meet_media_worker_key=key)
        hub = cleanup.enter_context(serving(make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)))
        key_file = tmp_path / "worker.key"
        key_file.write_bytes(key)
        key_file.chmod(0o600)
        monkeypatch.setenv("MEET_WORKER_KEY_FILE", str(key_file))
        monkeypatch.setenv("MEET_HUB_DIALOG_URL", f"http://127.0.0.1:{hub.server_port}/api/meet/v1/internal/dialog")
        worker = HubDialogClient(assignment())
        with pytest.raises(ValueError) as error:
            worker.call("exchange", meet_session_id=args[2])
        assert type(error.value) is (ControlReadUnavailable if status in {502, 503, 504} else ValueError)
        assert upstream_reads == ["/api/machine/sessions/authorization"]
        assert service.exchange.call_count == 1
