"""Actual Worker HTTP through Flask/SQL plus owned blocking-I/O deadline probes."""

import json
import subprocess
import sys
import threading
import time

import pytest
from werkzeug.serving import make_server

from tests.test_meet_dialog_diagnostics_contract import observation
from tests.test_meet_dialog_diagnostics_hub import setup
from tests.test_meet_dialog_diagnostics_routes import diagnostic_http  # noqa: F401
from tests.test_meet_dialog_phase_routes import http  # noqa: F401
from worker.meet_media.dialog_diagnostics_client import report_terminal

pytestmark = pytest.mark.timeout(20)


def test_actual_worker_http_records_terminal_sql_observation_and_owner_reads_it(app, diagnostic_http):  # noqa: F811
    with app.app_context():
        f = setup()
        before = f.tasks.get_by_id(f.task_id).model_dump()
        client, web, _, _ = diagnostic_http
        web.extensions["meet_dialog_diagnostics"] = f.diagnostics
        server = make_server("127.0.0.1", 0, web)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
        thread.start()
        try:
            identifiers = {key: f.payload[key] for key in ("task_id", "lease_id", "runtime_id")}
            assert report_terminal(
                f"http://127.0.0.1:{server.server_port}/api/meet/v1/internal/dialog",
                web.extensions["meet_media_worker_key"], identifiers, observation(), time.monotonic() + 60,
            )
            result = client.get(f"/api/meet/v1/projects/project/dialogs/{f.task_id}/diagnostics",
                                headers={"Authorization": "Bearer synthetic-user"})
            assert result.status_code == 200
            assert result.json["observation"] == observation()
            assert result.json["classification"] == "unverified_worker_observation"
            assert f.tasks.get_by_id(f.task_id).model_dump() == before
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
            assert not thread.is_alive()


@pytest.mark.parametrize("scenario", ["dns", "body", "redirect"])
def test_real_child_bounds_dns_and_slow_http_and_never_follows_redirect(scenario):
    code = """
import json, socket, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from worker.meet_media.dialog_diagnostics_deadline import bounded_terminal_report
from worker.meet_media.dialog_diagnostics_client import report_terminal
scenario = sys.argv[1]
paths = []
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_POST(self):
        paths.append(self.path)
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(302 if scenario == 'redirect' else 200)
        if scenario == 'redirect': self.send_header('Location', '/foreign')
        self.send_header('Content-Length', '100')
        self.end_headers()
        if scenario == 'body':
            self.wfile.write(b'x'); self.wfile.flush()
            time.sleep(5)
    def do_GET(self):
        paths.append(self.path)
        self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers()
server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
thread.start()
if scenario == 'dns':
    socket.getaddrinfo = lambda *_a, **_kw: time.sleep(5)
deadline = time.monotonic() + 60
started = time.monotonic()
try:
    result = bounded_terminal_report(lambda: report_terminal(
        'http://127.0.0.1:%d/api/meet/v1/internal/dialog' % server.server_port,
        b'synthetic', {'task_id': 'task', 'lease_id': 'lease', 'runtime_id': 'runtime'},
        {'schema': 'ananta.meet-dialog-terminal-observation.v1', 'stop_reason': 'runtime_failed', 'measurements': None},
        deadline), deadline)
    elapsed = time.monotonic() - started
    assert result is False and elapsed < 1.7
    assert paths == ([] if scenario == 'dns' else ['/api/meet/v1/internal/dialog/diagnostics'])
    print(json.dumps({'elapsed': elapsed}))
finally:
    server.shutdown(); thread.join(timeout=1); server.server_close()
"""
    child = subprocess.run([sys.executable, "-c", code, scenario], capture_output=True, timeout=4, check=True)
    elapsed = json.loads(child.stdout)["elapsed"]
    assert elapsed < 1.7
    if scenario != "redirect":
        assert elapsed > 0.8
